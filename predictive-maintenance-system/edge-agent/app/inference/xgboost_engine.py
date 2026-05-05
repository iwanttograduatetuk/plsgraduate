"""
XGBoost 고장 분류 엔진
────────────────────────────────────────
학습된 XGBoost 모델을 로드하여 4-class 분류 수행.
Classes: 0=normal, 1=coolant, 2=hydraulics, 3=probe
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

CLASS_NAMES = {0: "normal", 1: "coolant", 2: "hydraulics", 3: "probe"}

# XGBoost 학습 시 사용된 59개 피처 (03_xgboost_train.py ALL_FEATURES 순서 일치)
XGBOOST_FEATURES = [
    # Numeric (11)
    "Hyd_Pressure", "M_PowerOnDuration",
    "Prog_CuttingTime", "Prog_CycleTime", "Prog_LineNo",
    "Spd_ActPos_C", "Spd_ActPos_X", "Spd_ActPos_Z",
    "Spd_ActSpeed_C", "Spd_ActSpeed_X", "Spd_ActSpeed_Z",
    # Alarm (18)
    "CLF_A_700307", "CLT_A_700310", "F_A_700313",
    "HP_A_700304", "LP_A_700301", "LT_A_700317",
    "Hyd_A_700202", "Hyd_A_700203", "Hyd_A_700204",
    "Hyd_A_700205", "Hyd_A_700206", "Hyd_A_700207", "Hyd_A_700208",
    "MPA_A_701124", "MPA_A_701125",
    "Prog_A_701330", "SR_A_67040", "T_A_701309",
    # Binary (30)
    "CBC_Closed", "CBC_close", "CBC_isOpen", "CBC_open",
    "CLF_Filter_Ok", "CLT_Level_lt_Min",
    "ExU_On", "ExU_isOff",
    "F_Filter_Ok",
    "HP_Pump_Ok", "HP_Pump_isOff",
    "Hyd_Filter_Ok", "Hyd_IsEnabled", "Hyd_Level_Ok",
    "Hyd_Pump_Ok", "Hyd_Pump_On", "Hyd_Pump_isOff",
    "Hyd_Temp_lt_70", "Hyd_Temp_lt_80", "Hyd_Valve_P_Up",
    "LP_Pump_Ok", "LP_Pump_On",
    "LT_Level_Ok", "LT_Pump_Ok",
    "M_ErrorActive", "M_WarnActive", "M_WarnWithStacklight",
    "SL_Green", "SL_Red", "SL_Yellow",
]


@dataclass
class XGBoostPrediction:
    """XGBoost 분류 결과"""
    predicted_class: int
    predicted_label: str
    probabilities: dict[str, float]  # class_name -> probability
    is_fault: bool  # predicted_class != 0


class XGBoostEngine:
    """XGBoost 모델 로드 및 추론"""

    def __init__(self, model_path: Path):
        self._model = None
        self._model_path = model_path
        self._loaded = False

    def load(self) -> bool:
        """모델 파일 로드. 성공 시 True."""
        try:
            import xgboost as xgb

            if not self._model_path.exists():
                logger.warning("XGBoost 모델 파일 없음: %s", self._model_path)
                return False

            self._model = xgb.XGBClassifier()
            self._model.load_model(str(self._model_path))
            self._loaded = True
            logger.info("XGBoost 모델 로드 완료: %s", self._model_path)
            return True

        except ImportError:
            logger.error("xgboost 패키지 미설치. pip install xgboost")
            return False
        except Exception as e:
            logger.error("XGBoost 모델 로드 실패: %s", e)
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def predict(self, features: np.ndarray) -> Optional[XGBoostPrediction]:
        """
        단일 샘플 추론.

        Parameters:
            features: (n_features,) 또는 (1, n_features) 배열
                      59개 피처 (NUMERIC + ALARM + BINARY)

        Returns:
            XGBoostPrediction or None (모델 미로드 시)
        """
        if not self._loaded or self._model is None:
            return None

        # Reshape to 2D if needed
        if features.ndim == 1:
            features = features.reshape(1, -1)

        pred_class = int(self._model.predict(features)[0])
        pred_label = CLASS_NAMES.get(pred_class, "unknown")

        # Get probabilities if available
        proba = {}
        try:
            probs = self._model.predict_proba(features)[0]
            for i, p in enumerate(probs):
                proba[CLASS_NAMES.get(i, f"class_{i}")] = float(p)
        except Exception:
            proba = {pred_label: 1.0}

        return XGBoostPrediction(
            predicted_class=pred_class,
            predicted_label=pred_label,
            probabilities=proba,
            is_fault=(pred_class != 0),
        )

    def reload(self, model_path: Optional[Path] = None) -> bool:
        """모델 핫 리로드"""
        if model_path:
            self._model_path = model_path
        self._loaded = False
        return self.load()
