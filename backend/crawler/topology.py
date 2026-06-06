"""
Topology comparison and canonicalization utilities.

Extracted from ``backend.py:canonical_topology()`` so the same logic
can be reused by M8 (Drift Detection) and unit tests.
"""

import json
from typing import Dict, List, Optional


def canonical_topology(topo: Optional[dict]) -> dict:
    """
    Return a normalized version of the topology for diff comparison.

    *   Removes ephemeral fields like ``timestamp``
    *   Marks unreachable nodes as ``down``
    *   Extracts and sorts links for consistent comparison

    This is a **pure function** — it does not mutate the input.
    """
    if not topo:
        return {"nodes": {}, "links": []}

    nodes_dict = topo.get("nodes", {})
    if not isinstance(nodes_dict, dict):
        nodes_dict = {}

    cleaned_nodes: Dict[str, dict] = {}
    for node_id, node_data in nodes_dict.items():
        if not isinstance(node_data, dict):
            continue
        cleaned_node = {k: v for k, v in node_data.items() if k != "timestamp"}

        # Sort connectedNode array for consistent comparison
        if "connectedNode" in cleaned_node and isinstance(
            cleaned_node["connectedNode"], list
        ):
            cleaned_node["connectedNode"] = sorted(cleaned_node["connectedNode"])

        # Handle "down" field consistently
        has_interfaces = cleaned_node.get("interfaceList") and any(
            bool(i) for i in cleaned_node.get("interfaceList", [{}])
        )
        has_links = cleaned_node.get("edgeLink") and bool(
            cleaned_node.get("edgeLink", {})
        )

        if "down" in node_data:
            cleaned_node["down"] = node_data["down"]
        elif not has_interfaces and not has_links:
            cleaned_node["down"] = True
        else:
            cleaned_node.pop("down", None)

        cleaned_nodes[node_id] = cleaned_node

    # Extract links from edge data
    links: List[dict] = []
    for node_id, node_data in cleaned_nodes.items():
        edge_links = node_data.get("edgeLink", {})
        if isinstance(edge_links, dict):
            for local_if, link_info in edge_links.items():
                if isinstance(link_info, dict):
                    remote_node = link_info.get("remote_node")
                    if remote_node:
                        links.append(
                            {
                                "src": node_id,
                                "dst": remote_node,
                                "src_interface": local_if,
                                "dst_interface": link_info.get(
                                    "remote_interface"
                                ),
                                "remote_ip": link_info.get("remote_ip"),
                            }
                        )

    sorted_nodes = dict(sorted(cleaned_nodes.items()))
    sorted_links = sorted(
        links, key=lambda x: (x.get("src", ""), x.get("dst", ""))
    )

    return {"nodes": sorted_nodes, "links": sorted_links}


def topology_changed(
    previous: Optional[dict], current: Optional[dict]
) -> bool:
    """
    Compare two topology snapshots and return ``True`` if they differ.

    Both inputs should be the *raw* topology dicts (before
    canonicalization).  The function canonicalizes internally.
    """
    if previous is None:
        return True  # First run always counts as "changed"

    prev_canon = canonical_topology(previous)
    curr_canon = canonical_topology(current) if current else canonical_topology({})

    return json.dumps(prev_canon, sort_keys=True) != json.dumps(
        curr_canon, sort_keys=True
    )
