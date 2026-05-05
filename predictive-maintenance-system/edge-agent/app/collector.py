"""
CNC 센서 데이터 수집 모듈
──────────────────────────────────────────────────────────────
두 가지 모드 지원:
  1. replay_mode=True  → real_op CSV 파일을 재생 (개발/테스트용)
  2. replay_mode=False → OPC-UA / MQTT 실시간 수집 (프로덕션)
"""

from __future__ import annotations

import asyncio
import glob
import logging
import random
from pathlib import Path
from typing import AsyncIterator, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class CSVReplayCollector:
    def __init__(
        self,
        csv_dir: Path,
        feature_names: list[str],
        col_min: np.ndarray,
        col_range: np.ndarray,
        speed_factor: float = 10.0,
    ):
        self._csv_dir = csv_dir
        self._feature_names = feature_names
        self._col_min = col_min
        self._col_range = col_range
        self._speed_factor = speed_factor
        self._files = sorted(glob.glob(str(csv_dir / "*.csv")))
        if not self._files:
            raise FileNotFoundError(f"CSV 파일 없음: {csv_dir}")
        logger.info("CSVReplayCollector: %d개 파일 발견", len(self._files))

    async def stream(self) -> AsyncIterator[np.ndarray]:
        file_idx = random.randint(0, len(self._files) - 1)
        while True:
            path = self._files[file_idx % len(self._files)]
            file_idx += 1
            try:
                df = pd.read_csv(path)
                df = self._align_columns(df)
                arr = df.values.astype(np.float32)
                arr = np.clip((arr - self._col_min) / self._col_range, 0.0, 1.0)
                for row in arr:
                    yield row
                    await asyncio.sleep(1.0 / self._speed_factor)
            except Exception as e:
                logger.warning("CSV 읽기 오류 (%s): %s", path, e)
                await asyncio.sleep(1.0)

    def _align_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Long 포맷(time_s, node, value, type) → Wide 포맷(T × 89) 변환.
        Wide 포맷이면 그대로 처리.
        """
        # Wide 포맷 감지: feature 이름이 컬럼에 있는지 확인
        if set(self._feature_names[:5]).issubset(set(df.columns)):
            result = pd.DataFrame(index=df.index)
            for name in self._feature_names:
                if name in df.columns:
                    result[name] = pd.to_numeric(df[name], errors="coerce").fillna(0.0)
                else:
                    result[name] = 0.0
            return result

        # Long 포맷 처리 (time_s, node, value, type)
        # Colab 학습과 동일한 pivot_table 방식 — 이벤트 타임스탬프만 유지
        if not {"time_s", "node", "value"}.issubset(df.columns):
            raise ValueError(f"지원하지 않는 CSV 포맷: {list(df.columns)}")

        # bool 문자열 → float 변환
        def _b2f(x):
            s = str(x).strip().lower()
            if s == "true":
                return 1.0
            elif s == "false":
                return 0.0
            try:
                return float(s)
            except (ValueError, TypeError):
                return 0.0

        df = df.copy()
        df["value"] = df["value"].map(_b2f)

        # Colab과 동일: pivot_table (이벤트 발생 시점만 행으로 유지)
        wide = df.pivot_table(
            index="time_s", columns="node", values="value", aggfunc="last"
        )
        wide = wide.reindex(columns=self._feature_names).fillna(0.0)

        return wide.astype(np.float32)


class OPCUACollector:
    def __init__(
        self,
        opc_url: str,
        node_ids: Dict[str, str],
        feature_names: list[str],
        col_min: np.ndarray,
        col_range: np.ndarray,
        interval: float = 1.0,
    ):
        self._opc_url = opc_url
        self._node_ids = node_ids
        self._feature_names = feature_names
        self._col_min = col_min
        self._col_range = col_range
        self._interval = interval

    async def stream(self) -> AsyncIterator[np.ndarray]:
        logger.warning("OPC-UA 수집기는 스텁 상태입니다. replay_mode=True를 사용하세요.")
        while True:
            row = np.random.uniform(0.0, 0.1, size=len(self._feature_names)).astype(np.float32)
            yield row
            await asyncio.sleep(self._interval)


def create_collector(
    replay_mode: bool,
    replay_csv_dir: Path,
    feature_names: list[str],
    col_min: np.ndarray,
    col_range: np.ndarray,
    speed_factor: float = 10.0,
    opc_url: str = "",
    node_ids: dict | None = None,
):
    if replay_mode:
        return CSVReplayCollector(
            csv_dir=replay_csv_dir,
            feature_names=feature_names,
            col_min=col_min,
            col_range=col_range,
            speed_factor=speed_factor,
        )
    return OPCUACollector(
        opc_url=opc_url,
        node_ids=node_ids or {},
        feature_names=feature_names,
        col_min=col_min,
        col_range=col_range,
    )