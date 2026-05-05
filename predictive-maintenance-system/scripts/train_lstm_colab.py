"""
LSTM Autoencoder 재학습 스크립트 (Colab GPU용)
────────────────────────────────────────────────
사용법:
  1. Google Drive에 dataset_causRCA 폴더 업로드
  2. Colab에서 이 스크립트 실행:
     !python train_lstm_colab.py --data_dir /content/drive/MyDrive/dataset_causRCA
  3. 생성된 edge_agent_data.zip 다운로드
  4. edge-agent/models/ 에 .pt, _info.json 복사
  5. anomaly_detection/processed_data_v2/ 에 scaler_info.pkl, meta.json 복사
"""

import argparse
import json
import pickle
import zipfile
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# ── 설정 ──────────────────────────────────────────────────────────────────────
WINDOW = 30
HIDDEN, LATENT, N_LAYERS = 64, 32, 2
EPOCHS = 50
BATCH_SIZE = 64
LR = 1e-3

SUBSYSTEM_FEATURES = {
    'coolant': [
        'CLF_A_700307','CLF_Filter_Ok','CLT_A_700310','CLT_Level_lt_Min',
        'F_A_700313','F_Filter_Ok','HP_A_700304','HP_Pump_Ok','HP_Pump_isOff',
        'LP_A_700301','LP_Pump_Ok','LP_Pump_On','LT_A_700317','LT_Level_Ok','LT_Pump_Ok',
    ],
    'hydraulics': [
        'Hyd_A_700202','Hyd_A_700203','Hyd_A_700204','Hyd_A_700205','Hyd_A_700206',
        'Hyd_A_700207','Hyd_A_700208','Hyd_Filter_Ok','Hyd_IsEnabled','Hyd_Level_Ok',
        'Hyd_Pressure','Hyd_Pump_Ok','Hyd_Pump_On','Hyd_Pump_isOff','Hyd_Temp_lt_70',
        'Hyd_Temp_lt_80','Hyd_Valve_P_Up','Lubr_On','Lubr_P_Ok',
    ],
    'probe': [
        'MPA_A_701124','MPA_A_701125','MPA_InitPos','MPA_WorkPos','MPA_toInitPos',
        'MPA_toWorkPos','MPC_Closed','MPC_close','MPC_isOpen','MPC_open','MP_Inactive',
    ],
}

SUBSYSTEM_DIR = {
    'coolant':    'exp_coolant',
    'hydraulics': 'exp_hydraulics',
    'probe':      'exp_probe',
}


# ── 모델 아키텍처 ────────────────────────────────────────────────────────────
class LSTMEncoder(nn.Module):
    def __init__(self, n_feat):
        super().__init__()
        self.lstm = nn.LSTM(n_feat, HIDDEN, N_LAYERS, batch_first=True, dropout=0.1)
        self.fc = nn.Linear(HIDDEN, LATENT)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


class LSTMDecoder(nn.Module):
    def __init__(self, n_feat):
        super().__init__()
        self.fc = nn.Linear(LATENT, HIDDEN)
        self.lstm = nn.LSTM(HIDDEN, HIDDEN, N_LAYERS, batch_first=True, dropout=0.1)
        self.out = nn.Linear(HIDDEN, n_feat)

    def forward(self, z):
        h = self.fc(z).unsqueeze(1).repeat(1, WINDOW, 1)
        o, _ = self.lstm(h)
        return torch.sigmoid(self.out(o))


class LSTMAutoencoder(nn.Module):
    def __init__(self, n_feat):
        super().__init__()
        self.encoder = LSTMEncoder(n_feat)
        self.decoder = LSTMDecoder(n_feat)

    def forward(self, x):
        return self.decoder(self.encoder(x))


# ── 유틸리티 ──────────────────────────────────────────────────────────────────
def _b2f(v):
    s = str(v).strip().lower()
    if s == 'true': return 1.0
    if s == 'false': return 0.0
    try:
        return float(s)
    except:
        return 0.0


def load_wide(csv_path, features):
    df = pd.read_csv(csv_path)
    wide = df.pivot_table(index='time_s', columns='node', values='value', aggfunc='last')
    wide = wide.reindex(columns=features)
    return wide.map(_b2f).fillna(0.0)


