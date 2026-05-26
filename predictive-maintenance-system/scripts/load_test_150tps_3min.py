"""
CNC 150대 공통원인고장(CCF) 시뮬레이션 — 200 TPS 부하 테스트
anomaly-events-critical 토픽으로 200 TPS, 60초간 메시지 주입
멀티스레드 방식으로 정확한 TPS 제어
"""

import json
import random
import time
import uuid
import threading
from datetime import datetime, timezone
from kafka import KafkaProducer

KAFKA_BOOTSTRAP = "kafka.kafka.svc.cluster.local:9092"
TOPIC = "anomaly-events-critical"
TARGET_TPS = 150
DURATION_SEC = 180
NUM_THREADS = 5  # 스레드당 30 TPS

SUBSYSTEMS = ["hydraulics", "coolant", "probe"]
MACHINES = [f"cnc-{i:03d}" for i in range(1, 151)]

FEATURE_NAMES = [
    "Hyd_Pressure", "M_PowerOnDuration", "Prog_CuttingTime", "Prog_CycleTime", "Prog_LineNo",
    "Spd_ActPos_C", "Spd_ActPos_X", "Spd_ActPos_Z",
    "Spd_ActSpeed_C", "Spd_ActSpeed_X", "Spd_ActSpeed_Z",
    "CLF_A_700307", "CLT_A_700310", "F_A_700313", "HP_A_700304", "LP_A_700301",
    "LT_A_700317", "Hyd_A_700202", "Hyd_A_700203", "Hyd_A_700204", "Hyd_A_700205",
    "Hyd_A_700206", "Hyd_A_700207", "Hyd_A_700208", "MPA_A_701124", "MPA_A_701125",
    "Prog_A_701330", "SR_A_67040", "T_A_701309",
    "CBC_Closed", "CBC_close", "CBC_isOpen", "CBC_open",
    "CLF_Filter_Ok", "CLT_Level_lt_Min", "ExU_On", "ExU_isOff", "F_Filter_Ok",
    "HP_Pump_Ok", "HP_Pump_isOff", "Hyd_Filter_Ok", "Hyd_IsEnabled", "Hyd_Level_Ok",
    "Hyd_Pump_Ok", "Hyd_Pump_On", "Hyd_Pump_isOff", "Hyd_Temp_lt_70", "Hyd_Temp_lt_80",
    "Hyd_Valve_P_Up", "LP_Pump_Ok", "LP_Pump_On", "LT_Level_Ok", "LT_Pump_Ok",
    "M_ErrorActive", "M_WarnActive", "M_WarnWithStacklight", "SL_Green", "SL_Red", "SL_Yellow",
]

sent_total = 0
lock = threading.Lock()


def make_event(idx: int) -> dict:
    machine = "cnc-001"
    subsystem = SUBSYSTEMS[idx % len(SUBSYSTEMS)]
    anomaly_score = round(random.uniform(2.5, 5.0), 4)
    return {
        "event_id":             str(uuid.uuid4()),
        "site_id":              "site-A",
        "machine_id":           machine,
        "subsystem":            subsystem,
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "reconstruction_error": round(anomaly_score * 0.8, 4),
        "anomaly_score":        anomaly_score,
        "severity":             "CRITICAL",
        "priority":             "critical",
        "feature_values":       {f: round(random.uniform(0, 1), 4) for f in FEATURE_NAMES},
    }


def worker(thread_id: int, tps_per_thread: int, duration: int):
    global sent_total
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks=1,
        linger_ms=5,
        batch_size=65536,
    )

    interval = 1.0 / tps_per_thread
    start = time.time()
    local_sent = 0

    while time.time() - start < duration:
        t0 = time.time()
        idx = thread_id * 1000000 + local_sent
        event = make_event(idx)
        producer.send(TOPIC, key=event["event_id"].encode(), value=event)
        local_sent += 1

        with lock:
            sent_total += 1

        elapsed = time.time() - t0
        sleep = interval - elapsed
        if sleep > 0:
            time.sleep(sleep)

    producer.flush()
    producer.close()


def main():
    tps_per_thread = TARGET_TPS // NUM_THREADS

    print(f"[부하테스트] 시작: {TARGET_TPS} TPS × {DURATION_SEC}초 → 토픽={TOPIC}")
    print(f"[부하테스트] 스레드 {NUM_THREADS}개 × {tps_per_thread} TPS")

    threads = []
    start = time.time()

    for i in range(NUM_THREADS):
        t = threading.Thread(target=worker, args=(i, tps_per_thread, DURATION_SEC))
        t.start()
        threads.append(t)

    while any(t.is_alive() for t in threads):
        time.sleep(5)
        elapsed = time.time() - start
        with lock:
            total = sent_total
        if elapsed > 0:
            print(f"  {int(elapsed):3d}초 | 전송: {total:,}건 | 실제 TPS: {total/elapsed:.1f}")

    for t in threads:
        t.join()

    total_time = time.time() - start
    print(f"\n[부하테스트] 완료: {sent_total:,}건 / {total_time:.1f}초 = {sent_total/total_time:.1f} TPS")


if __name__ == "__main__":
    main()
