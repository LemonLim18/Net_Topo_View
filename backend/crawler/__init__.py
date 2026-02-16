"""
M2: Topology Crawler — BFS Discovery Engine.

Auto-discovers the entire network from seed devices using BFS traversal
via LLDP/CDP. Builds the topology graph that the frontend visualises
and the LLM reasons about.

Usage:
    from backend.crawler import crawl_topology

    topo = crawl_topology(
        seeds=["192.168.1.123"],
        username="admin",
        password="cisco",
        driver_type="cisco_ios",
    )

Legacy entry point (backend/mini_nce.py) is preserved and unchanged.
"""

from backend.crawler.bfs_engine import crawl_topology  # noqa: F401

__all__ = ["crawl_topology"]
