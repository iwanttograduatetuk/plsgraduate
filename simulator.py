"""
Edge-Cloud Hybrid PdM Pipeline — 고급 시뮬레이터
피벗된 13개 컬럼으로 AI가 이해할 수 있는 형태로 전송
"""

import asyncio
import logging
import sys
import random
import json
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pandas as pd
import numpy as np

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
DATASET_BASE    = "dataset_causRCA"
INGEST_URL      = "http://localhost:8000/ingest"
NUM_MACHINES    = 100
SEND_INTERVAL   = 1.0
REQUEST_TIMEOUT = 5.0
NOISE_LEVEL     = 0.001
LOG_INTERVAL    = 10
FAULT_CONFIRM   = 3
FAULT_MIN_SEC   = 10
FAULT_MAX_SEC   = 60
FAULT_TYPES     = ["coolant", "hydraulics", "probe"]
COLUMNS_PATH    = "columns.json"

FAULT_FOLDERS = {
    "coolant":    "dig_twin/exp_coolant",
    "hydraulics": "dig_twin/exp_hydraulics",
    "probe":      "dig_twin/exp_probe",
    "normal":     "real_op",
}

# ──────────────────────────────────────────────
# 로거
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("simulator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("pdm-simulator")


# ──────────────────────────────────────────────
# 카운터
# ──────────────────────────────────────────────
class Stats:
    success: int = 0
    failure: int = 0

    @classmethod
    def reset(cls):
        s, f = cls.success, cls.failure
        cls.success = 0
        cls.failure = 0
        return s, f


# ──────────────────────────────────────────────
# CSV 피벗 변환
# ──────────────────────────────────────────────
def pivot_csv(csv_path: Path, columns: list) -> pd.DataFrame:
    try:
        df = pd.read_csv(csv_path).fillna(0)
        df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)

        if "type" in df.columns:
            df = df[df["type"] != "Binary"]

        if len(df) == 0:
            return pd.DataFrame()

        pivoted = df.pivot_table(
            index="time_s",
            columns="node",
            values="value",
            aggfunc="last"
        ).reset_index(drop=True).fillna(0)

        # AI가 아는 컬럼만 선택
        available = [c for c in columns if c in pivoted.columns]
        if not available:
            return pd.DataFrame()

        result = pd.DataFrame(0.0, index=pivoted.index, columns=columns)
        result[available] = pivoted[available]
        return result

    except Exception as e:
        logger.warning(f"피벗 실패 {csv_path.name}: {e}")
        return pd.DataFrame()


# ──────────────────────────────────────────────
# 폴더에서 CSV 로드 + 피벗
# ──────────────────────────────────────────────
def load_csvs(folder: str, columns: list) -> list:
    path = Path(DATASET_BASE) / folder
    if not path.exists():
        logger.critical(f"폴더 없음: {path.resolve()}")
        sys.exit(1)

    csv_files = sorted(path.glob("*.csv"))
    rows_list = []
    for f in csv_files:
        pivoted = pivot_csv(f, columns)
        if len(pivoted) > 0:
            for _, row in pivoted.iterrows():
                rows_list.append(row.to_dict())

    logger.info(f"[{folder}] {len(rows_list)}개 행 로드!")
    return rows_list


# ──────────────────────────────────────────────
# 노이즈 추가
# ──────────────────────────────────────────────
def add_noise(row: dict) -> dict:
    noisy = {}
    for k, v in row.items():
        if isinstance(v, (int, float)):
            noise = np.random.normal(0, abs(v) * NOISE_LEVEL + 1e-6)
            noisy[k] = round(float(v) + noise, 6)
        else:
            noisy[k] = v
    return noisy


