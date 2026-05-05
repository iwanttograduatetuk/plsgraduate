"""
01_eda_threshold.py — CausRCA EDA & Threshold Reverse-Engineering
=================================================================
Analyzes real_op (normal) vs dig_twin (fault) recordings to:
  1. Derive normal operating ranges for numeric variables
  2. Reverse-engineer PLC/NC thresholds from boolean variable patterns
  3. Compare activation patterns of alarms and binary signals across fault types
  4. Output comprehensive statistics and deviation analysis

Author: AI Pipeline (graduation project)
"""

import os
import json
import glob
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────
BASE = Path("/sessions/bold-confident-allen/mnt/plsgraduate/dataset_causRCA")
REAL_OP = BASE / "real_op"
DIG_TWIN = BASE / "dig_twin"
OUT_DIR = Path("/sessions/bold-confident-allen/mnt/plsgraduate/ai_pipeline/eda_results")
OUT_DIR.mkdir(parents=True, exist_ok=True)

FAULT_GROUPS = ["exp_coolant", "exp_hydraulics", "exp_probe"]

# ── Variable Classification ───────────────────────────────────────────────
NUMERIC_NODES = [
    "Spd_ActPos_C", "Spd_ActSpeed_C",
    "Spd_ActPos_X", "Spd_ActSpeed_X",
    "Spd_ActPos_Z", "Spd_ActSpeed_Z",
    "Prog_CuttingTime", "Prog_CycleTime",
    "M_PowerOnDuration", "Prog_LineNo",
    "Hyd_Pressure",
]

ALARM_NODES = [
    "CLF_A_700307", "F_A_700313", "HP_A_700304", "LT_A_700317",
    "CLT_A_700310", "LP_A_700301",
    "Hyd_A_700202", "Hyd_A_700203", "Hyd_A_700204",
    "Hyd_A_700205", "Hyd_A_700206", "Hyd_A_700207", "Hyd_A_700208",
    "MPA_A_701124", "MPA_A_701125",
    "SR_A_67040", "T_A_701309", "Prog_A_701330",
]

# Key binary signals (PLC-controlled, threshold-derived)
KEY_BINARY_NODES = [
    "CBC_Closed", "CBC_close", "CBC_isOpen", "CBC_open",
    "CLF_Filter_Ok", "CLT_Level_lt_Min",
    "ExU_On", "ExU_isOff",
    "F_Filter_Ok",
    "HP_Pump_Ok", "HP_Pump_isOff",
    "Hyd_Filter_Ok", "Hyd_IsEnabled", "Hyd_Level_Ok",
    "Hyd_Pump_Ok", "Hyd_Pump_On", "Hyd_Pump_isOff",
    "Hyd_Temp_lt_70", "Hyd_Temp_lt_80", "Hyd_Valve_P_Up",
    "LP_Pump_Ok", "LP_Pump_On", "LT_Level_Ok", "LT_Pump_Ok",
    "Lubr_On", "Lubr_P_Ok",
    "M_ErrorActive", "M_WarnActive", "M_WarnWithStacklight",
    "SL_Green", "SL_Red", "SL_Yellow",
    "MPA_InitPos", "MPA_WorkPos", "MPA_toInitPos", "MPA_toWorkPos",
    "MPC_Closed", "MPC_close", "MPC_isOpen", "MPC_open",
    "MP_Inactive",
    "SRL_Locked", "SRL_Unlocked",
    "Spd_InnerCoolOn", "Spd_OuterCoolOn",
    "TL_Locked", "TL_lock", "TL_unlock",
    "WC_inPos",
    "WCL_InitPos", "WCL_Locked", "WCL_Unlocked", "WCL_WorkPos",
    "WCL_toInitPos", "WCL_toWorkPos",
    "SR_loosen", "SR_tighten",
    "M_Lifebit",
]


# ── Helper: Load & Pivot CSV ──────────────────────────────────────────────
def parse_val(v):
    if v == "True" or v is True:
        return 1.0
    elif v == "False" or v is False:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return np.nan


