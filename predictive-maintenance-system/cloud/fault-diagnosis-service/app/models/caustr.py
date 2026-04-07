"""
CausTR — Causal Transformer 기반 Root Cause Analysis
──────────────────────────────────────────────────────────────────
causRCA 논문의 CausTR 모델 구현.

아키텍처:
  - 입력: feature_snapshot (서브시스템 피처 값 딕셔너리)
  - Transformer Encoder로 피처 간 어텐션 학습
  - 각 피처의 이상 기여도(attention weight) → Root Cause 랭킹

재학습/재배포 대비:
  - ModelRegistry 패턴: load_weights(path) 호출로 가중치 교체
  - 가중치 없으면 통계 기반 fallback 사용 (attention score 근사)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch 없음 — CausTR 통계 fallback 모드")


# ── 모델 아키텍처 ─────────────────────────────────────────────────────────────

if TORCH_AVAILABLE:
    class CausTRModel(nn.Module):
        """
        Causal Transformer for Root Cause Analysis.
        입력: (1, n_features) 피처 스냅샷
        출력: (n_features,) 이상 기여도 점수
        """

        def __init__(self, n_features: int, d_model: int = 64, nhead: int = 4, num_layers: int = 2):
            super().__init__()
            self.n_features = n_features

            # 피처 임베딩
            self.feature_embed = nn.Linear(1, d_model)
            self.pos_embed = nn.Parameter(torch.randn(1, n_features, d_model) * 0.1)

            # Transformer Encoder
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=d_model * 4,
                dropout=0.1,
                batch_first=True,
                norm_first=True,   # Pre-LN (학습 안정성)
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

            # 기여도 헤드
            self.score_head = nn.Sequential(
                nn.Linear(d_model, 32),
                nn.ReLU(),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            # x: (B, n_features)
            B, F = x.shape
            x_emb = self.feature_embed(x.unsqueeze(-1))  # (B, F, d_model)
            x_emb = x_emb + self.pos_embed               # positional embedding
            out = self.transformer(x_emb)                # (B, F, d_model)
            scores = self.score_head(out).squeeze(-1)    # (B, F)
            return scores  # 각 피처의 이상 기여도


# ── CausTR 추론기 ─────────────────────────────────────────────────────────────

class CausTRInferencer:
    """
    CausTR 추론 래퍼.
    가중치 파일이 없으면 통계 기반 fallback으로 동작.
    """

    def __init__(self, n_features: int, model_dir: Optional[Path] = None, subsystem: str = ""):
        self._n_features = n_features
        self._model = None
        self._device = None

        if TORCH_AVAILABLE and model_dir is not None:
            pt_path = model_dir / f"caustr_{subsystem}.pt" if subsystem else model_dir / "caustr.pt"
            if not pt_path.exists():
                pt_path = model_dir / "caustr.pt"
            if pt_path.exists():
                self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                self._model = CausTRModel(n_features).to(self._device)
                self._model.load_state_dict(
                    torch.load(pt_path, map_location=self._device, weights_only=True)
                )
                self._model.eval()
                logger.info("CausTR 가중치 로드: %s", pt_path)
            else:
                logger.info("CausTR 가중치 없음 — 통계 fallback 모드")

    def predict(
        self,
        feature_snapshot: Dict[str, float],
        feature_names: List[str],
        top_k: int = 5,
    ) -> List[Dict]:
        """
        feature_snapshot: {"feat_name": value, ...}
        반환: [{"variable": str, "score": float, "rank": int}, ...]
        """
        # 피처 벡터 구성
        vec = np.array(
            [feature_snapshot.get(n, 0.0) for n in feature_names],
            dtype=np.float32,
        )

        if self._model is not None and TORCH_AVAILABLE:
            scores = self._predict_nn(vec)
        else:
            scores = self._predict_statistical(vec)

        # Top-K 랭킹
        ranked_idx = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "variable": feature_names[i],
                "score":    float(round(scores[i], 4)),
                "rank":     rank + 1,
            }
            for rank, i in enumerate(ranked_idx)
        ]

    @torch.no_grad()
    def _predict_nn(self, vec: np.ndarray) -> np.ndarray:
        t = torch.tensor(vec).unsqueeze(0).to(self._device)
        scores = self._model(t).squeeze(0).cpu().numpy()
        return scores

    def _predict_statistical(self, vec: np.ndarray) -> np.ndarray:
        """
        Fallback: 피처 값의 절댓값을 기여도로 사용.
        (정규화된 피처 기준으로 0에서 멀수록 이상 기여도 높음)
        """
        scores = np.abs(vec - 0.5) * 2.0   # [0,1] 범위로 정규화
        # 약간의 노이즈로 동점 방지
        scores += np.random.uniform(0, 0.001, size=scores.shape)
        return scores

    def load_weights(self, path: Path) -> None:
        """재배포 시 새 가중치 로드 (hot-reload 지원)"""
        if not TORCH_AVAILABLE:
            return
        if self._model is None:
            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self._model = CausTRModel(self._n_features).to(self._device)
        self._model.load_state_dict(
            torch.load(path, map_location=self._device, weights_only=True)
        )
        self._model.eval()
        logger.info("CausTR 가중치 재로드: %s", path)
