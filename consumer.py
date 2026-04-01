"""
Edge-Cloud Hybrid PdM Pipeline — AI Consumer
Kafka에서 데이터 꺼내서 오토인코더로 실시간 이상 감지
"""

import asyncio
import logging
import json
import pickle
import warnings
import numpy as np
import torch
import torch.nn as nn
from aiokafka import AIOKafkaConsumer

# 경고 메시지 완전 제거
warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdm-consumer")

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
KAFKA_BROKER   = "localhost:9092"
KAFKA_TOPIC    = "pdm-raw-data"
MODEL_PATH     = "autoencoder_model.pt"
SCALER_PATH    = "scaler.pkl"
COLUMNS_PATH   = "columns.json"
THRESHOLD_PATH = "threshold.json"
HIDDEN_DIM     = 32


# ──────────────────────────────────────────────
# 오토인코더 구조
# ──────────────────────────────────────────────
class Autoencoder(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, HIDDEN_DIM),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(HIDDEN_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, input_dim),
        )

    def forward(self, x):
        return self.decoder(self.encoder(x))


# ──────────────────────────────────────────────
# 모델 로드
# ──────────────────────────────────────────────
def load_model():
    with open(COLUMNS_PATH, "r") as f:
        columns = json.load(f)

    with open(SCALER_PATH, "rb") as f:
        scaler = pickle.load(f)

    with open(THRESHOLD_PATH, "r") as f:
        threshold = json.load(f)["threshold"]

    model = Autoencoder(len(columns))
    model.load_state_dict(torch.load(MODEL_PATH, weights_only=True))
    model.eval()

    logger.info(f"모델 로드 완료! 컬럼={len(columns)}개 임계값={threshold:.6f}")
    return model, scaler, columns, threshold


# ──────────────────────────────────────────────
# 센서값 추출
# ──────────────────────────────────────────────
def extract_features(payload: dict, columns: list) -> np.ndarray:
    features = []
    for col in columns:
        val = payload.get(col, 0)
        try:
            features.append(float(val))
        except:
            features.append(0.0)
    return np.array(features, dtype=np.float32).reshape(1, -1)


# ──────────────────────────────────────────────
# 이상 감지
# ──────────────────────────────────────────────
def detect_anomaly(model, scaler, columns, threshold, payload: dict) -> dict:
    features = extract_features(payload, columns)

    # 스케일러에 컬럼 이름 전달해서 경고 제거
    import pandas as pd
    features_df     = pd.DataFrame(features, columns=columns)
    features_scaled = scaler.transform(features_df)

    tensor = torch.FloatTensor(features_scaled)
    with torch.no_grad():
        output = model(tensor)
        error  = torch.mean((tensor - output) ** 2).item()

    return {
        "error":      round(error, 8),
        "threshold":  round(threshold, 8),
        "is_anomaly": error > threshold,
    }


# ──────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────
fault_confirmed = {}
anomaly_count   = {}

def print_result(machine_id, result):
    if result["is_anomaly"]:
        anomaly_count[machine_id] = anomaly_count.get(machine_id, 0) + 1
        count = anomaly_count[machine_id]

        if machine_id not in fault_confirmed:
            if count >= 3:
                fault_confirmed[machine_id] = "ANOMALY"
                logger.warning(
                    f"🚨 [Machine {machine_id:03d}] "
                    f"고장 확정! (이상 {count}회) "
                    f"오차={result['error']:.6f} > 임계값={result['threshold']:.6f}"
                )
            else:
                logger.info(
                    f"⚠️  [Machine {machine_id:03d}] "
                    f"이상 의심 {count}회째! "
                    f"오차={result['error']:.6f}"
                )
    else:
        if machine_id not in fault_confirmed:
            anomaly_count[machine_id] = 0


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def main():
    logger.info("=" * 50)
    logger.info("  PdM AI Consumer 시작!")
    logger.info("  Kafka → 오토인코더 → 이상 감지!")
    logger.info("=" * 50)

    model, scaler, columns, threshold = load_model()

    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BROKER,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
        group_id="pdm-ai-group",
    )

    await consumer.start()
    logger.info(f"Kafka Consumer 시작! 토픽: {KAFKA_TOPIC}")

    total = 0
    try:
        async for msg in consumer:
            data       = msg.value
            machine_id = data.get("machine_id", 0)
            payload    = data.get("payload", {})

            result = detect_anomaly(model, scaler, columns, threshold, payload)
            total += 1

            if result["is_anomaly"]:
                print_result(machine_id, result)
            elif total % 100 == 0:
                logger.info(
                    f"✅ 정상 처리 중... "
                    f"누적={total}건 "
                    f"고장확정={len(fault_confirmed)}대"
                )

    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()
        logger.info("Consumer 종료!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("종료합니다.")