def load_csv(path: str) -> pd.DataFrame:
    """Load a CausRCA CSV. Handles both long-format (time_s,node,value,type)
    and wide-format (time_s, <var1>, <var2>, ...) by converting wide to long."""
    df = pd.read_csv(path)

    if "node" in df.columns and "value" in df.columns:
        # Long format
        df["value_num"] = df["value"].apply(parse_val)
        return df
    else:
        # Wide format: melt to long format
        id_col = "time_s"
        value_cols = [c for c in df.columns if c != id_col]
        melted = df.melt(id_vars=[id_col], value_vars=value_cols,
                         var_name="node", value_name="value")
        melted["value"] = melted["value"].astype(str)
        melted["value_num"] = melted["value"].apply(parse_val)
        # Assign type based on value patterns
        melted["type"] = "Binary"  # default
        return melted


def extract_numeric_series(df: pd.DataFrame, nodes: list) -> dict:
    """Extract numeric time-series for specified nodes from a loaded DF."""
    result = {}
    for node in nodes:
        sub = df[df["node"] == node]
        if len(sub) > 0:
            vals = sub["value_num"].dropna().values
            if len(vals) > 0:
                result[node] = vals
    return result


def extract_binary_stats(df: pd.DataFrame, nodes: list) -> dict:
    """For each binary/alarm node, compute fraction of time it's True (=1)."""
    result = {}
    for node in nodes:
        sub = df[df["node"] == node]
        if len(sub) > 0:
            vals = sub["value_num"].dropna().values
            if len(vals) > 0:
                result[node] = {
                    "true_frac": float(np.mean(vals)),
                    "n_samples": len(vals),
                    "n_transitions": int(np.sum(np.abs(np.diff(vals)) > 0.5)),
                }
    return result


# ══════════════════════════════════════════════════════════════════════════
# PART 1: Normal Operating Profile from real_op
# ══════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("PART 1: Building Normal Operating Profile from real_op")
print("=" * 70)

real_files = sorted(glob.glob(str(REAL_OP / "*.csv")))
print(f"  Found {len(real_files)} normal-operation recordings")

# Accumulate numeric values across all files
normal_numeric = defaultdict(list)
normal_binary = defaultdict(lambda: {"true_sum": 0.0, "count": 0, "transitions": 0})

for i, fpath in enumerate(real_files):
    df = load_csv(fpath)

    # Numeric
    num_data = extract_numeric_series(df, NUMERIC_NODES)
    for node, vals in num_data.items():
        normal_numeric[node].extend(vals.tolist())

    # Binary + Alarm
    for node_list in [KEY_BINARY_NODES, ALARM_NODES]:
        bstats = extract_binary_stats(df, node_list)
        for node, st in bstats.items():
            normal_binary[node]["true_sum"] += st["true_frac"] * st["n_samples"]
            normal_binary[node]["count"] += st["n_samples"]
            normal_binary[node]["transitions"] += st["n_transitions"]

    if (i + 1) % 50 == 0:
        print(f"    Processed {i + 1}/{len(real_files)} files")

print(f"  Done processing {len(real_files)} files")

# ── Compute Normal Statistics ──────────────────────────────────────────────
normal_stats = {}
for node in NUMERIC_NODES:
    vals = np.array(normal_numeric.get(node, []))
    if len(vals) == 0:
        continue
    normal_stats[node] = {
        "count": len(vals),
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "p01": float(np.percentile(vals, 1)),
        "p05": float(np.percentile(vals, 5)),
        "p25": float(np.percentile(vals, 25)),
        "p50": float(np.percentile(vals, 50)),
        "p75": float(np.percentile(vals, 75)),
        "p95": float(np.percentile(vals, 95)),
        "p99": float(np.percentile(vals, 99)),
        # Threshold candidates: +-3sigma or percentile-based
        "thr_low_3sigma": float(np.mean(vals) - 3 * np.std(vals)),
        "thr_high_3sigma": float(np.mean(vals) + 3 * np.std(vals)),
        "thr_low_iqr": float(np.percentile(vals, 25) - 1.5 * (np.percentile(vals, 75) - np.percentile(vals, 25))),
        "thr_high_iqr": float(np.percentile(vals, 75) + 1.5 * (np.percentile(vals, 75) - np.percentile(vals, 25))),
    }

