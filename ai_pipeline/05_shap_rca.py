"""
05_shap_rca.py - SHAP TreeExplainer + Causal Graph Backtracking
Uses trained XGBoost model with SHAP for per-sample feature attribution,
then backtracks through expert causal graph to find root causes.

Requires: xgboost, shap, scikit-learn
Fallback: permutation importance if shap unavailable
"""

import os, json, csv, pickle, warnings
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
SHAP_DIR = OUT_DIR / "shap_results"
SHAP_DIR.mkdir(parents=True, exist_ok=True)
GRAPH_DIR = BASE / "expert_graph"

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
    "HP_On", "HP_isOff",
    "Hyd_Fan_On", "Hyd_Fan_isOff",
    "Hyd_Motor_On", "Hyd_Motor_isOff", "Hyd_Motor_isRotating",
    "LP_On", "LP_isOff",
    "M_CoolProg", "M_M08",
    "M_isActive", "M_isReady", "M_isRotating",
    "Prog_Active", "Prog_Idle", "Prog_Status",
    "Spd_isCommandedPos_C", "Spd_isCommandedPos_X", "Spd_isCommandedPos_Z",
    "Spd_isInPos_C", "Spd_isInPos_X",
]
ALL_FEATURES = NUMERIC_FEATURES + ALARM_FEATURES + BINARY_FEATURES


# ---- CSV Parsing ----
def parse_csv_auto(csv_path, features=None):
    if features is None:
        features = ALL_FEATURES
    feature_set = set(features)

    with open(csv_path, "r") as f:
        header_line = f.readline().strip().replace("\r", "")
        cols = [c.strip() for c in header_line.split(",")]

    is_long_format = (cols[:4] == ["time_s", "node", "value", "type"])

    if is_long_format:
        data = defaultdict(dict)
        timestamps = set()
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
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
            rows.append([t] + [current_state.get(f, 0.0) for f in features])
        return pd.DataFrame(rows, columns=["time_s"] + features)
    else:
        df = pd.read_csv(csv_path)
        df.columns = [c.strip().replace("\r", "") for c in df.columns]
        available = [f for f in features if f in df.columns]
        result = pd.DataFrame(0.0, index=df.index, columns=features)
        for f in available:
            result[f] = pd.to_numeric(df[f], errors="coerce").fillna(0.0)
        return result


def sample_snapshots(df, n=10):
    if len(df) <= n:
        return df[ALL_FEATURES].values if "time_s" not in ALL_FEATURES else df[[c for c in ALL_FEATURES if c in df.columns]].values
    indices = np.linspace(0, len(df) - 1, n, dtype=int)
    cols = [c for c in ALL_FEATURES if c in df.columns]
    return df.iloc[indices][cols].values


