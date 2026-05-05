"""
04_lstm_autoencoder.py - Pure Numpy LSTM Autoencoder for Unsupervised Anomaly Detection
Trains on normal data only, detects anomalies via reconstruction error.
"""

import os, json, csv, warnings
from pathlib import Path
from collections import defaultdict
import numpy as np

warnings.filterwarnings("ignore")

# ---- paths ----
BASE = Path(__file__).resolve().parent.parent
DATASET = BASE / "dataset_causRCA"
NORMAL_DIR = DATASET / "real_op"
DIG_TWIN = DATASET / "dig_twin"
OUT_DIR = Path(__file__).resolve().parent
MODEL_DIR = OUT_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

SUBSYSTEMS = {
    1: ("coolant",    DIG_TWIN / "exp_coolant"),
    2: ("hydraulics", DIG_TWIN / "exp_hydraulics"),
    3: ("probe",      DIG_TWIN / "exp_probe"),
}

# ---- 59 features ----
NUMERIC_FEATURES = [
    "Hyd_Pressure", "M_PowerOnDuration",
    "Prog_CuttingTime", "Prog_CycleTime", "Prog_LineNo",
    "Spd_ActPos_C", "Spd_ActPos_X", "Spd_ActPos_Z",
    "Spd_ActSpeed_C", "Spd_ActSpeed_X", "Spd_ActSpeed_Z",
]
ALARM_FEATURES = [
    "CLF_A_700307", "CLT_A_700310", "F_A_700313",
    "HP_A_700304", "LP_A_700301", "LT_A_700317",
    "Hyd_A_700202", "Hyd_A_700203", "Hyd_A_700204",
    "Hyd_A_700205", "Hyd_A_700206", "Hyd_A_700207", "Hyd_A_700208",
    "MPA_A_701124", "MPA_A_701125",
    "Prog_A_701330", "SR_A_67040", "T_A_701309",
]
BINARY_FEATURES = [
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
ALL_FEATURES = NUMERIC_FEATURES + ALARM_FEATURES + BINARY_FEATURES


# ---- CSV parsing (reused from 03) ----
def parse_csv_long(csv_path, features=None):
    if features is None:
        features = ALL_FEATURES
    feature_set = set(features)
    with open(csv_path, "r") as f:
        header_line = f.readline().strip().replace("\r", "")
        cols = [c.strip() for c in header_line.split(",")]
    is_long = (cols[:4] == ["time_s", "node", "value", "type"])
    if not is_long:
        # wide format
        rows_list = []
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [fn.strip().replace("\r","") for fn in reader.fieldnames]
            for row in reader:
                r = {}
                try:
                    r["time_s"] = float(row.get("time_s","0").strip().replace("\r",""))
                except:
                    continue
                for feat in features:
                    val_str = row.get(feat, "0")
                    if val_str is None:
                        val_str = "0"
                    val_str = str(val_str).strip().replace("\r","")
                    if val_str in ("True","true"): r[feat] = 1.0
                    elif val_str in ("False","false"): r[feat] = 0.0
                    else:
                        try: r[feat] = float(val_str)
                        except: r[feat] = 0.0
                rows_list.append(r)
        if not rows_list:
            return np.zeros((0, len(features))), np.array([])
        times = np.array([r["time_s"] for r in rows_list])
        data = np.array([[r.get(f, 0.0) for f in features] for r in rows_list])
        return data, times

    data = defaultdict(dict)
    timestamps = set()
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [fn.strip().replace("\r","") for fn in reader.fieldnames]
        for row in reader:
            node = row.get("node","").strip().replace("\r","")
            if node not in feature_set:
                continue
            try:
                t = float(row["time_s"].strip().replace("\r",""))
            except:
                continue
            val_str = row.get("value","0").strip().replace("\r","")
            if val_str in ("True","true"): val = 1.0
            elif val_str in ("False","false"): val = 0.0
            else:
                try: val = float(val_str)
                except: val = 0.0
            data[t][node] = val
            timestamps.add(t)
    if not timestamps:
        return np.zeros((0, len(features))), np.array([])
    sorted_times = sorted(timestamps)
    current = {f: 0.0 for f in features}
    rows = []
    for t in sorted_times:
        current.update(data[t])
        rows.append([current.get(f, 0.0) for f in features])
    return np.array(rows, dtype=np.float32), np.array(sorted_times)


def sample_indices(n, k=10):
    if n <= k:
        return list(range(n))
    return np.linspace(0, n-1, k, dtype=int).tolist()


# ---- LSTM cell (pure numpy) ----
def sigmoid(x):
    x = np.clip(x, -500, 500)
    return 1.0 / (1.0 + np.exp(-x))

def tanh(x):
    return np.tanh(x)

def sigmoid_deriv(s):
    return s * (1 - s)

def tanh_deriv(t):
    return 1 - t ** 2


class LSTMCell:
    """Single LSTM cell with input, forget, output gates."""
    def __init__(self, input_size, hidden_size, rng):
        scale = 0.1
        self.hidden_size = hidden_size
        # Combined weights: [f, i, g, o] gates
        self.Wf = rng.randn(input_size + hidden_size, hidden_size).astype(np.float32) * scale
        self.bf = np.zeros(hidden_size, dtype=np.float32)
        self.Wi = rng.randn(input_size + hidden_size, hidden_size).astype(np.float32) * scale
        self.bi = np.zeros(hidden_size, dtype=np.float32)
        self.Wg = rng.randn(input_size + hidden_size, hidden_size).astype(np.float32) * scale
        self.bg = np.zeros(hidden_size, dtype=np.float32)
        self.Wo = rng.randn(input_size + hidden_size, hidden_size).astype(np.float32) * scale
        self.bo = np.zeros(hidden_size, dtype=np.float32)

    def forward(self, x_seq, h0=None, c0=None):
        """x_seq: (seq_len, input_size). Returns h_seq, (h_last, c_last), cache."""
        seq_len, input_size = x_seq.shape
        hs = self.hidden_size
        if h0 is None: h0 = np.zeros(hs, dtype=np.float32)
        if c0 is None: c0 = np.zeros(hs, dtype=np.float32)

        h_seq = np.zeros((seq_len, hs), dtype=np.float32)
        cache = []
        h_prev, c_prev = h0, c0

        for t in range(seq_len):
            x = x_seq[t]
            concat = np.concatenate([x, h_prev])
            f = sigmoid(concat @ self.Wf + self.bf)
            i = sigmoid(concat @ self.Wi + self.bi)
            g = tanh(concat @ self.Wg + self.bg)
            o = sigmoid(concat @ self.Wo + self.bo)
            c = f * c_prev + i * g
            h = o * tanh(c)
            cache.append((x, h_prev, c_prev, concat, f, i, g, o, c, h))
            h_prev, c_prev = h, c
            h_seq[t] = h

        return h_seq, (h_prev, c_prev), cache

    def backward(self, dh_seq, cache, lr=0.001):
        """Backprop through time. dh_seq: (seq_len, hidden_size)."""
        seq_len = len(cache)
        hs = self.hidden_size

        dWf = np.zeros_like(self.Wf)
        dWi = np.zeros_like(self.Wi)
        dWg = np.zeros_like(self.Wg)
        dWo = np.zeros_like(self.Wo)
        dbf = np.zeros_like(self.bf)
        dbi = np.zeros_like(self.bi)
        dbg = np.zeros_like(self.bg)
        dbo = np.zeros_like(self.bo)

        dh_next = np.zeros(hs, dtype=np.float32)
        dc_next = np.zeros(hs, dtype=np.float32)

        for t in reversed(range(seq_len)):
            x, h_prev, c_prev, concat, f, i, g, o, c, h = cache[t]
            dh = dh_seq[t] + dh_next

            do = dh * tanh(c)
            dc = dh * o * tanh_deriv(tanh(c)) + dc_next

            df = dc * c_prev
            di = dc * g
            dg = dc * i

            df_raw = df * sigmoid_deriv(f)
            di_raw = di * sigmoid_deriv(i)
            dg_raw = dg * tanh_deriv(g)
            do_raw = do * sigmoid_deriv(o)

            dWf += np.outer(concat, df_raw)
            dWi += np.outer(concat, di_raw)
            dWg += np.outer(concat, dg_raw)
            dWo += np.outer(concat, do_raw)
            dbf += df_raw
            dbi += di_raw
            dbg += dg_raw
            dbo += do_raw

            d_concat = (df_raw @ self.Wf.T + di_raw @ self.Wi.T +
                        dg_raw @ self.Wg.T + do_raw @ self.Wo.T)
            dh_next = d_concat[x.shape[0]:]
            dc_next = dc * f

        # Gradient clipping
        for dW in [dWf, dWi, dWg, dWo, dbf, dbi, dbg, dbo]:
            np.clip(dW, -5, 5, out=dW)

        self.Wf -= lr * dWf
        self.Wi -= lr * dWi
        self.Wg -= lr * dWg
        self.Wo -= lr * dWo
        self.bf -= lr * dbf
        self.bi -= lr * dbi
        self.bg -= lr * dbg
        self.bo -= lr * dbo


class LSTMAutoencoder:
    """LSTM Autoencoder: encoder LSTM -> bottleneck -> decoder LSTM -> output."""
    def __init__(self, input_size, hidden_size=16, rng=None):
        if rng is None:
            rng = np.random.RandomState(42)
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.encoder = LSTMCell(input_size, hidden_size, rng)
        self.decoder = LSTMCell(hidden_size, hidden_size, rng)
        # Output projection
        scale = 0.1
        self.W_out = rng.randn(hidden_size, input_size).astype(np.float32) * scale
        self.b_out = np.zeros(input_size, dtype=np.float32)

    def forward(self, x_seq):
        """x_seq: (seq_len, input_size). Returns reconstructed sequence and caches."""
        seq_len = x_seq.shape[0]
        # Encode
        enc_h_seq, (h_last, c_last), enc_cache = self.encoder.forward(x_seq)

        # Decoder input: repeat last hidden state
        dec_input = np.tile(h_last, (seq_len, 1))
        dec_h_seq, _, dec_cache = self.decoder.forward(dec_input, h_last, c_last)

        # Output projection
        recon = dec_h_seq @ self.W_out + self.b_out
        return recon, enc_cache, dec_cache, dec_h_seq, h_last, c_last

    def train_step(self, x_seq, lr=0.001):
        """One training step. Returns MSE loss."""
        seq_len = x_seq.shape[0]
        recon, enc_cache, dec_cache, dec_h_seq, h_last, c_last = self.forward(x_seq)

        # Loss: MSE
        diff = recon - x_seq
        loss = np.mean(diff ** 2)

        # Backward through output projection
        d_recon = 2.0 * diff / (seq_len * self.input_size)
        d_dec_h = d_recon @ self.W_out.T

        # Update output projection
        dW_out = dec_h_seq.T @ d_recon
        db_out = d_recon.sum(axis=0)
        np.clip(dW_out, -5, 5, out=dW_out)
        np.clip(db_out, -5, 5, out=db_out)
        self.W_out -= lr * dW_out
        self.b_out -= lr * db_out

        # Backward through decoder
        self.decoder.backward(d_dec_h, dec_cache, lr)

        # Backward through encoder (approximate: pass gradient from decoder's initial state)
        dh_enc = np.zeros((seq_len, self.hidden_size), dtype=np.float32)
        dh_enc[-1] = d_dec_h.sum(axis=0) * 0.1  # scaled gradient
        self.encoder.backward(dh_enc, enc_cache, lr)

        return loss

    def reconstruction_error(self, x_seq):
        """Per-sample MSE reconstruction error."""
        recon, *_ = self.forward(x_seq)
        return np.mean((recon - x_seq) ** 2, axis=1).mean()

    def save(self, path):
        np.savez(path,
                 enc_Wf=self.encoder.Wf, enc_bf=self.encoder.bf,
                 enc_Wi=self.encoder.Wi, enc_bi=self.encoder.bi,
                 enc_Wg=self.encoder.Wg, enc_bg=self.encoder.bg,
                 enc_Wo=self.encoder.Wo, enc_bo=self.encoder.bo,
                 dec_Wf=self.decoder.Wf, dec_bf=self.decoder.bf,
                 dec_Wi=self.decoder.Wi, dec_bi=self.decoder.bi,
                 dec_Wg=self.decoder.Wg, dec_bg=self.decoder.bg,
                 dec_Wo=self.decoder.Wo, dec_bo=self.decoder.bo,
                 W_out=self.W_out, b_out=self.b_out,
                 input_size=self.input_size, hidden_size=self.hidden_size)


# ---- Data loading ----
def load_normal_sequences(max_files=None, window=10):
    csv_files = sorted(NORMAL_DIR.glob("*.csv"))
    if max_files:
        csv_files = csv_files[:max_files]
    print(f"Loading {len(csv_files)} normal files for LSTM AE...")
    sequences = []
    for i, f in enumerate(csv_files):
        if (i+1) % 50 == 0:
            print(f"  ...{i+1}/{len(csv_files)}")
        data, times = parse_csv_long(f)
        if len(data) < window:
            # Pad if too short
            if len(data) > 0:
                pad = np.tile(data[-1:], (window - len(data), 1))
                data = np.vstack([data, pad])
            else:
                continue
        # Sample window-sized subsequences
        idxs = sample_indices(len(data), k=max(1, len(data) // window))
        for idx in idxs:
            end = min(idx + window, len(data))
            seq = data[idx:end]
            if len(seq) < window:
                pad = np.tile(seq[-1:], (window - len(seq), 1))
                seq = np.vstack([seq, pad])
            sequences.append(seq)
    return sequences


def load_fault_sequences(label, name, path, window=10):
    sequences = []
    for exp_dir in sorted(path.iterdir()):
        if not exp_dir.is_dir():
            continue
        for run_dir in sorted(exp_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            causes_file = run_dir / "causes.json"
            if not causes_file.exists():
                continue
            with open(causes_file) as fh:
                causes = json.load(fh)
            fault_start = causes.get("cause_start_at", 0)
            for cf in run_dir.glob("*.csv"):
                data, times = parse_csv_long(cf)
                if len(data) == 0:
                    continue
                # Get fault portion
                mask = times >= fault_start
                if mask.sum() > 0:
                    fault_data = data[mask]
                else:
                    fault_data = data
                if len(fault_data) < window:
                    if len(fault_data) > 0:
                        pad = np.tile(fault_data[-1:], (window - len(fault_data), 1))
                        fault_data = np.vstack([fault_data, pad])
                    else:
                        continue
                idxs = sample_indices(len(fault_data), k=max(1, len(fault_data) // window))
                for idx in idxs:
                    end = min(idx + window, len(fault_data))
                    seq = fault_data[idx:end]
                    if len(seq) < window:
                        pad = np.tile(seq[-1:], (window - len(seq), 1))
                        seq = np.vstack([seq, pad])
                    sequences.append(seq)
    return sequences


def main():
    WINDOW = 10
    HIDDEN = 16
    EPOCHS = 30
    LR = 0.001

    # 1. Load normal sequences
    normal_seqs = load_normal_sequences(window=WINDOW)
    print(f"Normal sequences: {len(normal_seqs)}")

    if len(normal_seqs) == 0:
        print("ERROR: No normal sequences loaded")
        return

    # Normalize: compute mean/std from normal data
    all_normal = np.vstack(normal_seqs)
    feat_mean = all_normal.mean(axis=0)
    feat_std = all_normal.std(axis=0) + 1e-8
    normal_seqs_norm = [(s - feat_mean) / feat_std for s in normal_seqs]

    # 2. Build and train LSTM AE
    input_size = len(ALL_FEATURES)
    print(f"\nTraining LSTM Autoencoder: input={input_size}, hidden={HIDDEN}, epochs={EPOCHS}")
    model = LSTMAutoencoder(input_size, HIDDEN, rng=np.random.RandomState(42))

    # Train on subset for speed
    rng = np.random.RandomState(42)
    train_seqs = normal_seqs_norm
    if len(train_seqs) > 500:
        idx = rng.choice(len(train_seqs), 500, replace=False)
        train_seqs = [normal_seqs_norm[i] for i in idx]

    for epoch in range(EPOCHS):
        losses = []
        rng.shuffle(train_seqs)
        for seq in train_seqs:
            loss = model.train_step(seq.astype(np.float32), lr=LR)
            losses.append(loss)
        avg_loss = np.mean(losses)
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"  Epoch {epoch+1}/{EPOCHS} - Loss: {avg_loss:.6f}")

    # 3. Compute threshold from normal data
    normal_errors = []
    for seq in normal_seqs_norm:
        err = model.reconstruction_error(seq.astype(np.float32))
        normal_errors.append(err)
    normal_errors = np.array(normal_errors)
    threshold_95 = np.percentile(normal_errors, 95)
    threshold_99 = np.percentile(normal_errors, 99)
    print(f"\nNormal reconstruction errors: mean={normal_errors.mean():.6f}, "
          f"std={normal_errors.std():.6f}")
    print(f"Threshold (95th): {threshold_95:.6f}")
    print(f"Threshold (99th): {threshold_99:.6f}")

    # 4. Test on fault data
    print(f"\n{'='*60}")
    print("ANOMALY DETECTION RESULTS")
    print(f"{'='*60}")

    results = {
        "normal_error_mean": float(normal_errors.mean()),
        "normal_error_std": float(normal_errors.std()),
        "threshold_95": float(threshold_95),
        "threshold_99": float(threshold_99),
        "subsystem_results": {},
    }

    for label, (name, path) in SUBSYSTEMS.items():
        fault_seqs = load_fault_sequences(label, name, path, window=WINDOW)
        if not fault_seqs:
            print(f"  {name}: no sequences loaded")
            continue
        fault_seqs_norm = [(s - feat_mean) / feat_std for s in fault_seqs]
        fault_errors = []
        for seq in fault_seqs_norm:
            err = model.reconstruction_error(seq.astype(np.float32))
            fault_errors.append(err)
        fault_errors = np.array(fault_errors)

        det_95 = (fault_errors > threshold_95).mean() * 100
        det_99 = (fault_errors > threshold_99).mean() * 100
        print(f"\n  {name} ({len(fault_seqs)} sequences):")
        print(f"    Error: mean={fault_errors.mean():.6f}, std={fault_errors.std():.6f}")
        print(f"    Detection rate (95th): {det_95:.1f}%")
        print(f"    Detection rate (99th): {det_99:.1f}%")

        results["subsystem_results"][name] = {
            "n_sequences": len(fault_seqs),
            "error_mean": float(fault_errors.mean()),
            "error_std": float(fault_errors.std()),
            "detection_rate_95": float(det_95),
            "detection_rate_99": float(det_99),
        }

    # Also test normal detection (false positive rate)
    fp_95 = (normal_errors > threshold_95).mean() * 100
    fp_99 = (normal_errors > threshold_99).mean() * 100
    print(f"\n  Normal (false positives):")
    print(f"    FP rate (95th): {fp_95:.1f}%")
    print(f"    FP rate (99th): {fp_99:.1f}%")
    results["false_positive_95"] = float(fp_95)
    results["false_positive_99"] = float(fp_99)

    # 5. Save
    model.save(str(MODEL_DIR / "lstm_ae_weights.npz"))
    print(f"\nModel saved: {MODEL_DIR / 'lstm_ae_weights.npz'}")

    # Save normalization params
    np.savez(str(MODEL_DIR / "lstm_ae_norm.npz"), mean=feat_mean, std=feat_std)

    results_path = OUT_DIR / "lstm_ae_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved: {results_path}")


if __name__ == "__main__":
    main()