# Normal binary profile
normal_binary_profile = {}
for node in KEY_BINARY_NODES + ALARM_NODES:
    info = normal_binary.get(node)
    if info and info["count"] > 0:
        normal_binary_profile[node] = {
            "normal_true_frac": round(info["true_sum"] / info["count"], 4),
            "total_samples": info["count"],
            "total_transitions": info["transitions"],
        }

# ── Save Normal Stats ─────────────────────────────────────────────────────
df_normal = pd.DataFrame(normal_stats).T
df_normal.index.name = "variable"
df_normal.to_csv(OUT_DIR / "normal_numeric_stats.csv")
print(f"\n  Saved: normal_numeric_stats.csv ({len(df_normal)} variables)")

df_bin = pd.DataFrame(normal_binary_profile).T
df_bin.index.name = "variable"
df_bin.to_csv(OUT_DIR / "normal_binary_profile.csv")
print(f"  Saved: normal_binary_profile.csv ({len(df_bin)} variables)")

# Print summary
print("\n  ── Normal Numeric Summary ──")
for node, st in sorted(normal_stats.items()):
    print(f"    {node:25s}  mean={st['mean']:12.2f}  std={st['std']:12.2f}  "
          f"range=[{st['min']:12.2f}, {st['max']:12.2f}]  n={st['count']}")


# ══════════════════════════════════════════════════════════════════════════
# PART 2: Fault Deviation Analysis by Subsystem
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 2: Fault Deviation Analysis (per subsystem)")
print("=" * 70)

all_fault_deviations = []
all_fault_binary_changes = []