# ──────────────────────────────────────────────
# 단일 기계 워커
# ──────────────────────────────────────────────
async def machine_worker(
    machine_id: int,
    datasets: dict,
    session: aiohttp.ClientSession,
) -> None:
    fault_type      = random.choice(FAULT_TYPES)
    fault_delay     = random.uniform(FAULT_MIN_SEC, FAULT_MAX_SEC)
    fault_count     = 0
    fault_confirmed = False
    current_state   = "normal"
    start_time      = asyncio.get_event_loop().time()

    normal_rows = datasets["normal"]
    row_idx     = (machine_id * 7) % len(normal_rows)

    logger.info(f"[Machine {machine_id:03d}] 시작! 예정고장={fault_type} {fault_delay:.0f}초후")

    while True:
        cycle_start = asyncio.get_event_loop().time()
        elapsed     = cycle_start - start_time

        # 상태 결정
        if fault_confirmed:
            current_state = fault_type
            rows = datasets[fault_type]
        elif elapsed >= fault_delay:
            fault_count += 1
            current_state = f"suspect_{fault_type}_{fault_count}"
            rows = datasets[fault_type]
            if fault_count >= FAULT_CONFIRM:
                fault_confirmed = True
                current_state   = fault_type
                logger.warning(
                    f"[Machine {machine_id:03d}] "
                    f"🚨 {fault_type.upper()} 고장 확정!"
                )
        else:
            current_state = "normal"
            rows = normal_rows

        # 데이터 읽기
        row_idx = row_idx % len(rows)
        row     = add_noise(rows[row_idx].copy())

        # JSON 구성
        message = {
            "machine_id": machine_id,
            "state":      current_state,
            "is_fault":   fault_confirmed,
            "fault_type": fault_type if fault_confirmed else None,
            "fault_count": fault_count,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "row_index":  row_idx,
            "payload":    row,
        }

        # 전송
        try:
            async with session.post(INGEST_URL, json=message) as resp:
                if resp.status == 200:
                    Stats.success += 1
                    if fault_count > 0 and not fault_confirmed:
                        logger.info(
                            f"[Machine {machine_id:03d}] "
                            f"⚠️  {fault_type} 의심 {fault_count}회째!"
                        )
                else:
                    Stats.failure += 1
                    logger.warning(f"[Machine {machine_id:03d}] ✗ {resp.status}")

        except aiohttp.ClientConnectorError:
            Stats.failure += 1
            logger.error(f"[Machine {machine_id:03d}] 서버 연결 실패!")
        except asyncio.TimeoutError:
            Stats.failure += 1
            logger.error(f"[Machine {machine_id:03d}] 타임아웃!")
        except Exception as exc:
            Stats.failure += 1
            logger.error(f"[Machine {machine_id:03d}] 오류: {exc}")

        row_idx += 1
        elapsed2 = asyncio.get_event_loop().time() - cycle_start
        await asyncio.sleep(max(0.0, SEND_INTERVAL - elapsed2))


# ──────────────────────────────────────────────
# 통계 리포터
# ──────────────────────────────────────────────
async def stats_reporter() -> None:
    while True:
        await asyncio.sleep(LOG_INTERVAL)
        s, f = Stats.reset()
        total = s + f
        rate  = total / LOG_INTERVAL
        pct   = (s / total * 100) if total else 0
        logger.info(
            f"\n{'━'*50}\n"
            f"  📊 [{LOG_INTERVAL}s 집계] "
            f"총={total} 성공={s}({pct:.1f}%) "
            f"실패={f} RPS={rate:.1f}\n"
            f"{'━'*50}"
        )


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def main() -> None:
    # AI 컬럼 로드
    with open(COLUMNS_PATH, "r") as f:
        columns = json.load(f)
    logger.info(f"AI 컬럼 {len(columns)}개 로드!")

    logger.info("=" * 50)
    logger.info("  PdM 고급 시뮬레이터 시작!")
    logger.info(f"  기계 수: {NUM_MACHINES}대")
    logger.info(f"  AI 컬럼: {len(columns)}개")
    logger.info("=" * 50)

    # 데이터 로드
    datasets = {}
    for fault, folder in FAULT_FOLDERS.items():
        datasets[fault] = load_csvs(folder, columns)
        if not datasets[fault]:
            logger.warning(f"[{fault}] 데이터 없음! 정상 데이터로 대체!")
            datasets[fault] = datasets.get("normal", [{"dummy": 0}])

    connector = aiohttp.TCPConnector(limit=NUM_MACHINES * 2)
    timeout   = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            asyncio.create_task(
                machine_worker(mid, datasets, session)
            )
            for mid in range(1, NUM_MACHINES + 1)
        ]
        tasks.append(asyncio.create_task(stats_reporter()))

        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("종료합니다.")