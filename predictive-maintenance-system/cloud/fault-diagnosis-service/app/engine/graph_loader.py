"""
Expert Graph 로더
────────────────────────────────────────────────
causRCA 데이터셋의 expert_graph (all_nodes.csv, all_edges.csv)를
NetworkX 그래프로 로드하여 인과 추론에 활용.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Set

import networkx as nx
import pandas as pd

logger = logging.getLogger(__name__)


class ExpertGraph:
    """
    expert_graph.gml 또는 nodes/edges CSV 기반 인과 그래프.
    Fault Diagnosis 모델의 입력으로 활용.
    """

    def __init__(self, graph_dir: Path):
        self._dir = graph_dir
        self._graph: nx.DiGraph = nx.DiGraph()
        self._node_info: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        gml_path   = self._dir / "expert_graph.gml"
        nodes_path = self._dir / "all_nodes.csv"
        edges_path = self._dir / "all_edges.csv"

        if gml_path.exists():
            self._graph = nx.read_gml(gml_path)
            logger.info("Expert Graph 로드 (GML): %d nodes, %d edges",
                        self._graph.number_of_nodes(), self._graph.number_of_edges())
        elif nodes_path.exists() and edges_path.exists():
            nodes_df = pd.read_csv(nodes_path)
            edges_df = pd.read_csv(edges_path)
            for _, row in nodes_df.iterrows():
                self._graph.add_node(row["label"], **row.to_dict())
                self._node_info[row["label"]] = row.to_dict()
            for _, row in edges_df.iterrows():
                self._graph.add_edge(row["source"], row["target"])
            logger.info("Expert Graph 로드 (CSV): %d nodes, %d edges",
                        self._graph.number_of_nodes(), self._graph.number_of_edges())
        else:
            logger.warning("Expert Graph 파일 없음 — 빈 그래프 사용")

    def ancestors(self, node: str) -> Set[str]:
        """노드의 모든 조상 (인과 상위 노드)"""
        if node not in self._graph:
            return set()
        return nx.ancestors(self._graph, node)

    def causal_path_length(self, source: str, target: str) -> int:
        """source → target 경로 길이 (-1이면 경로 없음)"""
        try:
            return nx.shortest_path_length(self._graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return -1

    def subsystem_nodes(self, subsystem: str) -> List[str]:
        """특정 서브시스템에 속하는 노드 목록"""
        result = []
        for node, data in self._graph.nodes(data=True):
            path = data.get("path", "")
            if subsystem.lower() in path.lower():
                result.append(node)
        return result

    @property
    def graph(self) -> nx.DiGraph:
        return self._graph