for group in FAULT_GROUPS:
    group_dir = DIG_TWIN / group
    exp_dirs = sorted([d for d in group_dir.iterdir() if d.is_dir() and d.name.startswith("exp_")])
    group_short = group.replace("exp_", "")

    print(f"\n  ── {group} ({len(exp_dirs)} experiments) ──")

    fault_numeric = defaultdict(list)
    fault_binary = defaultdict(lambda: {"true_sum": 0.0, "count": 0, "transitions": 0})
    fault_pre_numeric = defaultdict(list)   # before cause_start
    fault_post_numeric = defaultdict(list)  # after cause_start

    n_runs = 0
    for exp_dir in exp_dirs:
        # Load description
        desc_files = list(exp_dir.glob("*_description.json"))
        desc = {}
        if desc_files:
            with open(desc_files[0]) as f:
                desc = json.load(f)

        run_dirs = sorted([d for d in exp_dir.iterdir() if d.is_dir() and d.name.startswith("run_")])
        for run_dir in run_dirs:
            csv_files = list(run_dir.glob("*.csv"))
            causes_file = run_dir / "causes.json"

            if not csv_files:
                continue

            df = load_csv(str(csv_files[0]))
            n_runs += 1

            # Load cause timestamps
            cause_ts = {}
            if causes_file.exists():
                with open(causes_file) as f:
                    cause_ts = json.load(f)

            cause_start = cause_ts.get("cause_start_at", None)

            # Numeric
            num_data = extract_numeric_series(df, NUMERIC_NODES)
            for node, vals in num_data.items():
                fault_numeric[node].extend(vals.tolist())

                # Split pre/post cause
                if cause_start is not None:
                    sub = df[(df["node"] == node) & df["value_num"].notna()]
                    pre = sub[sub["time_s"] < cause_start]["value_num"].values
                    post = sub[sub["time_s"] >= cause_start]["value_num"].values
                    if len(pre) > 0:
                        fault_pre_numeric[node].extend(pre.tolist())
                    if len(post) > 0:
                        fault_post_numeric[node].extend(post.tolist())

            # Binary + Alarm
            for node_list in [KEY_BINARY_NODES, ALARM_NODES]:
                bstats = extract_binary_stats(df, node_list)
                for node, st in bstats.items():
                    fault_binary[node]["true_sum"] += st["true_frac"] * st["n_samples"]
                    fault_binary[node]["count"] += st["n_samples"]
                    fault_binary[node]["transitions"] += st["n_transitions"]

    print(f"    Total runs: {n_runs}")

    # ── Numeric Deviation Analysis ─────────────────────────────────────────
    for node in NUMERIC_NODES:
        if node not in fault_numeric or node not in normal_stats:
            continue

        fvals = np.array(fault_numeric[node])
        ns = normal_stats[node]

        # Count outliers vs normal range
        n_below_low = int(np.sum(fvals < ns["thr_low_3sigma"]))
        n_above_high = int(np.sum(fvals > ns["thr_high_3sigma"]))
        n_below_iqr = int(np.sum(fvals < ns["thr_low_iqr"]))
        n_above_iqr = int(np.sum(fvals > ns["thr_high_iqr"]))

        # Pre vs post cause
        pre_mean = float(np.mean(fault_pre_numeric.get(node, [0])))
        post_mean = float(np.mean(fault_post_numeric.get(node, [0])))

        row = {
            "fault_group": group_short,
            "variable": node,
            "fault_mean": float(np.mean(fvals)),
            "fault_std": float(np.std(fvals)),
            "fault_min": float(np.min(fvals)),
            "fault_max": float(np.max(fvals)),
            "normal_mean": ns["mean"],
            "normal_std": ns["std"],
            "mean_shift": float(np.mean(fvals)) - ns["mean"],
            "mean_shift_sigma": (float(np.mean(fvals)) - ns["mean"]) / (ns["std"] + 1e-10),
            "n_outlier_3sigma": n_below_low + n_above_high,
            "pct_outlier_3sigma": round(100.0 * (n_below_low + n_above_high) / len(fvals), 2),
            "n_outlier_iqr": n_below_iqr + n_above_iqr,
            "pct_outlier_iqr": round(100.0 * (n_below_iqr + n_above_iqr) / len(fvals), 2),
            "pre_cause_mean": pre_mean,
            "post_cause_mean": post_mean,
            "pre_post_shift": post_mean - pre_mean,
            "n_fault_samples": len(fvals),
        }
        all_fault_deviations.append(row)

    # ── Binary Deviation Analysis ──────────────────────────────────────────
    for node in KEY_BINARY_NODES + ALARM_NODES:
        info = fault_binary.get(node)
        normal_info = normal_binary_profile.get(node)
        if info is None or info["count"] == 0:
            continue

        fault_true_frac = info["true_sum"] / info["count"]
        normal_true_frac = normal_info["normal_true_frac"] if normal_info else 0.0

        row = {
            "fault_group": group_short,
            "variable": node,
            "fault_true_frac": round(fault_true_frac, 4),
            "normal_true_frac": round(normal_true_frac, 4),
            "delta_true_frac": round(fault_true_frac - normal_true_frac, 4),
            "abs_delta": round(abs(fault_true_frac - normal_true_frac), 4),
            "fault_transitions": info["transitions"],
            "fault_samples": info["count"],
            "is_alarm": node in ALARM_NODES,
        }
        all_fault_binary_changes.append(row)

# ── Save Fault Analysis ───────────────────────────────────────────────────
df_dev = pd.DataFrame(all_fault_deviations)
df_dev.to_csv(OUT_DIR / "fault_numeric_deviations.csv", index=False)
print(f"\n  Saved: fault_numeric_deviations.csv ({len(df_dev)} rows)")

df_bchange = pd.DataFrame(all_fault_binary_changes)
df_bchange.to_csv(OUT_DIR / "fault_binary_changes.csv", index=False)
print(f"  Saved: fault_binary_changes.csv ({len(df_bchange)} rows)")


# ══════════════════════════════════════════════════════════════════════════
# PART 3: Threshold Reverse-Engineering
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 3: Reverse-Engineered Thresholds")
print("=" * 70)

thresholds = {}
for node, st in normal_stats.items():
    thresholds[node] = {
        "normal_range_min": round(st["p01"], 4),
        "normal_range_max": round(st["p99"], 4),
        "recommended_low_threshold": round(st["thr_low_3sigma"], 4),
        "recommended_high_threshold": round(st["thr_high_3sigma"], 4),
        "iqr_low_threshold": round(st["thr_low_iqr"], 4),
        "iqr_high_threshold": round(st["thr_high_iqr"], 4),
        "operating_mean": round(st["mean"], 4),
        "operating_std": round(st["std"], 4),
    }

