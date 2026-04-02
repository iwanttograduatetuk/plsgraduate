"""
LSTM Autoencoder 추론 엔진
─────────────────────────────────────────────────────
- 3개 서브시스템 모델(coolant / hydraulics / probe) 로드
- 재학습/재배포 대비: ModelRegistry 패턴으로 모델을 동적 교체 가능
- hot_reload()를 호출하면 디스크에서 최신 모델을 다시 읽음
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

SUBSYSTEMS = ("coolant", "hydraulics", "probe")


# ── 모델 아키텍처 ──────────────────────────────────────────────────────────────

class LSTMEncoder(nn.Module):
    def __init__(self, n_feat: int, hidden: int, latent: int, n_layers: int):
        super().__init__()
        self.lstm = nn.LSTM(
            n_feat, hidden, n_layers, batch_first=True,
            dropout=0.1 if n_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden, latent)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


class LSTMDecoder(nn.Module):
    def __init__(self, latent: int, hidden: int, n_feat: int, n_layers: int, seq_len: int):
        super().__init__()
        self.seq_len = seq_len
        self.fc = nn.Linear(latent, hidden)
        self.lstm = nn.LSTM(
            hidden, hidden, n_layers, batch_first=True,
            dropout=0.1 if n_layers > 1 else 0.0,
        )
        self.out = nn.Linear(hidden, n_feat)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        h = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        o, _ = self.lstm(h)
        return torch.sigmoid(self.out(o))


class LSTMAutoencoder(nn.Module):
    def __init__(self, n_feat: int, hidden: int, latent: int, n_layers: int, seq_len: int):
        super().__init__()
        self.encoder = LSTMEncoder(n_feat, hidden, latent, n_layers)
        self.decoder = LSTMDecoder(latent, hidden, n_feat, n_layers, seq_len)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


# ── 모델 메타데이터 컨테이너 ───────────────────────────────────────────────────

@dataclass
class ModelEntry:
    """로드된 모델과 관련 메타데이터를 보관"""
    name: str
    model: LSTMAutoencoder
    info: dict
    feat_indices: Optional[list] = None  # None이면 전체 89 피처 사용
    version: str = "v1.0.0"
    loaded_at: str = field(default_factory=lambda: _now_iso())

    @property
    def threshold_3sigma(self) -> float:
        return self.info["threshold_3sigma"]

    @property
    def window_size(self) -> int:
        return self.info["window_size"]

    @property
    def n_features(self) -> int:
        return self.info["n_features"]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── 모델 레지스트리 ────────────────────────────────────────────────────────────

class ModelRegistry:
    """
    ModelRegistry — 모델 로드 및 교체 관리
    ─────────────────────────────────────────
    재학습/재배포 시 hot_reload(name)를 호출하면
    디스크에서 새 가중치를 읽어 교체함 (서비스 무중단).
    """

    def __init__(self, model_dir: Path, meta_info: dict, device: torch.device):
        self._lock = threading.RLock()
        self._entries: Dict[str, ModelEntry] = {}
        self._model_dir = model_dir
        self._meta_info = meta_info  # meta.json 의 subsystem_info
        self._device = device

    # ── 로드 ─────────────────────────────────────────────────────────────────

    def load_all(self, version: str = "v1.0.0") -> None:
        """모든 서브시스템 모델을 로드"""
        for name in SUBSYSTEMS:
            self._load_one(name, version)
        logger.info("모든 LSTM 모델 로드 완료: %s", list(self._entries.keys()))

    def _load_one(self, name: str, version: str) -> None:
        pt_path   = self._model_dir / f"{name}_lstm.pt"
        info_path = self._model_dir / f"{name}_lstm_info.json"

        if not pt_path.exists() or not info_path.exists():
            logger.warning("모델 파일 없음 (skip): %s", name)
            return

        info = json.loads(info_path.read_text())
        model = LSTMAutoencoder(
            n_feat=info["n_features"],
            hidden=info["hidden_size"],
            latent=info["latent_dim"],
            n_layers=info["n_layers"],
            seq_len=info["window_size"],
        ).to(self._device)
        model.load_state_dict(
            torch.load(pt_path, map_location=self._device, weights_only=True)
        )
        model.eval()

        feat_indices = None
        if name in self._meta_info:
            feat_indices = self._meta_info[name]["indices"]

        entry = ModelEntry(
            name=name,
            model=model,
            info=info,
            feat_indices=feat_indices,
            version=version,
        )
        with self._lock:
            self._entries[name] = entry
        logger.info("  [%s] n_feat=%d, 3σ=%.5f, version=%s",
                    name, info["n_features"], info["threshold_3sigma"], version)

    # ── Hot Reload (재배포 대비) ───────────────────────────────────────────────

    def hot_reload(self, name: str | None = None, version: str = "latest") -> list[str]:
        """
        새 모델 가중치를 디스크에서 다시 로드.
        name=None이면 전체 리로드.
        반환: 리로드된 모델 이름 목록
        """
        targets = [name] if name else list(SUBSYSTEMS)
        reloaded = []
        for n in targets:
            try:
                self._load_one(n, version)
                reloaded.append(n)
                logger.info("hot_reload 완료: %s (version=%s)", n, version)
            except Exception as e:
                logger.error("hot_reload 실패: %s — %s", n, e)
        return reloaded

    # ── 접근자 ───────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ModelEntry]:
        with self._lock:
            return self._entries.get(name)

    def status(self) -> dict:
        """현재 로드된 모델들의 메타 정보 반환 (헬스체크용)"""
        with self._lock:
            return {
                name: {
                    "version":      e.version,
                    "loaded_at":    e.loaded_at,
                    "n_features":   e.n_features,
                    "threshold_3sigma": e.threshold_3sigma,
                }
                for name, e in self._entries.items()
            }


# ── 추론 함수 ──────────────────────────────────────────────────────────────────

@torch.no_grad()
def infer_window(
    entry: ModelEntry,
    window: np.ndarray,  # shape: (window_size, n_all_features)
    device: torch.device,
) -> float:
    """
    단일 윈도우에 대해 재구성 오차(MSE)를 반환.
    feat_indices가 설정된 경우 해당 피처만 추출하여 추론.
    """
    arr = window
    if entry.feat_indices is not None:
        arr = arr[:, entry.feat_indices]
    arr = np.nan_to_num(arr.astype(np.float32), nan=0.0)

    t = torch.tensor(arr).unsqueeze(0).to(device)  # (1, T, F)
    x_hat = entry.model(t)
    mse = float(((t - x_hat) ** 2).mean().item())
    return mse


@torch.no_grad()
def infer_batch(
    entry: ModelEntry,
    windows: np.ndarray,  # shape: (N, window_size, n_all_features)
    device: torch.device,
    batch_size: int = 256,
) -> np.ndarray:
    """배치 추론 — 각 윈도우의 MSE 배열 반환"""
    arr = windows
    if entry.feat_indices is not None:
        arr = arr[:, :, entry.feat_indices]
    arr = np.nan_to_num(arr.astype(np.float32), nan=0.0)

    errors = []
    t_all = torch.tensor(arr)
    for s in range(0, len(t_all), batch_size):
        b = t_all[s : s + batch_size].to(device)
        x_hat = entry.model(b)
        mse = ((b - x_hat) ** 2).mean(dim=(1, 2)).cpu().numpy()
        errors.extend(mse.tolist())
    return np.array(errors, dtype=np.float32)
