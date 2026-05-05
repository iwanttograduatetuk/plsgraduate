"""
02_fault_tree.py - CNC Fault Tree Construction
Builds fault trees per subsystem (coolant, hydraulics, probe) from expert graph
and experiment descriptions / causes.json files.

Output: ai_pipeline/fault_tree_results/
"""

import os
import json
import csv
from collections import defaultdict
from pathlib import Path

# ---- paths ----
BASE = Path(__file__).resolve().parent.parent
DATASET = BASE / "dataset_causRCA"
GRAPH_DIR = DATASET / "expert_graph"
DIG_TWIN = DATASET / "dig_twin"
OUT_DIR = Path(__file__).resolve().parent / "fault_tree_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SUBSYSTEMS = {
    "coolant":    DIG_TWIN / "exp_coolant",
    "hydraulics": DIG_TWIN / "exp_hydraulics",
    "probe":      DIG_TWIN / "exp_probe",
}

# ---- load expert graph ----
def load_nodes(path):
    nodes = {}
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nodes[int(row["id"])] = {
                "label": row["label"],
                "type": row["type"],
                "path": row["path"],
                "io": row.get("io", ""),
                "datatype": row.get("datatype", ""),
                "comment": row.get("comment", ""),
            }
    return nodes

def load_edges(path):
    edges = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            edges.append({
                "source_id": int(row["source_id"]),
                "target_id": int(row["target_id"]),
                "source_label": row["source_label"],
                "target_label": row["target_label"],
                "edge_name": row["edge_name"],
                "comment": row.get("comment", ""),
            })
    return edges

