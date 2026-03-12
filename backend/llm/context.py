"""
Network context builder for LLM prompts.

Assembles relevant network state data into a structured context string
that is injected into the system prompt, giving the LLM situational
awareness about the current topology, device health, and capabilities.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    QueryRoutingDecision,
    build_authoritative_context_contract,
    build_source_matrix,
    classify_query,
    compute_freshness,
)
from llm.semantics import build_canonical_semantics_prompt_text

logger = logging.getLogger("llm.context")


class ContextBuilder:
    """
    Builds a contextual prompt from live network data.

    Injected into the system prompt so the LLM knows what devices exist,
    their status, interfaces, and links — without the user having to
    describe the network manually.
    """

    def __init__(self, db_manager=None):
        """
        Args:
            db_manager: Optional ``DatabaseManager`` instance for
                        querying live topology and device data.
        """
        self._db = db_manager

    def build_topology_context(self) -> str:
        """
        Build a human-readable summary of the current network topology.

        Returns an empty string if no database or no topology available.
        """
        if not self._db:
            return "(No topology data available — database not connected)"

        topo = self._db.get_topology()
        if not topo or not topo.get("nodes"):
            return "(No topology data available — network has not been crawled yet)"

        nodes = topo.get("nodes", {})
        timestamp = topo.get("timestamp", "unknown")
        seeds = topo.get("seeds", [])
        freshness = compute_freshness(
            domain=CONTEXT_DOMAIN_LIVE_TOPOLOGY,
            captured_at=timestamp if timestamp != "unknown" else None,
            ingested_at=timestamp if timestamp != "unknown" else None,
            source="crawler_cache",
            scope="live_topology",
            confidence=0.9 if nodes else 0.0,
        )

        lines = [
            f"## Current Network Topology",
            f"Freshness: {freshness.to_prompt_text()}",
            f"Last crawled: {timestamp}",
            f"Seed devices: {', '.join(seeds)}",
            f"Total devices: {len(nodes)}",
            "",
        ]

        for hostname, data in nodes.items():
            if not isinstance(data, dict):
                continue

            mgmt_ip = data.get("mgmt_ip", "N/A")
            status = "DOWN" if data.get("down") else "UP"
            connected = data.get("connectedNode", [])
            ifaces = data.get("interfaceList", [{}])
            edge_links = data.get("edgeLink", {})

            # Count interfaces with IPs
            iface_dict = ifaces[0] if ifaces else {}
            iface_count = len(iface_dict) if isinstance(iface_dict, dict) else 0

            lines.append(f"### {hostname} ({status})")
            lines.append(f"  Management IP: {mgmt_ip}")
            lines.append(f"  Interfaces: {iface_count}")
            lines.append(f"  Connected to: {', '.join(connected) if connected else 'none'}")

            # Show edge links
            if isinstance(edge_links, dict) and edge_links:
                for local_if, link_info in edge_links.items():
                    if isinstance(link_info, dict):
                        remote = link_info.get("remote_node", "?")
                        remote_if = link_info.get("remote_interface", "?")
                        remote_ip = link_info.get("remote_ip", "")
                        ip_str = f" ({remote_ip})" if remote_ip else ""
                        lines.append(
                            f"    {local_if} → {remote}:{remote_if}{ip_str}"
                        )
            lines.append("")

        return "\n".join(lines)

    def build_device_context(self, hostname: str) -> str:
        """Build detailed context for a specific device."""
        if not self._db:
            return f"(No data available for {hostname})"

        topo = self._db.get_topology()
        if not topo or not topo.get("nodes", {}).get(hostname):
            return f"(Device '{hostname}' not found in current topology)"

        node = topo["nodes"][hostname]
        lines = [f"## Device: {hostname}"]

        mgmt_ip = node.get("mgmt_ip", "N/A")
        lines.append(f"Management IP: {mgmt_ip}")
        lines.append(f"Status: {'DOWN' if node.get('down') else 'UP'}")

        # Interfaces
        ifaces = node.get("interfaceList", [{}])
        iface_dict = ifaces[0] if ifaces else {}
        if isinstance(iface_dict, dict) and iface_dict:
            lines.append("\nInterfaces:")
            for name, ip in iface_dict.items():
                lines.append(f"  {name}: {ip}")

        # Neighbors
        connected = node.get("connectedNode", [])
        if connected:
            lines.append(f"\nConnected neighbors: {', '.join(connected)}")

        # Edge links
        edge_links = node.get("edgeLink", {})
        if isinstance(edge_links, dict) and edge_links:
            lines.append("\nLinks:")
            for local_if, link_info in edge_links.items():
                if isinstance(link_info, dict):
                    remote = link_info.get("remote_node", "?")
                    remote_if = link_info.get("remote_interface", "?")
                    lines.append(f"  {local_if} → {remote}:{remote_if}")

        return "\n".join(lines)

    def build_available_templates_context(self) -> str:
        """List available configuration templates from M3."""
        try:
            from config_engine.renderer import TemplateRenderer
            renderer = TemplateRenderer()
            templates = renderer.list_templates()

            if not templates:
                return "No configuration templates available."

            lines = ["## Available Configuration Templates"]
            by_vendor: Dict[str, List[str]] = {}
            for t in templates:
                vendor = t.get("vendor", "unknown")
                feature = t.get("feature", "unknown")
                has_undo = t.get("has_undo", False)
                undo_str = " (has rollback)" if has_undo else ""
                by_vendor.setdefault(vendor, []).append(f"{feature}{undo_str}")

            for vendor, features in sorted(by_vendor.items()):
                lines.append(f"\n### {vendor}")
                for f in sorted(features):
                    lines.append(f"  - {f}")

            return "\n".join(lines)
        except Exception as e:
            logger.warning("Failed to list templates: %s", e)
            return "(Config templates not available)"

    def build_full_context(
        self,
        device_hostnames: Optional[List[str]] = None,
        query: Optional[str] = None,
        routing_decision: Optional[QueryRoutingDecision] = None,
        include_contract: bool = True,
        include_bundle: bool = True,
    ) -> str:
        """
        Build the complete context string for the system prompt.

        When *include_bundle* is True (the default) the ``ContextBundleBuilder``
        is invoked to fuse NetBox inventory, telemetry samples, and SDN
        controller state into the context, giving the LLM much richer
        grounding than topology alone (SRD UC06-UC09).

        Domain filtering (R1): only inject context sections that
        ``routing_decision.required_domains`` actually requests.  For
        ``inventory_only`` queries this trims ~10K tokens by skipping
        topology, telemetry, SDN, threats, drift, and knowledge.
        """
        decision = routing_decision or (classify_query(query) if query else None)
        required = frozenset(decision.required_domains) if decision else None

        parts = []

        if include_contract:
            parts.append(build_authoritative_context_contract())
            parts.append(self.build_source_matrix_context())

        if decision is None or decision.use_live_context:
            if required is None or CONTEXT_DOMAIN_LIVE_TOPOLOGY in required:
                parts.append(self.build_topology_context())

        if required is None or CONTEXT_DOMAIN_CONFIG in required:
            parts.append(self.build_available_templates_context())

        if include_bundle:
            bundle_text = self.build_network_bundle_context(
                device_hostnames,
                query=query,
                routing_decision=decision,
            )
            if bundle_text:
                parts.append(bundle_text)

        return "\n\n".join(p for p in parts if p)

    def build_network_bundle_context(
        self,
        device_hostnames: Optional[List[str]] = None,
        query: Optional[str] = None,
        routing_decision: Optional[QueryRoutingDecision] = None,
    ) -> str:
        """
        Build the fused network-context bundle with source-of-truth planned state.

        Returns:
            A markdown-formatted block ready for LLM injection, or empty
            string if the bundle builder is unavailable.
        """
        if routing_decision is not None and not any(
            (
                routing_decision.include_inventory,
                routing_decision.include_telemetry,
                routing_decision.include_sdn,
            )
        ):
            return ""

        try:
            from rag.context_bundle import ContextBundleBuilder
            from netbox.client import NetBoxClient

            builder = ContextBundleBuilder(
                db_manager=self._db,
                netbox_client=NetBoxClient.get_client(),
            )
            bundle = builder.build_bundle(
                device_hostnames=device_hostnames,
                query=query,
                routing_decision=routing_decision,
                include_inventory=(
                    routing_decision.include_inventory
                    if routing_decision is not None else True
                ),
                include_planned_topology=(
                    routing_decision.include_inventory
                    if routing_decision is not None else True
                ),
                include_telemetry=(
                    routing_decision.include_telemetry
                    if routing_decision is not None else True
                ),
                include_sdn=(
                    routing_decision.include_sdn
                    if routing_decision is not None else True
                ),
            )
            return bundle.to_prompt_text()
        except Exception as exc:
            logger.debug("Context bundle build skipped: %s", exc)
            return ""

    def build_source_matrix_context(self) -> str:
        lines = ["## Source Matrix"]
        for entry in build_source_matrix():
            primary = ", ".join(entry["primary_domains"])
            fallback = ", ".join(entry["fallback_domains"])
            lines.append(
                f"- {entry['query_class']}: primary={primary}; fallback={fallback}"
            )
        lines.append("")
        lines.append(build_canonical_semantics_prompt_text())
        return "\n".join(lines)

    def build_knowledge_context(self, query: str, retriever, k: int = 5) -> str:
        """
        Retrieve semantically relevant knowledge chunks for *query*.

        Delegates to retriever.retrieve_as_text() and returns a formatted
        markdown block suitable for per-turn injection into the system prompt.
        Returns an empty string when the knowledge base is empty or unavailable.

        Args:
            query:     The user's message (used as the embedding query).
            retriever: A ``KnowledgeRetriever`` instance (or None → no-op).
            k:         Maximum number of chunks to return.
        """
        if retriever is None:
            return ""
        try:
            return retriever.retrieve_as_text(query, k=k)
        except Exception as exc:
            logger.warning("build_knowledge_context failed: %s", exc)
            return ""
