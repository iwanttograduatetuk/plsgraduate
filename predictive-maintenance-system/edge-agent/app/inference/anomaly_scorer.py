"""
이상 점수 계산 및 판정 모듈
────────────────────────────────────────
재구성 오차(MSE) → anomaly_score, is_anomaly 판정
이상 점수 = reconstruction_error / threshold (정규화된 점수)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class SubsystemScore:
    """서브시스템별 이상 점수"""
    name: str
    reconstruction_error: float
    threshold: float
    is_anomaly: bool
    anomaly_score: float           # error / threshold
    feature_values: Dict[str, float]   # 현재 윈도우 마지막 타임스텝 피처값

    @property
    def severity(self) -> str:
        if self.anomaly_score < 1.5:
            return "INFO"
        elif self.anomaly_score < 3.0:
            return "WARNING"
        return "CRITICAL"


@dataclass
class MachineScore:
    """3개 서브시스템 종합 판정"""
    coolant: SubsystemScore
    hydraulics: SubsystemScore
    probe: SubsystemScore

    @property
    def is_anomaly(self) -> bool:
        return any([
            self.coolant.is_anomaly,
            self.hydraulics.is_anomaly,
            self.probe.is_anomaly,
        ])

    @property
    def machine_status(self) -> str:
        return "ANOMALY" if self.is_anomaly else "NORMAL"

    @property
    def anomalous_subsystems(self) -> list[SubsystemScore]:
        return [s for s in [self.coolant, self.hydraulics, self.probe] if s.is_anomaly]

    def to_telemetry_payload(self) -> dict:
        return {
            "subsystem_scores": {
                s.name: {
                    "error":      s.reconstruction_error,
                    "threshold":  s.threshold,
                    "is_anomaly": s.is_anomaly,
                }
                for s in [self.coolant, self.hydraulics, self.probe]
            },
            "machine_status": self.machine_status,
        }


def compute_score(
    name: str,
    reconstruction_error: float,
    threshold: float,
    threshold_override: Optional[float],
    feature_values: Dict[str, float],
) -> SubsystemScore:
    """재구성 오차로부터 SubsystemScore 생성"""
    thr = threshold_override if threshold_override is not None else threshold
    score = reconstruction_error / max(thr, 1e-9)
    return SubsystemScore(
        name=name,
        reconstruction_error=reconstruction_error,
        threshold=thr,
        is_anomaly=(reconstruction_error >= thr),
        anomaly_score=score,
        feature_values=feature_values,
    )