def load_experiment_descriptions(subsystem_dir):
    """Load all exp_*_description.json files in a subsystem directory."""
    descriptions = []
    for exp_dir in sorted(subsystem_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        for f in exp_dir.glob("*_description.json"):
            with open(f) as fh:
                desc = json.load(fh)
                descriptions.append(desc)
    return descriptions

def load_causes(subsystem_dir):
    """Load all causes.json files from run sub-directories."""
    causes_list = []
    for exp_dir in sorted(subsystem_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        for run_dir in sorted(exp_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            cf = run_dir / "causes.json"
            if cf.exists():
                with open(cf) as fh:
                    c = json.load(fh)
                    c["_exp"] = exp_dir.name
                    c["_run"] = run_dir.name
                    causes_list.append(c)
    return causes_list


def build_fault_tree(nodes, edges, descriptions, causes, subsystem_name):
    """
    Build a fault tree for one subsystem.
    Structure:
      TOP event (subsystem fault)
        -> Intermediate events (alarms triggered)
          -> Root causes (manipulated variables)
            -> Propagation paths (from expert graph edges)
    """
    tree = {
        "subsystem": subsystem_name,
        "top_event": f"{subsystem_name.upper()}_SYSTEM_FAULT",
        "experiments": [],
        "fault_scenarios": [],
        "causal_chains": [],
        "timing_stats": {},
    }

    # Build adjacency from expert graph (source -> targets)
    adj_forward = defaultdict(list)   # source_label -> [(target_label, edge_name, comment)]
    adj_backward = defaultdict(list)  # target_label -> [(source_label, edge_name, comment)]
    for e in edges:
        adj_forward[e["source_label"]].append((e["target_label"], e["edge_name"], e["comment"]))
        adj_backward[e["target_label"]].append((e["source_label"], e["edge_name"], e["comment"]))

    # label -> node info
    label_map = {n["label"]: n for n in nodes.values()}

    # Process each experiment description
    for desc in descriptions:
        exp_id = desc["exp_id"]
        manipulated = desc.get("manipulatedVars", [])
        alarms = desc.get("alarms", [])
        diagnoses = desc.get("diagnoses", [])
        scenario = desc.get("scenario", {})

        scenario_entry = {
            "exp_id": exp_id,
            "description": scenario.get("instead_failure", ""),
            "root_causes": manipulated,
            "triggered_alarms": alarms,
            "diagnoses": [d["name"] for d in diagnoses],
        }
        tree["fault_scenarios"].append(scenario_entry)

        # Build causal chains: root_cause -> alarm -> system effects
        for root_var in manipulated:
            chain = {
                "root_cause": root_var,
                "propagation": [],
            }
            # BFS from root var through the expert graph
            visited = set()
            queue = [root_var]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                for target, edge_name, comment in adj_forward.get(current, []):
                    chain["propagation"].append({
                        "from": current,
                        "to": target,
                        "mechanism": edge_name,
                        "description": comment.strip(),
                    })
                    if target not in visited:
                        queue.append(target)
            tree["causal_chains"].append(chain)

    # Timing statistics from causes.json
    timing_keys = ["cause_start_at", "alarms_detected_at", "diagnosis_at",
                    "cause_end_at", "alarms_resolved_at"]
    timing_data = {k: [] for k in timing_keys}
    detection_delays = []
    diagnosis_delays = []
    resolution_delays = []

    for c in causes:
        for k in timing_keys:
            if k in c:
                timing_data[k].append(c[k])
        if "cause_start_at" in c and "alarms_detected_at" in c:
            detection_delays.append(c["alarms_detected_at"] - c["cause_start_at"])
        if "cause_start_at" in c and "diagnosis_at" in c:
            diagnosis_delays.append(c["diagnosis_at"] - c["cause_start_at"])
        if "cause_start_at" in c and "alarms_resolved_at" in c:
            resolution_delays.append(c["alarms_resolved_at"] - c["cause_start_at"])

    def stats(arr):
        if not arr:
            return {}
        arr_s = sorted(arr)
        n = len(arr_s)
        return {
            "count": n,
            "mean": sum(arr_s) / n,
            "min": arr_s[0],
            "max": arr_s[-1],
            "median": arr_s[n // 2],
        }

    tree["timing_stats"] = {
        "detection_delay_sec": stats(detection_delays),
        "diagnosis_delay_sec": stats(diagnosis_delays),
        "resolution_delay_sec": stats(resolution_delays),
        "total_runs": len(causes),
    }

    # Experiment summary
    tree["experiments"] = [
        {"exp_id": d["exp_id"], "group": d.get("group", subsystem_name)}
        for d in descriptions
    ]

    return tree


def render_text_tree(tree, nodes_map):
    """Render fault tree as readable text."""
    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"FAULT TREE: {tree['top_event']}")
    lines.append(f"Subsystem: {tree['subsystem']}")
    lines.append(f"Total experiments: {len(tree['experiments'])}")
    lines.append(f"Total fault runs: {tree['timing_stats'].get('total_runs', 0)}")
    lines.append(f"{'='*70}")
    lines.append("")

    # Timing
    ts = tree["timing_stats"]
    if ts.get("detection_delay_sec"):
        dd = ts["detection_delay_sec"]
        lines.append(f"  Detection delay: mean={dd['mean']:.1f}s, "
                      f"min={dd['min']:.1f}s, max={dd['max']:.1f}s")
    if ts.get("diagnosis_delay_sec"):
        dd = ts["diagnosis_delay_sec"]
        lines.append(f"  Diagnosis delay: mean={dd['mean']:.1f}s, "
                      f"min={dd['min']:.1f}s, max={dd['max']:.1f}s")
    if ts.get("resolution_delay_sec"):
        dd = ts["resolution_delay_sec"]
        lines.append(f"  Resolution time: mean={dd['mean']:.1f}s, "
                      f"min={dd['min']:.1f}s, max={dd['max']:.1f}s")
    lines.append("")

    # Fault scenarios
    lines.append("FAULT SCENARIOS:")
    lines.append("-" * 50)
    for sc in tree["fault_scenarios"]:
        lines.append(f"  [{sc['exp_id']}] {sc['description']}")
        lines.append(f"    Root causes: {', '.join(sc['root_causes'])}")
        lines.append(f"    Alarms:      {', '.join(sc['triggered_alarms'])}")
        for d in sc["diagnoses"]:
            lines.append(f"    Diagnosis:   {d}")
        lines.append("")

    # Causal chains
    lines.append("CAUSAL PROPAGATION CHAINS:")
    lines.append("-" * 50)
    seen_roots = set()
    for chain in tree["causal_chains"]:
        root = chain["root_cause"]
        if root in seen_roots:
            continue
        seen_roots.add(root)
        lines.append(f"  ROOT: {root}")
        for step in chain["propagation"]:
            lines.append(f"    {step['from']} -> {step['to']}")
            lines.append(f"      [{step['mechanism']}] {step['description']}")
        lines.append("")

    return "\n".join(lines)


# ---- main ----
def main():
    print("Loading expert graph...")
    nodes = load_nodes(GRAPH_DIR / "all_nodes.csv")
    edges = load_edges(GRAPH_DIR / "all_edges.csv")
    label_map = {n["label"]: n for n in nodes.values()}

    all_trees = {}

    for subsystem_name, subsystem_dir in SUBSYSTEMS.items():
        print(f"\nBuilding fault tree for: {subsystem_name}")
        descriptions = load_experiment_descriptions(subsystem_dir)
        causes = load_causes(subsystem_dir)
        print(f"  Experiments: {len(descriptions)}, Runs: {len(causes)}")

        tree = build_fault_tree(nodes, edges, descriptions, causes, subsystem_name)
        all_trees[subsystem_name] = tree

        # Save JSON
        json_path = OUT_DIR / f"fault_tree_{subsystem_name}.json"
        with open(json_path, "w") as f:
            json.dump(tree, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {json_path}")

        # Save text report
        txt_path = OUT_DIR / f"fault_tree_{subsystem_name}.txt"
        txt = render_text_tree(tree, label_map)
        with open(txt_path, "w") as f:
            f.write(txt)
        print(f"  Saved: {txt_path}")

    # Combined summary
    summary = {
        "subsystems": list(all_trees.keys()),
        "summary": {},
    }
    for name, tree in all_trees.items():
        scenarios = tree["fault_scenarios"]
        all_roots = set()
        all_alarms = set()
        for sc in scenarios:
            all_roots.update(sc["root_causes"])
            all_alarms.update(sc["triggered_alarms"])
        summary["summary"][name] = {
            "num_experiments": len(tree["experiments"]),
            "num_runs": tree["timing_stats"].get("total_runs", 0),
            "num_scenarios": len(scenarios),
            "root_cause_variables": sorted(all_roots),
            "triggered_alarms": sorted(all_alarms),
            "timing": tree["timing_stats"],
        }

    summary_path = OUT_DIR / "fault_tree_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSaved combined summary: {summary_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("FAULT TREE CONSTRUCTION SUMMARY")
    print("=" * 60)
    for name, s in summary["summary"].items():
        print(f"\n{name.upper()}:")
        print(f"  Experiments: {s['num_experiments']}, Runs: {s['num_runs']}")
        print(f"  Root causes: {', '.join(s['root_cause_variables'])}")
        print(f"  Alarms: {', '.join(s['triggered_alarms'])}")
        t = s["timing"]
        if t.get("detection_delay_sec"):
            print(f"  Avg detection delay: {t['detection_delay_sec']['mean']:.1f}s")


if __name__ == "__main__":
    main()
