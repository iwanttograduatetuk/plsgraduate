"""
데이터 전처리 모듈
──────────────────────────────────────────────────
- scaler_info.pkl 기반 min-max 정규화
- 슬라이딩 윈도우 버퍼 관리
- 89 피처 순서 보장 (meta.json의 feature_names 기준)
"""

from __future__ import annotations

import logging
import pickle
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

N_FEATURES = 89


class Preprocessor:
    """
    실시간 센서 데이터를 LSTM 입력 윈도우로 변환.

    사용법:
        pre = Preprocessor(processed_data_dir, window_size=30)
        pre.push(raw_feature_dict)   # 1초마다 호출
        window = pre.get_window()    # 윈도우가 가득 차면 ndarray 반환, 아니면 None
    """

    def __init__(self, processed_data_dir: Path, window_size: int = 30):
        self._window_size = window_size
        self._buf: deque = deque(maxlen=window_size)

        # scaler 로드 (min-max 정규화용)
        scaler_path = processed_data_dir / "scaler_info.pkl"
        meta_path   = processed_data_dir / "meta.json"

        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                scaler = pickle.load(f)
            self._col_min: np.ndarray   = scaler["min"].astype(np.float32)
            self._col_range: np.ndarray = scaler["range"].astype(np.float32)
            self._col_range[self._col_range == 0] = 1.0
            logger.info("scaler_info.pkl 로드 완료 (%d 피처)", len(self._col_min))
        else:
            logger.warning("scaler_info.pkl 없음 — 정규화 생략")
            self._col_min   = np.zeros(N_FEATURES, dtype=np.float32)
            self._col_range = np.ones(N_FEATURES,  dtype=np.float32)

        # feature 이름 순서 로드
        import json
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            self._feature_names: List[str] = meta["feature_names"]
        else:
            logger.warning("meta.json 없음 — 피처 이름 미사용")
            self._feature_names = [f"feat_{i}" for i in range(N_FEATURES)]

        self._feat_idx: Dict[str, int] = {n: i for i, n in enumerate(self._feature_names)}

    # ── 퍼블릭 인터페이스 ──────────────────────────────────────────────────────

    def push(self, raw: Dict[str, float]) -> None:
        """
        센서 딕셔너리 1 타임스텝 삽입.
        raw = {"feat_name": value, ...}
        """
        row = np.zeros(N_FEATURES, dtype=np.float32)
        for name, val in raw.items():
            idx = self._feat_idx.get(name)
            if idx is not None:
                row[idx] = float(val) if val is not None else 0.0
        # 정규화
        row = np.clip((row - self._col_min) / self._col_range, 0.0, 1.0)
        self._buf.append(row)

    def push_row(self, row: np.ndarray) -> None:
        """
        이미 정렬된 89-dim 배열을 삽입 (CSV replay 등에서 사용).
        정규화는 호출자 책임.
        """
        if row.shape[0] != N_FEATURES:
            raise ValueError(f"피처 수 불일치: {row.shape[0]} != {N_FEATURES}")
        self._buf.append(row.astype(np.float32))

    def get_window(self) -> Optional[np.ndarray]:
        """윈도우가 가득 차면 (window_size, N_FEATURES) 배열 반환, 아니면 None"""
        if len(self._buf) < self._window_size:
            return None
        return np.stack(list(self._buf), axis=0)  # (T, F)

    def is_ready(self) -> bool:
        return len(self._buf) >= self._window_size

    def get_last_raw_values(self, feature_names: List[str]) -> Dict[str, float]:
        """마지막 타임스텝의 특정 피처 값 반환 (이벤트 페이로드용)"""
        if not self._buf:
            return {}
        last = list(self._buf)[-1]
        return {
            name: float(last[self._feat_idx[name]])
            for name in feature_names
            if name in self._feat_idx
        }

    @property
    def feature_names(self) -> List[str]:
        return self._feature_names