def make_windows(arr, w=WINDOW):
    return np.stack([arr[i:i+w] for i in range(len(arr) - w + 1)]).astype(np.float32)


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str, default='/content/drive/MyDrive/dataset_causRCA',
                        help='CausRCA 데이터셋 루트 디렉터리 (dig_twin, real_op 포함)')
    parser.add_argument('--out_dir', type=str, default='/content/lstm_output',
                        help='출력 디렉터리')
    parser.add_argument('--epochs', type=int, default=EPOCHS)
    args = parser.parse_args()

    DATASET_DIR = Path(args.data_dir)
    DIG_TWIN = DATASET_DIR / 'dig_twin'
    OUT_DIR = Path(args.out_dir)
    PROC_DIR = OUT_DIR / 'processed_data_v2'
    LSTM_DIR = OUT_DIR / 'models_lstm'
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    LSTM_DIR.mkdir(parents=True, exist_ok=True)

    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {DEVICE}')
    print(f'Dataset: {DATASET_DIR}')

    # ── [E-1] 전체 피처 목록 수집 ────────────────────────────────────────────
    print('\n[1/4] 피처 목록 수집...')
    all_nodes = set()
    for sub, folder in SUBSYSTEM_DIR.items():
        sub_path = DIG_TWIN / folder
        if not sub_path.exists():
            print(f'  경고: {sub_path} 없음')
            continue
        for exp_dir in sub_path.iterdir():
            if not exp_dir.is_dir(): continue
            for run_dir in exp_dir.iterdir():
                if not run_dir.is_dir(): continue
                for csv_f in run_dir.glob('faultDataset_*.csv'):
                    df = pd.read_csv(csv_f)
                    if 'node' in df.columns:
                        all_nodes.update(df['node'].unique())

    ALL_FEATURES = sorted(all_nodes)
    N_FEAT = len(ALL_FEATURES)
    print(f'  전체 피처 수: {N_FEAT}')

    # ── [E-2] scaler 생성 ────────────────────────────────────────────────────
    print('\n[2/4] scaler + meta 생성...')
    all_normal = []
    for sub, folder in SUBSYSTEM_DIR.items():
        sub_path = DIG_TWIN / folder
        if not sub_path.exists(): continue
        for exp_dir in sorted(sub_path.iterdir()):
            if not exp_dir.is_dir(): continue
            for run_dir in sorted(exp_dir.iterdir()):
                if not run_dir.is_dir(): continue
                csv_files = list(run_dir.glob('faultDataset_*.csv'))
                cause_files = list(run_dir.glob('causes.json'))
                if not csv_files or not cause_files: continue
                causes = json.loads(cause_files[0].read_text())
                cause_start = causes.get('cause_start_at', 0.0)
                wide = load_wide(csv_files[0], ALL_FEATURES)
                normal = wide[wide.index < cause_start]
                if len(normal) >= 5:
                    all_normal.append(normal)

    normal_all = pd.concat(all_normal, ignore_index=True).values.astype(np.float32)
    col_min = normal_all.min(axis=0)
    col_range = normal_all.max(axis=0) - col_min
    col_range[col_range == 0] = 1.0

    with open(PROC_DIR / 'scaler_info.pkl', 'wb') as f:
        pickle.dump({'min': col_min, 'range': col_range}, f)
    print(f'  scaler_info.pkl 저장 (shape: {col_min.shape})')

    feat_idx = {n: i for i, n in enumerate(ALL_FEATURES)}
    subsystem_info = {}
    for sub, feats in SUBSYSTEM_FEATURES.items():
        valid = [f for f in feats if f in feat_idx]
        subsystem_info[sub] = {'features': valid, 'indices': [feat_idx[f] for f in valid]}

    meta = {'feature_names': ALL_FEATURES, 'subsystem_info': subsystem_info}
    with open(PROC_DIR / 'meta.json', 'w') as f:
        json.dump(meta, f, indent=2)
    print('  meta.json 저장')
    for sub, info in subsystem_info.items():
        print(f'    {sub}: {len(info["features"])}개 피처')

    # ── [E-3] LSTM AE 학습 ───────────────────────────────────────────────────
    print(f'\n[3/4] LSTM Autoencoder 학습 (epochs={args.epochs})...')

    for subsystem in ['coolant', 'hydraulics', 'probe']:
        indices = subsystem_info[subsystem]['indices']
        n_feat = len(indices)
        print(f'\n  [{subsystem}] n_feat={n_feat}')

        wins = []
        for sub, folder in SUBSYSTEM_DIR.items():
            sub_path = DIG_TWIN / folder
            if not sub_path.exists(): continue
            for exp_dir in sorted(sub_path.iterdir()):
                if not exp_dir.is_dir(): continue
                for run_dir in sorted(exp_dir.iterdir()):
                    if not run_dir.is_dir(): continue
                    csv_files = list(run_dir.glob('faultDataset_*.csv'))
                    cause_files = list(run_dir.glob('causes.json'))
                    if not csv_files or not cause_files: continue
                    causes = json.loads(cause_files[0].read_text())
                    cause_start = causes.get('cause_start_at', 0.0)
                    wide = load_wide(csv_files[0], ALL_FEATURES)
                    normal = wide[wide.index < cause_start]
                    if len(normal) < WINDOW + 1:
                        continue
                    arr = normal.values.astype(np.float32)
                    arr = np.clip((arr - col_min) / col_range, 0.0, 1.0)
                    arr = arr[:, indices]
                    wins.append(make_windows(arr))

        if not wins:
            print('    데이터 없음 — 스킵')
            continue

        X = torch.tensor(np.concatenate(wins))
        loader = DataLoader(TensorDataset(X), batch_size=BATCH_SIZE, shuffle=True)
        print(f'    학습 윈도우: {len(X)}개')

        model = LSTMAutoencoder(n_feat).to(DEVICE)
        opt = torch.optim.Adam(model.parameters(), lr=LR)
        sch = torch.optim.lr_scheduler.StepLR(opt, step_size=20, gamma=0.5)

        model.train()
        for ep in range(1, args.epochs + 1):
            total = 0.0
            for (xb,) in loader:
                xb = xb.to(DEVICE)
                opt.zero_grad()
                loss = ((model(xb) - xb) ** 2).mean()
                loss.backward()
                opt.step()
                total += loss.item() * len(xb)
            sch.step()
            if ep % 10 == 0 or ep == 1:
                print(f'    epoch {ep:3d}/{args.epochs}  mse={total/len(X):.6f}')

        # Threshold 계산
        model.eval()
        errors = []
        with torch.no_grad():
            for (xb,) in DataLoader(TensorDataset(X), batch_size=256):
                xb = xb.to(DEVICE)
                mse = ((model(xb) - xb) ** 2).mean(dim=(1, 2)).cpu().numpy()
                errors.extend(mse.tolist())
        thr = float(np.mean(errors) + 3 * np.std(errors))

        # 저장
        # state_dict 키를 edge-agent의 LSTMAutoencoder와 맞춤
        torch.save(model.state_dict(), LSTM_DIR / f'{subsystem}_lstm.pt')
        info = {
            'n_features': n_feat,
            'hidden_size': HIDDEN,
            'latent_dim': LATENT,
            'n_layers': N_LAYERS,
            'window_size': WINDOW,
            'threshold_3sigma': thr,
            'train_windows': int(len(X)),
            'train_error_mean': float(np.mean(errors)),
            'train_error_std': float(np.std(errors)),
        }
        with open(LSTM_DIR / f'{subsystem}_lstm_info.json', 'w') as f:
            json.dump(info, f, indent=2)
        print(f'    → threshold_3sigma={thr:.6f}  (mean={np.mean(errors):.6f}, std={np.std(errors):.6f})')

    # ── [E-4] 압축 ──────────────────────────────────────────────────────────
    print('\n[4/4] 압축...')
    zip_path = OUT_DIR / 'edge_agent_data.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in PROC_DIR.iterdir():
            zf.write(f, f'processed_data_v2/{f.name}')
        for f in LSTM_DIR.iterdir():
            zf.write(f, f'models_lstm/{f.name}')

    print(f'\n완료! 출력 파일:')
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            print(f'  {name}  ({zf.getinfo(name).file_size/1024:.1f} KB)')
    print(f'\n→ {zip_path}')
    print('\n배포 방법:')
    print('  1. models_lstm/*.pt, *_info.json → edge-agent/models/')
    print('  2. processed_data_v2/* → anomaly_detection/processed_data_v2/')


if __name__ == '__main__':
    main()
