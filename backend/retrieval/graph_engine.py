import logging
import json
import time
from typing import Dict, Any, List

logger = logging.getLogger("retrieval.graph_engine")

try:
    import networkx as nx
except ImportError:
    logger.debug("networkx not installed; falling back to internal graph representations.")
    nx = None

class InMemoryNodeView:
    def __init__(self, nodes_dict):
        self._nodes = nodes_dict

    def __getitem__(self, node):
        return self._nodes[node]

    def __contains__(self, node):
        return node in self._nodes

    def __iter__(self):
        return iter(self._nodes)

    def keys(self):
        return self._nodes.keys()

    def items(self):
        return self._nodes.items()

    def values(self):
        return self._nodes.values()

    def __call__(self, data=False):
        if data:
            return list(self._nodes.items())
        return list(self._nodes.keys())

class InMemoryGraph:
    """Fallback graph class matching NetworkX interfaces for pathing and blast radius."""
    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._adj: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def clear(self):
        self._nodes.clear()
        self._adj.clear()

    @property
    def nodes(self):
        return InMemoryNodeView(self._nodes)

    def add_node(self, node: str, **attrs):
        if node not in self._nodes:
            self._nodes[node] = {}
        self._nodes[node].update(attrs)
        if node not in self._adj:
            self._adj[node] = {}

    def add_edge(self, u: str, v: str, **attrs):
        self.add_node(u)
        self.add_node(v)
        self._adj[u].setdefault(v, []).append(attrs)
        self._adj[v].setdefault(u, []).append(attrs)

    def remove_node(self, node: str):
        if node in self._nodes:
            del self._nodes[node]
        if node in self._adj:
            for neighbor in list(self._adj[node].keys()):
                # Remove edges matching node
                if node in self._adj[neighbor]:
                    del self._adj[neighbor][node]
            del self._adj[node]

    def neighbors(self, node: str) -> List[str]:
        return list(self._adj.get(node, {}).keys())

    def has_path(self, u: str, v: str) -> bool:
        if u not in self._nodes or v not in self._nodes:
            return False
        visited = {u}
        queue = [u]
        while queue:
            curr = queue.pop(0)
            if curr == v:
                return True
            for neighbor in self.neighbors(curr):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False

    def copy(self):
        g = InMemoryGraph()
        g._nodes = {k: dict(v) for k, v in self._nodes.items()}
        g._adj = {u: {v: list(edges) for v, edges in neighbors.items()} for u, neighbors in self._adj.items()}
        return g


class TopologyGraphManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TopologyGraphManager, cls).__new__(cls)
            if nx is not None:
                cls._instance.planned_graph = nx.MultiGraph()
                cls._instance.observed_graph = nx.MultiGraph()
            else:
                cls._instance.planned_graph = InMemoryGraph()
                cls._instance.observed_graph = InMemoryGraph()
        return cls._instance

    def rebuild_graph_from_db(self, db_mgr):
        """Query physical relationships table and build topology baseline."""
        self.planned_graph = nx.MultiGraph() if nx is not None else InMemoryGraph()
        self.observed_graph = nx.MultiGraph() if nx is not None else InMemoryGraph()
        
        try:
            with db_mgr._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, hostname, role, status FROM devices")
                devices = cursor.fetchall()
                for row in devices:
                    dev_id, host, role, status = row
                    self.planned_graph.add_node(host, role=role, status=status)
                    self.observed_graph.add_node(host, role=role, status=status)
                    
                cursor.execute("""
                    SELECT d1.hostname, d2.hostname, i1.name, i2.name, l.discovery_protocol
                    FROM links l
                    JOIN devices d1 ON l.src_device_id = d1.id
                    JOIN devices d2 ON l.dst_device_id = d2.id
                    LEFT JOIN interfaces i1 ON l.src_interface_id = i1.id
                    LEFT JOIN interfaces i2 ON l.dst_interface_id = i2.id
                """)
                links = cursor.fetchall()
                for row in links:
                    src, dst, src_port, dst_port, proto = row
                    self.planned_graph.add_edge(src, dst, port_a=src_port, port_b=dst_port, protocol=proto)
                    # Populate default observed physical connections as up
                    self.observed_graph.add_edge(src, dst, port_a=src_port, port_b=dst_port, status="UP")
            
            logger.info("Successfully rebuilt baseline topology graph from DB cache.")
        except Exception as e:
            logger.error("Failed to rebuild topology graph: %s", e)

    def update_observed_link(self, u: str, v: str, status: str):
        """Update interface connectivity status dynamically on the observed topology graph."""
        if nx is not None:
            if self.observed_graph.has_edge(u, v):
                # Update all edges between u and v
                for key in self.observed_graph[u][v]:
                    self.observed_graph[u][v][key]["status"] = status
                    self.observed_graph[u][v][key]["last_update"] = time.time()
        else:
            if v in self.observed_graph._adj.get(u, {}):
                for edge in self.observed_graph._adj[u][v]:
                    edge["status"] = status
                    edge["last_update"] = time.time()

    def add_logical_peering(self, u: str, v: str, peering_type: str, status: str):
        """Add BGP/OSPF logical protocol peerings to the observed graph overlay."""
        self.observed_graph.add_edge(u, v, type=peering_type, status=status)

    def are_adjacent(self, u: str, v: str) -> bool:
        if u not in self.observed_graph.nodes or v not in self.observed_graph.nodes:
            return False
        if nx is not None:
            return self.observed_graph.has_edge(u, v)
        else:
            return v in self.observed_graph._adj.get(u, {})

    def calculate_blast_radius(self, failed_node: str) -> List[str]:
        """Identify nodes that lose paths to core gateways/routers if failed_node fails."""
        if failed_node not in self.observed_graph.nodes:
            return []

        temp_graph = self.observed_graph.copy()
        temp_graph.remove_node(failed_node)

        # Get core nodes
        cores = [n for n, attr in self.observed_graph.nodes.items() if attr.get("role") == "core"]
        impacted_nodes = []

        # Check path for non-core nodes
        for node in temp_graph.nodes.keys():
            if self.observed_graph.nodes[node].get("role") == "core":
                continue
            # If no path remains to any core, it's impacted
            if nx is not None:
                has_active_core_path = any(nx.has_path(temp_graph, node, core) for core in cores)
            else:
                has_active_core_path = any(temp_graph.has_path(node, core) for core in cores)
                
            if not has_active_core_path:
                impacted_nodes.append(node)

        return impacted_nodes

    def serialize_snapshot(self) -> str:
        """Serializes observed graph representation to JSON."""
        nodes_list = []
        for name, attrs in self.planned_graph.nodes.items():
            nodes_list.append({"id": name, **attrs})
        return json.dumps(nodes_list)
