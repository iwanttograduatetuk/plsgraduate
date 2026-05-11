"""
데모 플레이리스트 컬렉터
────────────────────────────────────────────────────────────────
발표/시연용 데이터 재생기.

고정 플레이리스트를 한 사이클로 반복합니다:

  정상(first_normal_sec)
  → coolant 폴트(fault_sec)
  → 정상(inter_normal_sec)
  → hydraulics 폴트(fault_sec)
  → 정상(inter_normal_sec)
  → probe 폴트(fault_sec)
  → 랜덤 폴트(fault_sec)
  → 반복

폴트 구간은 causes.json 의 cause_start_at 직전(FAULT_PRE_ROLL_SEC)부터
재생하여 약 10~15초 안에 이상 감지가 트리거됩니다.

사용:
    DATA_DEMO_MODE=true
    DATA_DEMO_FIRST_NORMAL_SEC=10   # 첫 정상 구간 길이(초)
    DATA_DEMO_INTER_NORMAL_SEC=5    # 폴트 사이 정상 구간 길이(초)
    DATA_DEMO_FAULT_SEC=10          # 각 폴트 구간 길이(초)
    DATA_DIG_TWIN_DIR=<path>        # dig_twin 디렉터리 경로
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 폴트 순환 순서
FAULT_CYCLE = ["coolant", "hydraulics", "probe"]

# 폴트 시작 몇 초 전부터 재생할지 (너무 앞에서 시작하면 정상처럼 보임)
FAULT_PRE_ROLL_SEC = 15.0


@dataclass
class _PhaseInfo:
    kind: str          # "normal" | "fault"
    subsystem: str     # "coolant" | "hydraulics" | "probe" | "real_op"
    csv_path: Path
    cause_start_at: Optional[float] = None   # fault 구간 only


class DemoPlaylistCollector:
    """
    고정 플레이리스트로 정상/폴트 구간을 재생하는 데모 전용 컬렉터.

    Parameters
    ----------
    real_op_dir         : real_op CSV 디렉터리 (정상 데이터)
    dig_twin_dir        : dig_twin 루트 (exp_coolant / exp_hydraulics / exp_probe)
    feature_names       : 89개 피처 이름 (meta.json 기준)
    col_min             : min-max scaler min 벡터
    col_range           : min-max scaler range 벡터
    first_normal_sec    : 첫 번째 정상 구간 길이(초), 기본 10
    inter_normal_sec    : 폴트 사이 정상 구간 길이(초), 기본 5
    fault_sec           : 각 폴트 구간 길이(초), 기본 10
    speed_factor        : 재생 속도 배율 (10 = 10배속), 기본 10.0
    """

    def __init__(
        self,
        real_op_dir: Path,
        dig_twin_dir: Path,
        feature_names: List[str],
        col_min: np.ndarray,
        col_range: np.ndarray,
        first_normal_sec: int = 10,
        inter_normal_sec: int = 5,
        fault_sec: int = 10,
        speed_factor: float = 10.0,
    ):
        self._feature_names = feature_names
        self._col_min = col_min
        self._col_range = col_range
        self._speed_factor = speed_factor
        # 실제 시간(초) → 재생 행 수 (speed_factor=10 → 1행=0.1초)
        self._first_normal_rows = int(first_normal_sec * speed_factor)
        self._inter_normal_rows = int(inter_normal_sec * speed_factor)
        self._fault_rows        = int(fault_sec        * speed_factor)

        # real_op CSV 목록
        self._normal_files: List[Path] = sorted(real_op_dir.glob("*.csv"))
        if not self._normal_files:
            raise FileNotFoundError(f"real_op CSV 없음: {real_op_dir}")

        # dig_twin 폴트 CSV 목록 (서브시스템별)
        self._fault_files: Dict[str, List[Path]] = {}
        for sub in FAULT_CYCLE:
            pattern = f"exp_{sub}/**/faultDataset_{sub}_*.csv"
            files = sorted(dig_twin_dir.glob(pattern))
            if not files:
                logger.warning("[DemoCollector] 폴트 파일 없음: %s (%s)", sub, dig_twin_dir)
            self._fault_files[sub] = files

        logger.info(
            "[DemoCollector] 준비 완료 | normal %d개 | 폴트: coolant %d hydraulics %d probe %d",
            len(self._normal_files),
            len(self._fault_files.get("coolant", [])),
            len(self._fault_files.get("hydraulics", [])),
            len(self._fault_files.get("probe", [])),
        )
        logger.info(
            "[DemoCollector] 플레이리스트 | 첫정상 %d행(%ds) / 중간정상 %d행(%ds) / 폴트 %d행(%ds) @ %.0fx속",
            self._first_normal_rows, first_normal_sec,
            self._inter_normal_rows, inter_normal_sec,
            self._fault_rows,        fault_sec,
            speed_factor,
        )

    # ── 메인 스트림 ──────────────────────────────────────────────────────────

    async def stream(self) -> AsyncIterator[np.ndarray]:
        """
        고정 플레이리스트 무한 반복:
          정상(first) → coolant → 정상(inter) → hydraulics
          → 정상(inter) → probe → 랜덤폴트 → 반복
        """
        cycle = 0
        while True:
            cycle += 1
            logger.info("━━━ [DEMO] 사이클 %d 시작 ━━━", cycle)

            async for row in self._play_normal("첫 정상",   self._first_normal_rows): yield row
            async for row in self._play_fault ("coolant",   self._fault_rows):        yield row
            async for row in self._play_normal("중간 정상", self._inter_normal_rows): yield row
            async for row in self._play_fault ("hydraulics",self._fault_rows):        yield row
            async for row in self._play_normal("중간 정상", self._inter_normal_rows): yield row
            async for row in self._play_fault ("probe",     self._fault_rows):        yield row

            random_sub = random.choice(FAULT_CYCLE)
            async for row in self._play_fault(f"{random_sub}(랜덤)", self._fault_rows, subsystem=random_sub):
                yield row

            logger.info("━━━ [DEMO] 사이클 %d 종료 ━━━", cycle)

    # ── 구간 헬퍼 (async generator) ──────────────────────────────────────────

    async def _play_normal(self, label: str, max_rows: int) -> AsyncIterator[np.ndarray]:
        phase = self._pick_normal_phase()
        logger.info("▶ [DEMO] %s | %s | %d행", label, phase.csv_path.name, max_rows)
        async for row in self._replay_phase(phase, max_rows=max_rows):
            yield row

    async def _play_fault(
        self, label: str, max_rows: int, subsystem: str | None = None
    ) -> AsyncIterator[np.ndarray]:
        sub = subsystem or label
        phase = self._pick_fault_phase(sub)
        if phase is None:
            logger.warning("[DEMO] %s 폴트 파일 없음 — 정상으로 대체", sub)
            phase = self._pick_normal_phase()
        else:
            logger.info(
                "⚠ [DEMO] 폴트 %s | %s | cause_start=%.1f초 | %d행",
                label, phase.csv_path.name, phase.cause_start_at or 0.0, max_rows,
            )
        async for row in self._replay_phase(phase, max_rows=max_rows):
            yield row

    # ── Phase 선택 ───────────────────────────────────────────────────────────

    def _pick_normal_phase(self) -> _PhaseInfo:
        path = random.choice(self._normal_files)
        return _PhaseInfo(kind="normal", subsystem="real_op", csv_path=path)

    def _pick_fault_phase(self, subsystem: str) -> Optional[_PhaseInfo]:
        files = self._fault_files.get(subsystem, [])
        if not files:
            return None
        path = random.choice(files)
        cause_start = self._load_cause_start(path)
        return _PhaseInfo(
            kind="fault",
            subsystem=subsystem,
            csv_path=path,
            cause_start_at=cause_start,
        )

    @staticmethod
    def _load_cause_start(fault_csv: Path) -> Optional[float]:
        """같은 디렉터리의 causes.json 에서 cause_start_at 읽기"""
        causes_path = fault_csv.parent / "causes.json"
        if not causes_path.exists():
            return None
        try:
            data = json.loads(causes_path.read_text())
            return float(data.get("cause_start_at", 0.0))
        except Exception as e:
            logger.warning("causes.json 파싱 오류 (%s): %s", causes_path, e)
            return None

    # ── CSV 재생 ─────────────────────────────────────────────────────────────

    async def _replay_phase(
        self,
        phase: _PhaseInfo,
        max_rows: int,
    ) -> AsyncIterator[np.ndarray]:
        """
        CSV 파일을 wide 포맷으로 변환 후 최대 max_rows 행 yield.
        폴트 파일은 cause_start_at 직전(FAULT_PRE_ROLL_SEC)부터 시작.
        """
        try:
            df_raw = pd.read_csv(phase.csv_path)
            df = self._to_wide(df_raw)
        except Exception as e:
            logger.error("CSV 읽기 실패 (%s): %s", phase.csv_path, e)
            return

        if df.empty:
            logger.warning("빈 DataFrame: %s", phase.csv_path)
            return

        # 폴트 파일: cause_start_at 직전부터 seek
        start_row = 0
        if phase.kind == "fault" and phase.cause_start_at is not None:
            seek_time = max(0.0, phase.cause_start_at - FAULT_PRE_ROLL_SEC)
            # wide 포맷 행 인덱스로 변환 (1행 ≈ 1초 단위 이벤트)
            total_rows = len(df)
            start_row = min(
                int(seek_time / max(df.index[-1], 1.0) * total_rows),
                max(0, total_rows - self._fault_rows),
            )
            logger.debug(
                "[DEMO] 폴트 seek | cause_start=%.1f → seek_time=%.1f → start_row=%d/%d",
                phase.cause_start_at, seek_time, start_row, total_rows,
            )

        arr = df.values.astype(np.float32)
        arr = np.clip((arr - self._col_min) / self._col_range, 0.0, 1.0)

        count = 0
        idx = start_row
        while count < max_rows:
            if idx >= len(arr):
                # 파일 끝 → 처음부터 다시 (max_rows 채울 때까지)
                idx = 0
            row = arr[idx]
            idx += 1
            count += 1
            yield row
            await asyncio.sleep(1.0 / self._speed_factor)

    # ── Wide 포맷 변환 ────────────────────────────────────────────────────────

    def _to_wide(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Long(time_s, node, value, type) 또는 Wide 포맷 모두 처리.
        CSVReplayCollector._align_columns 과 동일 로직.
        """
        feature_set = set(self._feature_names)

        # Wide 포맷 감지
        if len(feature_set & set(df.columns)) >= 5:
            result = pd.DataFrame(index=df.index)
            for name in self._feature_names:
                if name in df.columns:
                    result[name] = pd.to_numeric(df[name], errors="coerce").fillna(0.0)
                else:
                    result[name] = 0.0
            return result

        # Long 포맷 처리
        if not {"time_s", "node", "value"}.issubset(df.columns):
            raise ValueError(f"지원하지 않는 포맷: {list(df.columns)}")

        def _b2f(x):
            s = str(x).strip().lower()
            if s == "true":  return 1.0
            if s == "false": return 0.0
            try: return float(s)
            except (ValueError, TypeError): return 0.0

        df = df.copy()
        df["value"] = df["value"].map(_b2f)
        wide = df.pivot_table(
            index="time_s", columns="node", values="value", aggfunc="last"
        )
        wide = wide.reindex(columns=self._feature_names).fillna(0.0)
        return wide.astype(np.float32)