# Analyze boolean thresholds from expert graph edges
# Known threshold relationships from expert_graph/all_edges.csv:
# - Hyd_Temp_lt_70: True when hydraulic oil temp < 70°C
# - Hyd_Temp_lt_80: True when hydraulic oil temp < 80°C
# These define implicit thresholds the PLC uses.
threshold_rules = {
    "Hyd_Temp_lt_70": {"threshold": 70, "unit": "°C", "condition": "temp < 70°C → True"},
    "Hyd_Temp_lt_80": {"threshold": 80, "unit": "°C", "condition": "temp < 80°C → True"},
    "CLT_Level_lt_Min": {"threshold": "min_level", "unit": "level", "condition": "level < min → True (fault)"},
    "M_ErrorActive": {"threshold": "any_error", "condition": "any alarm active → True"},
    "SL_Red": {"threshold": "error_state", "condition": "fault/stop → True"},
    "SL_Yellow": {"threshold": "warning_state", "condition": "warning present → True"},
    "SL_Green": {"threshold": "all_ok", "condition": "all OK → True"},
}

# Save thresholds
df_thr = pd.DataFrame(thresholds).T
df_thr.index.name = "variable"
df_thr.to_csv(OUT_DIR / "derived_thresholds.csv")
print(f"  Saved: derived_thresholds.csv ({len(df_thr)} variables)")

with open(OUT_DIR / "plc_threshold_rules.json", "w") as f:
    json.dump(threshold_rules, f, indent=2, default=str)
print(f"  Saved: plc_threshold_rules.json")

# Print derived thresholds
for node, thr in sorted(thresholds.items()):
    print(f"    {node:25s}  normal=[{thr['normal_range_min']:12.2f}, {thr['normal_range_max']:12.2f}]  "
          f"3σ=[{thr['recommended_low_threshold']:12.2f}, {thr['recommended_high_threshold']:12.2f}]")


# ══════════════════════════════════════════════════════════════════════════
# PART 4: Key Findings Summary
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 4: Key Findings")
print("=" * 70)

# Top deviating variables per fault group
if len(df_dev) > 0:
    print("\n  ── Top Deviating Numeric Variables by Fault Group ──")
    for grp in df_dev["fault_group"].unique():
        sub = df_dev[df_dev["fault_group"] == grp].sort_values("pct_outlier_3sigma", ascending=False)
        print(f"\n    [{grp}] Top 5 by 3σ outlier %:")
        for _, row in sub.head(5).iterrows():
            print(f"      {row['variable']:25s}  outlier%={row['pct_outlier_3sigma']:6.2f}%  "
                  f"mean_shift={row['mean_shift']:12.2f}  shift_σ={row['mean_shift_sigma']:8.2f}")

# Top changing binary/alarm variables
if len(df_bchange) > 0:
    print("\n  ── Top Changing Binary/Alarm Variables by Fault Group ──")
    for grp in df_bchange["fault_group"].unique():
        sub = df_bchange[df_bchange["fault_group"] == grp].sort_values("abs_delta", ascending=False)
        print(f"\n    [{grp}] Top 10 by |Δ true_frac|:")
        for _, row in sub.head(10).iterrows():
            marker = " *** ALARM" if row["is_alarm"] else ""
            print(f"      {row['variable']:25s}  normal={row['normal_true_frac']:.4f}  "
                  f"fault={row['fault_true_frac']:.4f}  Δ={row['delta_true_frac']:+.4f}{marker}")

# ── Save full summary report ──────────────────────────────────────────────
summary = {
    "dataset": {
        "real_op_files": len(real_files),
        "fault_groups": FAULT_GROUPS,
        "numeric_variables": len(normal_stats),
        "binary_variables": len(normal_binary_profile),
    },
    "normal_stats": normal_stats,
    "thresholds": thresholds,
    "plc_rules": threshold_rules,
}

with open(OUT_DIR / "eda_summary.json", "w") as f:
    json.dump(summary, f, indent=2, default=str)
print(f"\n  Saved: eda_summary.json")

print("\n" + "=" * 70)
print("EDA COMPLETE. All results saved to:", OUT_DIR)
print("=" * 70)
