"""
CausReg — Causal Regression 기반 Root Cause Analysis
──────────────────────────────────────────────────────────────────
causRCA 논문의 CausReg 모델 구현.

아키텍처:
  - Expert Graph를 활용한 인과 회귀 모델
  - 각 피처에 대해 Graph-guided Ridge Regression 수행
  - 잔차(residual) 크기 → Root Cause 기여도

재학습/재배포 대비:
  - load_weights() 메서드로 새 회귀 계수 로드
  - joblib pickle 포맷 사용
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn 없음 — CausReg fallback 모드")


class CausRegInferencer:
    """
    CausReg 추론 래퍼.
    회귀 모델 파일이 없으면 통계 기반 fallback 사용.
    """

    def __init__(self, model_dir: Optional[Path] = None):
        self._regressors: Dict[str, object] = {}   # feat_name → Ridge
        self._scaler: Optional[object] = None

        if model_dir is not None:
            pkl_path = model_dir / "causreg.pkl"
            if pkl_path.exists():
                with open(pkl_path, "rb") as f:
                    saved = pickle.load(f)
                self._regressors = saved.get("regressors", {})
                self._scaler     = saved.get("scaler")
                logger.info("CausReg 모델 로드: %d 회귀식", len(self._regressors))
            else:
                logger.info("CausReg 모델 없음 — 통계 fallback 모드")

    def predict(
        self,
        feature_snapshot: Dict[str, float],
        feature_names: List[str],
        top_k: int = 5,
    ) -> List[Dict]:
        """
        각 피처에 대해 회귀 잔차를 계산하여 Root Cause 랭킹 반환.
        """
        vec = np.array(
            [feature_snapshot.get(n, 0.0) for n in feature_names],
            dtype=np.float64,
        )

        if self._regressors and SKLEARN_AVAILABLE:
            scores = self._predict_regression(vec, feature_names)
        else:
            scores = self._predict_statistical(vec)

        ranked_idx = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "variable": feature_names[i],
                "score":    float(round(scores[i], 4)),
                "rank":     rank + 1,
            }
            for rank, i in enumerate(ranked_idx)
        ]

    def _predict_regression(self, vec: np.ndarray, feature_names: List[str]) -> np.ndarray:
        """
        각 피처 y_i를 나머지 피처들로 회귀한 후 잔차 계산.
        잔차가 클수록 인과 관계를 벗어난 이상 피처 → Root Cause 후보.
        """
        scores = np.zeros(len(feature_names))
        x_scaled = self._scaler.transform(vec.reshape(1, -1))[0] if self._scaler else vec

        for i, feat in enumerate(feature_names):
            if feat in self._regressors:
                reg = self._regressors[feat]
                # 해당 피처 제외한 다른 피처로 예측
                x_others = np.delete(x_scaled, i).reshape(1, -1)
                y_pred  = reg.predict(x_others)[0]
                residual = abs(x_scaled[i] - y_pred)
                scores[i] = residual
        return scores

    def _predict_statistical(self, vec: np.ndarray) -> np.ndarray:
        """Fallback: 피처 편차를 기여도로 사용"""
        scores = np.abs(vec - 0.5) * 2.0
        scores += np.random.uniform(0, 0.001, size=scores.shape)
        return scores

    def load_weights(self, path: Path) -> None:
        """재배포 시 새 회귀 모델 로드"""
        with open(path, "rb") as f:
            saved = pickle.load(f)
        self._regressors = saved.get("regressors", {})
        self._scaler     = saved.get("scaler")
        logger.info("CausReg 모델 재로드: %d 회귀식", len(self._regressors))