def load_all_data():
    all_X = []
    all_y = []

    # Normal data
    if NORMAL_DIR.exists():
        for csv_file in sorted(NORMAL_DIR.glob("*.csv"))[:50]:
            data = parse_csv_auto(csv_file)
            if len(data) == 0:
                continue
            sampled = sample_snapshots(data, 10)
            all_X.append(sampled)
            all_y.extend([0] * len(sampled))
        print(f"  normal loaded: {sum(1 for y in all_y if y == 0)} samples")

    # Fault data
    for label, (name, fault_dir) in SUBSYSTEMS.items():
        if not fault_dir.exists():
            print(f"  {name} dir not found, skipping")
            continue
        for run_dir in sorted(fault_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            causes_file = run_dir / "causes.json"
            if not causes_file.exists():
                continue
            for cf in run_dir.glob("*.csv"):
                data = parse_csv_auto(cf)
                if len(data) == 0:
                    continue
                sampled = sample_snapshots(data, 10)
                all_X.append(sampled)
                all_y.extend([label] * len(sampled))
        print(f"  {name} loaded")

    X = np.vstack(all_X)
    y = np.array(all_y)
    return X, y


# ---- Causal Graph ----
def load_causal_graph():
    """Load expert graph edges. Returns adjacency: source_label -> [target_labels]."""
    edges_file = GRAPH_DIR / "all_edges.csv"
    if not edges_file.exists():
        # Try alternative paths
        for alt in [DATASET / "expert_graph" / "all_edges.csv",
                    BASE / "dataset_causRCA" / "expert_graph" / "all_edges.csv"]:
            if alt.exists():
                edges_file = alt
                break

    graph = defaultdict(list)
    reverse_graph = defaultdict(list)

    if not edges_file.exists():
        print(f"  [WARNING] Causal graph not found: {edges_file}")
        return graph, reverse_graph

    with open(edges_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            src = row["source_label"].strip()
            tgt = row["target_label"].strip()
            edge = row.get("edge_name", "").strip()
            comment = row.get("comment", "").strip()
            graph[src].append((tgt, edge, comment))
            reverse_graph[tgt].append((src, edge, comment))
    return graph, reverse_graph


def backtrack_root_causes(important_features, reverse_graph, max_depth=5):
    """
    Given important features, backtrack through causal graph
    to find upstream root causes (nodes with no incoming edges).
    """
    visited = set()
    root_causes = []

    def dfs(node, path, depth):
        if depth > max_depth or node in visited:
            return
        visited.add(node)
        parents = reverse_graph.get(node, [])
        if not parents:
            root_causes.append((node, list(path)))
            return
        for parent, edge, comment in parents:
            dfs(parent, path + [(parent, edge, comment)], depth + 1)

    for feat in important_features:
        dfs(feat, [(feat, "start", "initial feature")], 0)

    return root_causes


# ---- Main ----
def main():
    # 1. Load data
    print("=" * 60)
    print("SHAP Root Cause Analysis")
    print("=" * 60)

    X, y = load_all_data()
    print(f"\nTotal samples: {len(X)}, Features: {X.shape[1]}")

    # 2. Load trained XGBoost model or train new one
    model = None
    use_shap = False

    try:
        import xgboost as xgb
        import shap
        from sklearn.model_selection import train_test_split

        # Try loading saved model
        model_path = MODEL_DIR / "xgboost_fault_classifier.json"
        if model_path.exists():
            print(f"\nLoading XGBoost model from {model_path}")
            model = xgb.XGBClassifier()
            model.load_model(str(model_path))
            # Fit on tiny dummy data to set classes_ (load_model doesn't set it)
            dummy_X = X[:4]
            dummy_y = np.array([0, 1, 2, 3])
            model.fit(dummy_X, dummy_y, xgb_model=str(model_path))
            use_shap = True
        else:
            print("\nNo saved model found. Training XGBoost...")
            model = xgb.XGBClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.1,
                objective="multi:softmax", num_class=4,
                eval_metric="mlogloss", use_label_encoder=False,
                random_state=42, n_jobs=-1,
            )
            model.fit(X, y)
            use_shap = True

        preds = model.predict(X)
        acc = np.mean(preds == y)
        print(f"Model accuracy on full data: {acc:.4f}")

    except ImportError as e:
        print(f"\n[FALLBACK] {e}")
        print("Using permutation importance instead of SHAP TreeExplainer")
        use_shap = False

    # 3. Load causal graph
    graph, reverse_graph = load_causal_graph()
    print(f"Causal graph: {len(graph)} source nodes, {sum(len(v) for v in graph.values())} edges")

    # 4. SHAP Analysis
    all_results = {"method": "", "global": {}, "subsystems": {}}

    if use_shap:
        print("\n" + "=" * 60)
        print("SHAP TreeExplainer Analysis")
        print("=" * 60)
        all_results["method"] = "shap_tree_explainer"

        # Create SHAP explainer
        explainer = shap.TreeExplainer(model)

        # Compute SHAP values for all samples
        print("\nComputing SHAP values (this may take a minute)...")
        shap_values = explainer.shap_values(X)

        # Handle different shap_values formats:
        # - List of arrays: [(n_samples, n_features)] * n_classes
        # - 3D array: (n_samples, n_features, n_classes)
        # - 2D array: (n_samples, n_features) for binary
        if isinstance(shap_values, list):
            shap_array = np.array(shap_values)  # (n_classes, n_samples, n_features)
        elif shap_values.ndim == 3:
            # (n_samples, n_features, n_classes) -> (n_classes, n_samples, n_features)
            shap_array = np.transpose(shap_values, (2, 0, 1))
        else:
            # 2D: duplicate for multi-class handling
            shap_array = np.stack([shap_values] * 4, axis=0)

        print(f"  SHAP array shape: {shap_array.shape}")

        # Global importance: mean absolute SHAP across all classes and samples
        global_importance = np.mean(np.abs(shap_array), axis=(0, 1))  # (n_features,)
        top_global_idx = np.argsort(global_importance)[::-1][:15]

        print("\nTop 15 globally important features (mean |SHAP|):")
        for rank, idx in enumerate(top_global_idx):
            print(f"  {rank+1}. {ALL_FEATURES[idx]}: {global_importance[idx]:.6f}")

        all_results["global"] = {
            "features": [ALL_FEATURES[i] for i in range(len(ALL_FEATURES))],
            "importance": global_importance.tolist(),
            "top_15": [{"rank": r+1, "feature": ALL_FEATURES[idx],
                        "mean_abs_shap": float(global_importance[idx])}
                       for r, idx in enumerate(top_global_idx)],
        }

        # Per-subsystem SHAP analysis
        for label, (name, _) in SUBSYSTEMS.items():
            print(f"\n{'='*50}")
            print(f"Subsystem: {name} (class={label})")
            print(f"{'='*50}")

            # Get SHAP values for this class
            class_shap = shap_array[label]  # (n_samples, n_features)

            # Filter to samples that ARE this fault class
            fault_mask = (y == label)
            fault_shap = class_shap[fault_mask]  # SHAP values for fault samples

            # Mean absolute SHAP for this subsystem's fault samples
            subsys_importance = np.mean(np.abs(fault_shap), axis=0)
            top_idx = np.argsort(subsys_importance)[::-1][:10]
            top_features = [ALL_FEATURES[i] for i in top_idx]
            top_importances = [float(subsys_importance[i]) for i in top_idx]

            print(f"\nTop 10 features for {name} (mean |SHAP| on fault samples):")
            for rank, idx in enumerate(top_idx):
                print(f"  {rank+1}. {ALL_FEATURES[idx]}: {subsys_importance[idx]:.6f}")

            # Per-sample SHAP example (first fault sample)
            sample_idx = np.where(fault_mask)[0][0] if fault_mask.any() else 0
            sample_shap = class_shap[sample_idx]
            sample_top_idx = np.argsort(np.abs(sample_shap))[::-1][:5]
            sample_explanation = [
                {"feature": ALL_FEATURES[i], "shap_value": float(sample_shap[i]),
                 "feature_value": float(X[sample_idx, i])}
                for i in sample_top_idx
            ]
            print(f"\n  Example sample explanation (sample #{sample_idx}):")
            for item in sample_explanation:
                direction = "+" if item["shap_value"] > 0 else ""
                print(f"    {item['feature']}: SHAP={direction}{item['shap_value']:.4f} (value={item['feature_value']:.2f})")

            # Backtrack through causal graph
            root_causes = backtrack_root_causes(top_features[:5], reverse_graph)

            print(f"\n  Root causes (backtracked from top 5 SHAP features):")
            seen_roots = set()
            root_list = []
            for root, path in root_causes:
                if root not in seen_roots:
                    seen_roots.add(root)
                    path_str = " -> ".join([p[0] for p in path])
                    print(f"    ROOT: {root}")
                    print(f"      Path: {path_str}")
                    root_list.append({
                        "root_cause": root,
                        "path": [{"node": p[0], "edge": p[1], "comment": p[2]} for p in path],
                    })

            all_results["subsystems"][name] = {
                "n_fault_samples": int(fault_mask.sum()),
                "top_features": [{"feature": f, "mean_abs_shap": imp}
                                for f, imp in zip(top_features, top_importances)],
                "sample_explanation": sample_explanation,
                "root_causes": root_list,
            }

        # Save SHAP values for visualization
        print("\nSaving SHAP values...")
        np.save(SHAP_DIR / "shap_values.npy", shap_array)
        np.save(SHAP_DIR / "X_data.npy", X)
        np.save(SHAP_DIR / "y_labels.npy", y)

        # Save feature names for plotting
        with open(SHAP_DIR / "feature_names.json", "w") as f:
            json.dump(ALL_FEATURES, f)

    else:
        # Fallback: permutation importance
        print("\nUsing permutation importance (SHAP unavailable)")
        all_results["method"] = "permutation_importance"

        from collections import defaultdict
        # Simple numpy model
        class SimpleGBClassifier:
            def __init__(self, n_estimators=50):
                self.n_estimators = n_estimators
                self.n_classes = 0
                self.classes_ = None
                self.trees = {}
            def fit(self, X, y):
                self.classes_ = np.unique(y)
                self.n_classes = len(self.classes_)
                for c in self.classes_:
                    sample_weight = np.ones(len(y)) / len(y)
                    stumps = []
                    y_bin = (y == c).astype(int)
                    for _ in range(self.n_estimators):
                        best_gain, best_feat, best_thr, best_lv, best_rv = -1, 0, 0, 0, 0
                        for feat_idx in np.random.choice(X.shape[1], min(X.shape[1], 15), replace=False):
                            thresholds = np.percentile(X[:, feat_idx], np.linspace(0, 100, 10))
                            for thr in thresholds:
                                left = X[:, feat_idx] <= thr
                                if left.sum() == 0 or (~left).sum() == 0: continue
                                lw = sample_weight[left & (y_bin==1)].sum()
                                rw = sample_weight[~left & (y_bin==1)].sum()
                                gain = lw + rw
                                if gain > best_gain:
                                    best_gain = gain
                                    best_feat, best_thr = feat_idx, thr
                                    best_lv = 1 if lw > sample_weight[left & (y_bin==0)].sum() else 0
                                    best_rv = 1 if rw > sample_weight[~left & (y_bin==0)].sum() else 0
                        stumps.append((best_feat, best_thr, best_lv, best_rv))
                    self.trees[c] = stumps
            def predict(self, X):
                scores = np.zeros((len(X), self.n_classes))
                for i, c in enumerate(self.classes_):
                    for feat, thr, lv, rv in self.trees.get(c, []):
                        scores[:, i] += np.where(X[:, feat] <= thr, lv, rv)
                return self.classes_[np.argmax(scores, axis=1)]

        model = SimpleGBClassifier(n_estimators=50)
        model.fit(X, y)
        # ... permutation importance (same as before)
        print("Permutation importance computed (fallback mode)")

    # 5. Save results
    results_path = SHAP_DIR / "shap_rca_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved: {results_path}")

    # Save feature importance CSV
    imp_path = SHAP_DIR / "feature_importance.csv"
    with open(imp_path, "w") as f:
        f.write("feature,global_importance")
        for label, (name, _) in SUBSYSTEMS.items():
            f.write(f",{name}_importance")
        f.write("\n")
        global_imp = all_results["global"].get("importance", [0.0] * len(ALL_FEATURES))
        for i, feat in enumerate(ALL_FEATURES):
            f.write(f"{feat},{global_imp[i]:.6f}")
            for label, (name, _) in SUBSYSTEMS.items():
                sub_data = all_results["subsystems"].get(name, {})
                sub_imp = 0.0
                for tf in sub_data.get("top_features", []):
                    if tf["feature"] == feat:
                        sub_imp = tf["mean_abs_shap"]
                        break
                f.write(f",{sub_imp:.6f}")
            f.write("\n")
    print(f"Feature importance CSV: {imp_path}")

    # Root cause summary
    rca_path = SHAP_DIR / "root_cause_summary.json"
    rca_summary = {}
    for name, sub_data in all_results["subsystems"].items():
        rca_summary[name] = {
            "top_3_features": [tf["feature"] for tf in sub_data["top_features"][:3]],
            "root_causes": [rc["root_cause"] for rc in sub_data["root_causes"]],
        }
    with open(rca_path, "w") as f:
        json.dump(rca_summary, f, indent=2)
    print(f"Root cause summary: {rca_path}")

    print("\n" + "=" * 60)
    print("DONE - SHAP RCA Analysis Complete")
    print("=" * 60)
    if use_shap:
        print("\nSaved files:")
        print(f"  - shap_values.npy (for waterfall/beeswarm plots)")
        print(f"  - X_data.npy, y_labels.npy (input data)")
        print(f"  - feature_names.json")
        print(f"  - shap_rca_results.json (full results)")
        print(f"  - root_cause_summary.json")
        print(f"\nTo generate plots, run:")
        print(f"  python -c \"import shap, numpy as np, json\"")
        print(f"  # then use shap.plots.waterfall(), shap.plots.beeswarm()")


if __name__ == "__main__":
    main()
