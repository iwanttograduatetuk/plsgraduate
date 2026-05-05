"""
06_resnet1d_train.py - Pure Numpy 1D-ResNet for Multi-class Fault Classification
Implements 1D convolutions with residual connections, trained with SGD.
Compares results with XGBoost/ensemble baseline.
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
CLASS_NAMES = {0: "normal", 1: "coolant_fault", 2: "hydraulics_fault", 3: "probe_fault"}

# ---- features ----
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


# ---- CSV parsing ----
def parse_csv_auto(csv_path, features=None):
    if features is None:
        features = ALL_FEATURES
    feature_set = set(features)
    with open(csv_path, "r") as f:
        header_line = f.readline().strip().replace("\r", "")
        cols = [c.strip() for c in header_line.split(",")]
    is_long = (cols[:4] == ["time_s", "node", "value", "type"])
    if is_long:
        data = defaultdict(dict)
        timestamps = set()
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [fn.strip().replace("\r","") for fn in reader.fieldnames]
            for row in reader:
                node = row.get("node","").strip().replace("\r","")
                if node not in feature_set: continue
                try: t = float(row["time_s"].strip().replace("\r",""))
                except: continue
                val_str = row.get("value","0").strip().replace("\r","")
                if val_str in ("True","true"): val = 1.0
                elif val_str in ("False","false"): val = 0.0
                else:
                    try: val = float(val_str)
                    except: val = 0.0
                data[t][node] = val
                timestamps.add(t)
        if not timestamps:
            return np.zeros((0, len(features)))
        sorted_times = sorted(timestamps)
        current = {f: 0.0 for f in features}
        rows = []
        for t in sorted_times:
            current.update(data[t])
            rows.append([current.get(f, 0.0) for f in features])
        return np.array(rows, dtype=np.float32)
    else:
        rows_list = []
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [fn.strip().replace("\r","") for fn in reader.fieldnames]
            for row in reader:
                r = []
                for feat in features:
                    val_str = row.get(feat, "0")
                    if val_str is None: val_str = "0"
                    val_str = str(val_str).strip().replace("\r","")
                    if val_str in ("True","true"): r.append(1.0)
                    elif val_str in ("False","false"): r.append(0.0)
                    else:
                        try: r.append(float(val_str))
                        except: r.append(0.0)
                rows_list.append(r)
        if not rows_list:
            return np.zeros((0, len(features)))
        return np.array(rows_list, dtype=np.float32)


def sample_snapshots(data, n=10):
    if len(data) <= n: return data
    idx = np.linspace(0, len(data)-1, n, dtype=int)
    return data[idx]


def load_all_data():
    all_X, all_y = [], []
    csv_files = sorted(NORMAL_DIR.glob("*.csv"))
    print(f"Loading {len(csv_files)} normal files...")
    for i, f in enumerate(csv_files):
        if (i+1) % 50 == 0: print(f"  ...{i+1}/{len(csv_files)}")
        data = parse_csv_auto(f)
        if len(data) > 0:
            sampled = sample_snapshots(data, 10)
            all_X.append(sampled)
            all_y.extend([0] * len(sampled))
    for label, (name, path) in SUBSYSTEMS.items():
        for exp_dir in sorted(path.iterdir()):
            if not exp_dir.is_dir(): continue
            for run_dir in sorted(exp_dir.iterdir()):
                if not run_dir.is_dir(): continue
                causes_file = run_dir / "causes.json"
                if not causes_file.exists(): continue
                with open(causes_file) as fh:
                    causes = json.load(fh)
                fault_start = causes.get("cause_start_at", 0)
                for cf in run_dir.glob("*.csv"):
                    data = parse_csv_auto(cf)
                    if len(data) == 0: continue
                    sampled = sample_snapshots(data, 10)
                    all_X.append(sampled)
                    all_y.extend([label] * len(sampled))
        print(f"  {name} loaded")
    X = np.vstack(all_X)
    y = np.array(all_y)
    return X, y


def stratified_split(X, y, test_size=0.2, random_state=42):
    rng = np.random.RandomState(random_state)
    classes = np.unique(y)
    train_idx, test_idx = [], []
    for c in classes:
        c_idx = np.where(y == c)[0]
        rng.shuffle(c_idx)
        n_test = max(1, int(len(c_idx) * test_size))
        test_idx.extend(c_idx[:n_test])
        train_idx.extend(c_idx[n_test:])
    train_idx = np.array(train_idx)
    test_idx = np.array(test_idx)
    rng.shuffle(train_idx)
    rng.shuffle(test_idx)
    return X[train_idx], X[test_idx], y[train_idx], y[test_idx]


# ---- 1D-ResNet building blocks ----
def relu(x):
    return np.maximum(0, x)

def relu_deriv(x):
    return (x > 0).astype(np.float32)

def softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)

def cross_entropy_loss(probs, y):
    n = len(y)
    log_probs = np.log(probs[np.arange(n), y] + 1e-10)
    return -np.mean(log_probs)

def one_hot(y, n_classes):
    oh = np.zeros((len(y), n_classes), dtype=np.float32)
    oh[np.arange(len(y)), y] = 1
    return oh


class Conv1DLayer:
    """1D convolution: input (batch, in_channels) -> output (batch, out_channels).
    Since our data is single-timestep (flat), this is effectively a dense layer
    that can be viewed as 1x1 conv on a length-1 sequence."""
    def __init__(self, in_ch, out_ch, rng):
        # He initialization
        self.W = rng.randn(in_ch, out_ch).astype(np.float32) * np.sqrt(2.0 / in_ch)
        self.b = np.zeros(out_ch, dtype=np.float32)
        self.dW = None
        self.db = None
        self.x_cache = None

    def forward(self, x):
        self.x_cache = x
        return x @ self.W + self.b

    def backward(self, dout):
        self.dW = self.x_cache.T @ dout / len(dout)
        self.db = dout.mean(axis=0)
        dx = dout @ self.W.T
        return dx

    def update(self, lr):
        np.clip(self.dW, -1, 1, out=self.dW)
        np.clip(self.db, -1, 1, out=self.db)
        self.W -= lr * self.dW
        self.b -= lr * self.db


class BatchNorm1D:
    """Simple batch normalization."""
    def __init__(self, n_features):
        self.gamma = np.ones(n_features, dtype=np.float32)
        self.beta = np.zeros(n_features, dtype=np.float32)
        self.eps = 1e-5
        self.cache = None
        self.dgamma = None
        self.dbeta = None

    def forward(self, x, training=True):
        if training:
            mu = x.mean(axis=0)
            var = x.var(axis=0)
            x_norm = (x - mu) / np.sqrt(var + self.eps)
            self.cache = (x, x_norm, mu, var)
            return self.gamma * x_norm + self.beta
        else:
            mu = x.mean(axis=0)
            var = x.var(axis=0)
            x_norm = (x - mu) / np.sqrt(var + self.eps)
            return self.gamma * x_norm + self.beta

    def backward(self, dout):
        x, x_norm, mu, var = self.cache
        N = x.shape[0]
        self.dgamma = (dout * x_norm).sum(axis=0)
        self.dbeta = dout.sum(axis=0)
        dx_norm = dout * self.gamma
        dvar = (-0.5 * dx_norm * (x - mu) * (var + self.eps) ** (-1.5)).sum(axis=0)
        dmu = (-dx_norm / np.sqrt(var + self.eps)).sum(axis=0)
        dx = dx_norm / np.sqrt(var + self.eps) + 2 * dvar * (x - mu) / N + dmu / N
        return dx

    def update(self, lr):
        self.gamma -= lr * self.dgamma
        self.beta -= lr * self.dbeta


class ResBlock:
    """Residual block: conv -> bn -> relu -> conv -> bn + skip -> relu"""
    def __init__(self, channels, rng):
        self.conv1 = Conv1DLayer(channels, channels, rng)
        self.bn1 = BatchNorm1D(channels)
        self.conv2 = Conv1DLayer(channels, channels, rng)
        self.bn2 = BatchNorm1D(channels)
        self.cache = {}

    def forward(self, x, training=True):
        self.cache["input"] = x
        h = self.conv1.forward(x)
        h = self.bn1.forward(h, training)
        self.cache["pre_relu1"] = h
        h = relu(h)
        self.cache["post_relu1"] = h
        h = self.conv2.forward(h)
        h = self.bn2.forward(h, training)
        self.cache["pre_skip"] = h
        h = h + x  # skip connection
        self.cache["pre_relu2"] = h
        h = relu(h)
        return h

    def backward(self, dout):
        dout = dout * relu_deriv(self.cache["pre_relu2"])
        d_skip = dout  # gradient through skip
        d_main = dout
        d_main = self.bn2.backward(d_main)
        d_main = self.conv2.backward(d_main)
        d_main = d_main * relu_deriv(self.cache["pre_relu1"])
        d_main = self.bn1.backward(d_main)
        d_main = self.conv1.backward(d_main)
        return d_main + d_skip  # sum gradients

    def update(self, lr):
        self.conv1.update(lr)
        self.conv2.update(lr)
        self.bn1.update(lr)
        self.bn2.update(lr)


class ResNet1D:
    """Simple 1D-ResNet: input projection -> 2 res blocks -> global pool -> classifier."""
    def __init__(self, input_size, hidden_size=32, n_blocks=2, n_classes=4, rng=None):
        if rng is None:
            rng = np.random.RandomState(42)
        self.input_proj = Conv1DLayer(input_size, hidden_size, rng)
        self.input_bn = BatchNorm1D(hidden_size)
        self.blocks = [ResBlock(hidden_size, rng) for _ in range(n_blocks)]
        self.classifier = Conv1DLayer(hidden_size, n_classes, rng)
        self.cache = {}

    def forward(self, x, training=True):
        h = self.input_proj.forward(x)
        h = self.input_bn.forward(h, training)
        self.cache["pre_relu0"] = h
        h = relu(h)
        for block in self.blocks:
            h = block.forward(h, training)
        self.cache["pre_classifier"] = h
        logits = self.classifier.forward(h)
        return logits

    def backward(self, dlogits):
        dh = self.classifier.backward(dlogits)
        for block in reversed(self.blocks):
            dh = block.backward(dh)
        dh = dh * relu_deriv(self.cache["pre_relu0"])
        dh = self.input_bn.backward(dh)
        dh = self.input_proj.backward(dh)
        return dh

    def update(self, lr):
        self.classifier.update(lr)
        for block in reversed(self.blocks):
            block.update(lr)
        self.input_bn.update(lr)
        self.input_proj.update(lr)

    def predict(self, X, batch_size=256):
        preds = []
        for i in range(0, len(X), batch_size):
            batch = X[i:i+batch_size]
            logits = self.forward(batch, training=False)
            preds.append(np.argmax(logits, axis=1))
        return np.concatenate(preds)

    def predict_proba(self, X, batch_size=256):
        probs = []
        for i in range(0, len(X), batch_size):
            batch = X[i:i+batch_size]
            logits = self.forward(batch, training=False)
            probs.append(softmax(logits))
        return np.concatenate(probs)

    def save(self, path):
        weights = {}
        weights["inp_W"] = self.input_proj.W
        weights["inp_b"] = self.input_proj.b
        weights["inp_bn_gamma"] = self.input_bn.gamma
        weights["inp_bn_beta"] = self.input_bn.beta
        for i, block in enumerate(self.blocks):
            weights[f"blk{i}_conv1_W"] = block.conv1.W
            weights[f"blk{i}_conv1_b"] = block.conv1.b
            weights[f"blk{i}_bn1_gamma"] = block.bn1.gamma
            weights[f"blk{i}_bn1_beta"] = block.bn1.beta
            weights[f"blk{i}_conv2_W"] = block.conv2.W
            weights[f"blk{i}_conv2_b"] = block.conv2.b
            weights[f"blk{i}_bn2_gamma"] = block.bn2.gamma
            weights[f"blk{i}_bn2_beta"] = block.bn2.beta
        weights["cls_W"] = self.classifier.W
        weights["cls_b"] = self.classifier.b
        np.savez(path, **weights)


def confusion_matrix_np(y_true, y_pred, n_classes):
    cm = np.zeros((n_classes, n_classes), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


def classification_report_np(y_true, y_pred, class_names):
    classes = sorted(set(y_true) | set(y_pred))
    lines = []
    lines.append(f"{'':>20s} {'precision':>10s} {'recall':>10s} {'f1-score':>10s} {'support':>10s}")
    lines.append("")
    precisions, recalls, f1s, supports = [], [], [], []
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        support = sum(1 for t in y_true if t == c)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        name = class_names.get(int(c), str(c))
        lines.append(f"{name:>20s} {precision:>10.4f} {recall:>10.4f} {f1:>10.4f} {support:>10d}")
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
        supports.append(support)
    total = sum(supports)
    lines.append("")
    lines.append(f"{'macro avg':>20s} {np.mean(precisions):>10.4f} {np.mean(recalls):>10.4f} "
                 f"{np.mean(f1s):>10.4f} {total:>10d}")
    w_prec = sum(p*s for p,s in zip(precisions, supports)) / total if total > 0 else 0
    w_rec = sum(r*s for r,s in zip(recalls, supports)) / total if total > 0 else 0
    w_f1 = sum(f*s for f,s in zip(f1s, supports)) / total if total > 0 else 0
    lines.append(f"{'weighted avg':>20s} {w_prec:>10.4f} {w_rec:>10.4f} {w_f1:>10.4f} {total:>10d}")
    return "\n".join(lines), np.mean(f1s), w_f1


def main():
    # 1. Load data
    X, y = load_all_data()
    print(f"\nTotal samples: {len(X)}")
    print(f"Class distribution: {dict(zip(*np.unique(y, return_counts=True)))}")

    # 2. Normalize
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-8
    X_norm = (X - mean) / std

    # 3. Split
    X_train, X_test, y_train, y_test = stratified_split(X_norm, y, test_size=0.2)
    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    # 4. Build and train ResNet
    HIDDEN = 32
    N_BLOCKS = 2
    N_CLASSES = 4
    EPOCHS = 50
    BATCH_SIZE = 64
    LR = 0.01

    print(f"\nTraining 1D-ResNet: hidden={HIDDEN}, blocks={N_BLOCKS}, epochs={EPOCHS}")
    rng = np.random.RandomState(42)
    model = ResNet1D(len(ALL_FEATURES), HIDDEN, N_BLOCKS, N_CLASSES, rng)

    best_acc = 0
    for epoch in range(EPOCHS):
        # Shuffle training data
        perm = rng.permutation(len(X_train))
        X_shuf = X_train[perm]
        y_shuf = y_train[perm]

        epoch_loss = 0
        n_batches = 0
        for i in range(0, len(X_shuf), BATCH_SIZE):
            xb = X_shuf[i:i+BATCH_SIZE]
            yb = y_shuf[i:i+BATCH_SIZE]
            if len(xb) < 2:
                continue

            # Forward
            logits = model.forward(xb, training=True)
            probs = softmax(logits)
            loss = cross_entropy_loss(probs, yb)
            epoch_loss += loss
            n_batches += 1

            # Backward
            dlogits = probs - one_hot(yb, N_CLASSES)
            dlogits /= len(yb)
            model.backward(dlogits)

            # Update with learning rate decay
            cur_lr = LR * (1.0 - epoch / EPOCHS)
            model.update(cur_lr)

        # Eval
        if (epoch + 1) % 10 == 0 or epoch == 0:
            y_pred = model.predict(X_test)
            acc = np.mean(y_pred == y_test)
            if acc > best_acc:
                best_acc = acc
            print(f"  Epoch {epoch+1}/{EPOCHS} - Loss: {epoch_loss/max(n_batches,1):.4f} - "
                  f"Test Acc: {acc:.4f}")

    # 5. Final evaluation
    y_pred = model.predict(X_test)
    acc = np.mean(y_pred == y_test)
    cm = confusion_matrix_np(y_test, y_pred, N_CLASSES)
    report, f1_macro, f1_weighted = classification_report_np(y_test, y_pred, CLASS_NAMES)

    print(f"\n{'='*60}")
    print(f"1D-ResNet CLASSIFICATION RESULTS")
    print(f"{'='*60}")
    print(f"Accuracy:      {acc:.4f}")
    print(f"F1 (macro):    {f1_macro:.4f}")
    print(f"F1 (weighted): {f1_weighted:.4f}")
    print(f"\nConfusion Matrix:")
    print(f"{'':>20s}", end="")
    for i in range(4):
        print(f"{CLASS_NAMES[i]:>18s}", end="")
    print()
    for i in range(4):
        print(f"{CLASS_NAMES[i]:>20s}", end="")
        for j in range(4):
            print(f"{cm[i][j]:>18d}", end="")
        print()
    print(f"\nClassification Report:")
    print(report)

    # 6. Compare with XGBoost
    xgb_results_path = OUT_DIR / "xgboost_results.json"
    if xgb_results_path.exists():
        with open(xgb_results_path) as f:
            xgb_results = json.load(f)
        print(f"\n{'='*60}")
        print("COMPARISON: 1D-ResNet vs XGBoost/Ensemble")
        print(f"{'='*60}")
        print(f"{'Metric':<20s} {'ResNet1D':>12s} {'XGBoost':>12s}")
        print(f"{'Accuracy':<20s} {acc:>12.4f} {xgb_results['accuracy']:>12.4f}")
        print(f"{'F1 (macro)':<20s} {f1_macro:>12.4f} {xgb_results['f1_macro']:>12.4f}")
        print(f"{'F1 (weighted)':<20s} {f1_weighted:>12.4f} {xgb_results['f1_weighted']:>12.4f}")

    # 7. Save
    model.save(str(MODEL_DIR / "resnet1d_weights.npz"))
    print(f"\nModel saved: {MODEL_DIR / 'resnet1d_weights.npz'}")

    results = {
        "model_type": "resnet1d_numpy",
        "accuracy": float(acc),
        "f1_macro": float(f1_macro),
        "f1_weighted": float(f1_weighted),
        "confusion_matrix": cm.tolist(),
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "n_features": len(ALL_FEATURES),
        "hidden_size": HIDDEN,
        "n_blocks": N_BLOCKS,
        "epochs": EPOCHS,
        "class_distribution": {CLASS_NAMES[k]: int(v)
                               for k, v in zip(*np.unique(y, return_counts=True))},
    }
    if xgb_results_path.exists():
        results["comparison"] = {
            "xgboost_accuracy": xgb_results["accuracy"],
            "xgboost_f1_macro": xgb_results["f1_macro"],
            "resnet_accuracy": float(acc),
            "resnet_f1_macro": float(f1_macro),
        }
    results_path = OUT_DIR / "resnet1d_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved: {results_path}")

    # Save normalization params
    np.savez(str(MODEL_DIR / "resnet1d_norm.npz"), mean=mean, std=std)


if __name__ == "__main__":
    main()
