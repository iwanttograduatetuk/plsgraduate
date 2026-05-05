"""
03_xgboost_train.py - XGBoost Fault Classifier Training
Parses long-format CSV -> wide format, trains multi-class fault classifier.
Labels: normal=0, coolant=1, hydraulics=2, probe=3

Falls back to numpy-based Decision Tree ensemble if xgboost/sklearn unavailable.
"""

import os
import json
import csv
import pickle
import warnings
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd

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

# ---- 59 selected features (11 numeric + 18 alarm + 30 binary) ----
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
print(f"Feature set: {len(NUMERIC_FEATURES)} numeric + {len(ALARM_FEATURES)} alarm + "
      f"{len(BINARY_FEATURES)} binary = {len(ALL_FEATURES)} total")


# ---- CSV parsing (long -> wide) ----
def parse_csv_auto(csv_path, features=None):
    """
    Auto-detect CSV format (long vs wide) and parse to wide-format DataFrame.
    Long format: time_s, node, value, type
    Wide format: time_s, <feature1>, <feature2>, ...
    Returns DataFrame with one row per unique timestamp, columns = feature names.
    """
    if features is None:
        features = ALL_FEATURES

    feature_set = set(features)

    # Read header to detect format
    with open(csv_path, "r") as f:
        header_line = f.readline().strip().replace("\r", "")
        cols = [c.strip() for c in header_line.split(",")]

    is_long_format = (cols[:4] == ["time_s", "node", "value", "type"])

    if is_long_format:
        # Long format parsing
        data = defaultdict(dict)
        timestamps = set()

        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            # Fix fieldnames for \r
            reader.fieldnames = [fn.strip().replace("\r", "") for fn in reader.fieldnames]
            for row in reader:
                node = row.get("node", "").strip().replace("\r", "")
                if node not in feature_set:
                    continue
                try:
                    t = float(row["time_s"].strip().replace("\r", ""))
                except (ValueError, AttributeError):
                    continue
                val_str = row.get("value", "0").strip().replace("\r", "")
                if val_str in ("True", "true"):
                    val = 1.0
                elif val_str in ("False", "false"):
                    val = 0.0
                else:
                    try:
                        val = float(val_str)
                    except ValueError:
                        val = 0.0
                data[t][node] = val
                timestamps.add(t)

        if not timestamps:
            return pd.DataFrame(columns=["time_s"] + features)

        sorted_times = sorted(timestamps)
        rows = []
        current_state = {f: 0.0 for f in features}
        for t in sorted_times:
            current_state.update(data[t])
            row = {"time_s": t}
            row.update({f: current_state.get(f, 0.0) for f in features})
            rows.append(row)
        df = pd.DataFrame(rows, columns=["time_s"] + features)

    else:
        # Wide format - read directly with pandas
        df = pd.read_csv(csv_path)
        df.columns = [c.strip().replace("\r", "") for c in df.columns]
        # Convert True/False strings
        for col in df.columns:
            if col == "time_s":
                continue
            if df[col].dtype == object:
                df[col] = df[col].map(
                    lambda x: 1.0 if str(x).strip() in ("True", "true")
                    else (0.0 if str(x).strip() in ("False", "false")
                          else (float(x) if str(x).strip().replace("-","").replace(".","").isdigit()
                                else 0.0))
                )
        # Keep only our features
        available = [f for f in features if f in df.columns]
        missing = [f for f in features if f not in df.columns]
        result = df[["time_s"] + available].copy()
        for m in missing:
            result[m] = 0.0
        df = result[["time_s"] + features]

    return df


def sample_snapshots(df, n_samples=10):
    """Sample n evenly-spaced snapshots from a time-series DataFrame."""
    if len(df) <= n_samples:
        return df.copy()
    indices = np.linspace(0, len(df) - 1, n_samples, dtype=int)
    return df.iloc[indices].copy()


# ---- data loading ----
def load_normal_data(max_files=None):
    """Load normal operation CSVs, return list of DataFrames."""
    csv_files = sorted(NORMAL_DIR.glob("*.csv"))
    if max_files:
        csv_files = csv_files[:max_files]
    print(f"Loading {len(csv_files)} normal operation files...")
    frames = []
    for i, f in enumerate(csv_files):
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(csv_files)}")
        df = parse_csv_auto(f)
        if len(df) > 0:
            sampled = sample_snapshots(df, n_samples=10)
            sampled["label"] = 0
            sampled["source"] = f.stem
            frames.append(sampled)
    return frames


