"""
Edge-Cloud Hybrid PdM Pipeline — Autoencoder 모델
causRCA 이벤트 드리븐 구조 처리 버전
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
import pandas as pd
from pathlib import Path
import logging
import json
import pickle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autoencoder")

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
DATASET_DIR    = "dataset_causRCA/real_op"
MODEL_PATH     = "autoencoder_model.pt"
THRESHOLD_PATH = "threshold.json"
EPOCHS         = 50
BATCH_SIZE     = 32
LEARNING_RATE  = 0.001
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
# 이벤트 드리븐 CSV → 피벗 변환
# ──────────────────────────────────────────────
def load_and_pivot(csv_path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(csv_path).fillna(0)

        # value 컬럼을 숫자로 강제 변환
        # True → 1.0, False → 0.0, 숫자 → 그대로
        df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)

        # 숫자형 value만 있는 행만 사용
        df = df[df["type"] != "Binary"] if "type" in df.columns else df

        if len(df) == 0:
            return pd.DataFrame()

        pivoted = df.pivot_table(
            index="time_s",
            columns="node",
            values="value",
            aggfunc="last"
        ).reset_index(drop=True).fillna(0)

        return pivoted

    except Exception as e:
        logger.warning(f"피벗 실패 {csv_path.name}: {e}")
        return pd.DataFrame()


# ──────────────────────────────────────────────
# 전체 정상 데이터 로드
# ──────────────────────────────────────────────
def load_normal_data():
    path = Path(DATASET_DIR)
    csv_files = sorted(path.glob("*.csv"))
    logger.info(f"정상 데이터 {len(csv_files)}개 로드 중...")

    dfs = []
    for i, f in enumerate(csv_files):
        pivoted = load_and_pivot(f)
        if len(pivoted) > 0 and len(pivoted.columns) > 0:
            dfs.append(pivoted)
        if (i + 1) % 20 == 0:
            logger.info(f"  {i+1}/{len(csv_files)} 처리 중...")

    if not dfs:
        logger.critical("유효한 데이터가 없어요!")
        return None, None

    logger.info(f"유효한 파일 수: {len(dfs)}개")

    # 공통 컬럼만 사용
    common_cols = set(dfs[0].columns)
    for df in dfs[1:]:
        common_cols &= set(df.columns)
    common_cols = sorted(list(common_cols))

    logger.info(f"공통 컬럼 수: {len(common_cols)}개")

    if len(common_cols) == 0:
        logger.critical("공통 컬럼이 없어요!")
        return None, None

    all_data = pd.concat(
        [df[common_cols] for df in dfs],
        ignore_index=True
    ).fillna(0)

    logger.info(f"최종 데이터 크기: {all_data.shape}")
    return all_data, common_cols


# ──────────────────────────────────────────────
# 학습
# ──────────────────────────────────────────────
def train():
    data, columns = load_normal_data()
    if data is None:
        return

    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    data_scaled = scaler.fit_transform(data)

    with open("scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)

    with open("columns.json", "w") as f:
        json.dump(columns, f)

    logger.info(f"컬럼 {len(columns)}개 저장 완료!")

    tensor_data = torch.FloatTensor(data_scaled)
    dataset     = TensorDataset(tensor_data)
    dataloader  = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)

    input_dim = data_scaled.shape[1]
    model     = Autoencoder(input_dim)
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    criterion = nn.MSELoss()

    logger.info(f"오토인코더 학습 시작! 입력 차원: {input_dim}")

    for epoch in range(EPOCHS):
        total_loss = 0
        for batch in dataloader:
            x      = batch[0]
            output = model(x)
            loss   = criterion(output, x)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        avg_loss = total_loss / len(dataloader)
        if (epoch + 1) % 10 == 0:
            logger.info(f"Epoch {epoch+1}/{EPOCHS}  Loss: {avg_loss:.6f}")

    torch.save(model.state_dict(), MODEL_PATH)
    logger.info(f"모델 저장 완료!")

    model.eval()
    with torch.no_grad():
        output = model(tensor_data)
        errors = torch.mean((tensor_data - output) ** 2, dim=1).numpy()

    threshold = float(np.percentile(errors, 95))
    with open(THRESHOLD_PATH, "w") as f:
        json.dump({"threshold": threshold}, f)

    logger.info(f"임계값: {threshold:.6f}")
    logger.info("이 값보다 복원 오차가 크면 → 고장 의심!")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  오토인코더 학습 시작!")
    logger.info("=" * 50)
    train()
    logger.info("=" * 50)
    logger.info("  학습 완료!")
    logger.info("=" * 50)