def load_fault_data(label, subsystem_name, subsystem_dir):
    """Load fault CSVs with causes.json timing."""
    frames = []
    for exp_dir in sorted(subsystem_dir.iterdir()):
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

            csv_files = list(run_dir.glob("*.csv"))
            if not csv_files:
                continue

            for cf in csv_files:
                df = parse_csv_auto(cf)
                if len(df) == 0:
                    continue

                # Label: rows after cause_start_at are fault
                fault_start = causes.get("cause_start_at", 0)
                # Sample from fault period (after cause_start)
                fault_df = df[df["time_s"] >= fault_start].copy()
                if len(fault_df) == 0:
                    fault_df = df.copy()

                sampled = sample_snapshots(fault_df, n_samples=10)
                sampled["label"] = label
                sampled["source"] = f"{subsystem_name}/{exp_dir.name}/{run_dir.name}"
                frames.append(sampled)
    return frames


# ---- Fallback classifier (numpy-based Decision Tree) ----
class DecisionStump:
    """A single-level decision tree (stump) for use in a simple ensemble."""
    def __init__(self):
        self.feature_idx = 0
        self.threshold = 0.0
        self.left_val = 0
        self.right_val = 0

    def fit(self, X, y, sample_weight=None):
        n_samples, n_features = X.shape
        classes = np.unique(y)
        if sample_weight is None:
            sample_weight = np.ones(n_samples) / n_samples

        best_gain = -1
        for feat_idx in np.random.choice(n_features, min(n_features, max(10, n_features//3)), replace=False):
            thresholds = np.unique(X[:, feat_idx])
            if len(thresholds) > 20:
                thresholds = np.percentile(X[:, feat_idx], np.linspace(0, 100, 20))

            for thr in thresholds:
                left_mask = X[:, feat_idx] <= thr
                right_mask = ~left_mask
                if left_mask.sum() == 0 or right_mask.sum() == 0:
                    continue

                left_w = sample_weight[left_mask].sum()
                right_w = sample_weight[right_mask].sum()

                # Weighted most common class
                left_counts = {}
                for c in classes:
                    left_counts[c] = sample_weight[left_mask & (y == c)].sum()
                right_counts = {}
                for c in classes:
                    right_counts[c] = sample_weight[right_mask & (y == c)].sum()

                gain = 0
                for c in classes:
                    gain += left_counts.get(c, 0) * (1 if c == max(left_counts, key=left_counts.get) else 0)
                    gain += right_counts.get(c, 0) * (1 if c == max(right_counts, key=right_counts.get) else 0)

                if gain > best_gain:
                    best_gain = gain
                    self.feature_idx = feat_idx
                    self.threshold = thr
                    self.left_val = max(left_counts, key=left_counts.get)
                    self.right_val = max(right_counts, key=right_counts.get)

    def predict(self, X):
        preds = np.where(X[:, self.feature_idx] <= self.threshold,
                         self.left_val, self.right_val)
        return preds


class SimpleGBClassifier:
    """
    Simple Gradient Boosting-like multi-class classifier using decision stumps.
    Uses one-vs-all strategy.
    """
    def __init__(self, n_estimators=100, learning_rate=0.1, max_depth=3):
        self.n_estimators = n_estimators
        self.n_classes = 0
        self.classes_ = None
        self.trees = {}  # class -> list of (stump, weight)

    def fit(self, X, y):
        self.classes_ = np.unique(y)
        self.n_classes = len(self.classes_)
        n_samples = len(y)

        for c in self.classes_:
            y_binary = (y == c).astype(float)
            sample_weight = np.ones(n_samples) / n_samples
            stumps = []

            for _ in range(self.n_estimators):
                stump = DecisionStump()
                stump.fit(X, (y == c).astype(int), sample_weight)

                preds = stump.predict(X)
                correct = (preds == (y == c).astype(int))
                err = sample_weight[~correct].sum()
                if err >= 0.5:
                    break
                if err < 1e-10:
                    stumps.append((stump, 1.0))
                    break

                alpha = 0.5 * np.log((1 - err) / (err + 1e-10))
                sample_weight[~correct] *= np.exp(alpha)
                sample_weight[correct] *= np.exp(-alpha)
                sample_weight /= sample_weight.sum()
                stumps.append((stump, alpha))

            self.trees[c] = stumps

    def predict(self, X):
        scores = np.zeros((len(X), self.n_classes))
        for i, c in enumerate(self.classes_):
            for stump, alpha in self.trees.get(c, []):
                preds = stump.predict(X)
                scores[:, i] += alpha * preds
        return self.classes_[np.argmax(scores, axis=1)]

    def save_model(self, path):
        with open(path, "wb") as f:
            pickle.dump(self, f)


# ---- Evaluation helpers ----
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
    # Macro avg
    lines.append(f"{'macro avg':>20s} {np.mean(precisions):>10.4f} {np.mean(recalls):>10.4f} "
                 f"{np.mean(f1s):>10.4f} {total:>10d}")
    # Weighted avg
    w_prec = sum(p * s for p, s in zip(precisions, supports)) / total if total > 0 else 0
    w_rec = sum(r * s for r, s in zip(recalls, supports)) / total if total > 0 else 0
    w_f1 = sum(f * s for f, s in zip(f1s, supports)) / total if total > 0 else 0
    lines.append(f"{'weighted avg':>20s} {w_prec:>10.4f} {w_rec:>10.4f} {w_f1:>10.4f} {total:>10d}")

    return "\n".join(lines), np.mean(f1s), w_f1


def stratified_split(X, y, test_size=0.2, random_state=42):
    """Stratified train/test split using numpy."""
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


# ---- main ----
def main():
    CLASS_NAMES = {0: "normal", 1: "coolant_fault", 2: "hydraulics_fault", 3: "probe_fault"}

    # 1. Load all data
    all_frames = []

    # Normal data
    normal_frames = load_normal_data()
    all_frames.extend(normal_frames)
    n_normal = sum(len(f) for f in normal_frames)
    print(f"Normal samples: {n_normal}")

    # Fault data
    for label, (name, path) in SUBSYSTEMS.items():
        fault_frames = load_fault_data(label, name, path)
        all_frames.extend(fault_frames)
        n_fault = sum(len(f) for f in fault_frames)
        print(f"{name} fault samples: {n_fault}")

    # 2. Combine
    combined = pd.concat(all_frames, ignore_index=True)
    print(f"\nTotal samples: {len(combined)}")
    print(f"Class distribution:\n{combined['label'].value_counts().sort_index()}")

    # 3. Prepare feature matrix
    X = combined[ALL_FEATURES].fillna(0).values.astype(np.float32)
    y = combined["label"].values.astype(int)

    # 4. Train/test split (stratified)
    X_train, X_test, y_train, y_test = stratified_split(X, y, test_size=0.2, random_state=42)
    print(f"\nTrain: {len(X_train)}, Test: {len(X_test)}")

    # 5. Try XGBoost, fallback to custom classifier
    model = None
    model_type = None

    try:
        import xgboost as xgb
        from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score

        print("\nTraining XGBoost classifier...")
        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            objective="multi:softmax",
            num_class=4,
            eval_metric="mlogloss",
            use_label_encoder=False,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        model_type = "xgboost"

        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        f1_macro = f1_score(y_test, y_pred, average="macro")
        f1_weighted = f1_score(y_test, y_pred, average="weighted")
        cm = confusion_matrix(y_test, y_pred)
        report = classification_report(y_test, y_pred,
                                       target_names=[CLASS_NAMES[i] for i in range(4)])

        # Save model
        model_path = MODEL_DIR / "xgboost_fault_classifier.json"
        model.save_model(str(model_path))
        print(f"Model saved: {model_path}")

    except ImportError:
        print("\nXGBoost/sklearn not available. Using numpy-based ensemble classifier...")
        model = SimpleGBClassifier(n_estimators=50)
        model.fit(X_train, y_train)
        model_type = "numpy_ensemble"

        y_pred = model.predict(X_test)
        acc = np.mean(y_pred == y_test)
        cm = confusion_matrix_np(y_test, y_pred, 4)
        report, f1_macro, f1_weighted = classification_report_np(
            y_test, y_pred, CLASS_NAMES)

        # Save model
        model_path = MODEL_DIR / "xgboost_fault_classifier.json"
        model.save_model(str(model_path))
        print(f"Model saved (pickle): {model_path}")

    # 6. Print results
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION RESULTS (model: {model_type})")
    print(f"{'='*60}")
    print(f"Accuracy:         {acc:.4f}")
    print(f"F1 (macro):       {f1_macro:.4f}")
    print(f"F1 (weighted):    {f1_weighted:.4f}")
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

    # 7. Feature importance (for numpy model)
    if model_type == "numpy_ensemble":
        feat_counts = defaultdict(int)
        for c, stumps in model.trees.items():
            for stump, alpha in stumps:
                feat_counts[ALL_FEATURES[stump.feature_idx]] += 1
        top_feats = sorted(feat_counts.items(), key=lambda x: -x[1])[:15]
        print("\nTop 15 important features (by usage frequency):")
        for feat, count in top_feats:
            print(f"  {feat}: {count}")

    # 8. Save results
    results = {
        "model_type": model_type,
        "accuracy": float(acc),
        "f1_macro": float(f1_macro),
        "f1_weighted": float(f1_weighted),
        "confusion_matrix": cm.tolist() if isinstance(cm, np.ndarray) else cm,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "n_features": len(ALL_FEATURES),
        "class_distribution": {CLASS_NAMES[k]: int(v) for k, v in
                               zip(*np.unique(y, return_counts=True))},
    }
    results_path = OUT_DIR / "xgboost_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
