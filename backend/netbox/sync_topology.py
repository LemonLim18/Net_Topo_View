import logging
from typing import Any, Dict, List
from db.manager import DatabaseManager
from netbox.client import NetBoxClient

logger = logging.getLogger("netbox.sync_topology")


class TopologySyncer:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.nb_client = NetBoxClient.get_client()

    def sync_connections(self) -> Dict[str, Any]:
        """
        Fetch cables from NetBox and update the local links table.

        Each NetBox cable has two termination endpoints (A/B).  We resolve
        every termination to (device_hostname, interface_name), look up
        their local IDs, and upsert a link record.
        """
        if not self.nb_client.api:
            return {"status": "skipped", "reason": "no_connection"}

        cables = self.nb_client.get_cables()
        synced = 0
        errors: List[str] = []

        for cable in cables:
            try:
                a_terms = getattr(cable, "a_terminations", None) or []
                b_terms = getattr(cable, "b_terminations", None) or []

                if not a_terms or not b_terms:
                    continue

                # Each termination is usually a dcim.Interface object
                ep_a = a_terms[0]
                ep_b = b_terms[0]

                # Resolve endpoint A
                dev_a, iface_a = self._resolve_endpoint(ep_a)
                dev_b, iface_b = self._resolve_endpoint(ep_b)

                if not dev_a or not dev_b:
                    continue

                # Look up local device/interface IDs
                with self.db._connect() as conn:
                    da = conn.execute("SELECT id FROM devices WHERE hostname = ?", (dev_a,)).fetchone()
                    db_row = conn.execute("SELECT id FROM devices WHERE hostname = ?", (dev_b,)).fetchone()
                    if not da or not db_row:
                        continue

                    ia = None
                    ib = None
                    if iface_a:
                        ia = conn.execute(
                            "SELECT id FROM interfaces WHERE device_id = ? AND name = ?",
                            (da["id"], iface_a),
                        ).fetchone()
                    if iface_b:
                        ib = conn.execute(
                            "SELECT id FROM interfaces WHERE device_id = ? AND name = ?",
                            (db_row["id"], iface_b),
                        ).fetchone()

                    # Upsert link
                    conn.execute(
                        "INSERT OR REPLACE INTO links "
                        "(src_device_id, src_interface_id, dst_device_id, dst_interface_id, discovery_protocol, updated_at) "
                        "VALUES (?, ?, ?, ?, 'netbox', datetime('now'))",
                        (
                            da["id"],
                            ia["id"] if ia else None,
                            db_row["id"],
                            ib["id"] if ib else None,
                        ),
                    )
                    synced += 1

            except Exception as e:
                cable_id = getattr(cable, "id", "?")
                errors.append(f"cable {cable_id}: {e}")
                logger.error("Failed to sync cable %s: %s", cable_id, e)

        logger.info("NetBox Topology Sync: synced %d cables, %d errors", synced, len(errors))
        return {"status": "success", "cables_synced": synced, "errors": errors}

    def get_planned_topology(self) -> Dict[str, Any]:
        """
        Build a planned (design-of-record) topology dict from NetBox.

        Returns a graph structure compatible with the frontend's topology
        renderer (same shape as the BFS crawler output).
        """
        if not self.nb_client.api:
            return {"nodes": {}, "source": "netbox", "status": "no_connection"}

        nb = self.nb_client.api
        nodes: Dict[str, Dict[str, Any]] = {}

        try:
            devices = list(nb.dcim.devices.all())
        except Exception as e:
            logger.error("Failed to fetch NetBox devices: %s", e)
            return {"nodes": {}, "source": "netbox", "error": str(e)}

        # Build node map
        for dev in devices:
            hostname = str(dev.name)
            primary_ip = str(dev.primary_ip).split("/")[0] if dev.primary_ip else None

            # Fetch interfaces for this device
            iface_dict: Dict[str, str] = {}
            try:
                ifaces = list(nb.dcim.interfaces.filter(device=hostname))
                for iface in ifaces:
                    # Get IPs assigned to this interface
                    try:
                        ips = list(nb.ipam.ip_addresses.filter(interface_id=iface.id))
                        ip_str = str(ips[0]).split("/")[0] if ips else ""
                    except Exception:
                        ip_str = ""
                    iface_dict[str(iface.name)] = ip_str
            except Exception:
                pass

            nodes[hostname] = {
                "mgmt_ip": primary_ip,
                "interfaceList": [iface_dict] if iface_dict else [{}],
                "connectedNode": [],
                "edgeLink": {},
                "down": str(dev.status) != "active" if dev.status else False,
                "netbox_role": str(dev.role) if dev.role else None,
                "netbox_site": str(dev.site) if dev.site else None,
            }

        # Build edges from cables
        try:
            cables = list(nb.dcim.cables.all())
        except Exception:
            cables = []

        for cable in cables:
            try:
                a_terms = getattr(cable, "a_terminations", None) or []
                b_terms = getattr(cable, "b_terminations", None) or []
                if not a_terms or not b_terms:
                    continue

                dev_a, iface_a = self._resolve_endpoint(a_terms[0])
                dev_b, iface_b = self._resolve_endpoint(b_terms[0])

                if not dev_a or not dev_b:
                    continue

                if dev_a in nodes and dev_b in nodes:
                    # Add connected nodes
                    if dev_b not in nodes[dev_a].get("connectedNode", []):
                        nodes[dev_a].setdefault("connectedNode", []).append(dev_b)
                    if dev_a not in nodes[dev_b].get("connectedNode", []):
                        nodes[dev_b].setdefault("connectedNode", []).append(dev_a)

                    # Add edge links
                    if iface_a:
                        nodes[dev_a].setdefault("edgeLink", {})[iface_a] = {
                            "remote_node": dev_b,
                            "remote_interface": iface_b or "?",
                            "remote_ip": nodes[dev_b].get("mgmt_ip", ""),
                        }
                    if iface_b:
                        nodes[dev_b].setdefault("edgeLink", {})[iface_b] = {
                            "remote_node": dev_a,
                            "remote_interface": iface_a or "?",
                            "remote_ip": nodes[dev_a].get("mgmt_ip", ""),
                        }
            except Exception as e:
                logger.debug("Skipping cable in planned topology: %s", e)

        return {
            "nodes": nodes,
            "source": "netbox",
            "device_count": len(nodes),
            "timestamp": None,  # Design-of-record — no crawl timestamp
        }

    @staticmethod
    def _resolve_endpoint(ep) -> tuple:
        """
        Resolve a cable termination to (device_hostname, interface_name).

        pynetbox terminations can be dcim.Interface objects or nested dicts.
        """
        try:
            # pynetbox object
            if hasattr(ep, "device") and hasattr(ep, "name"):
                return (str(ep.device), str(ep.name))
            # Nested dict (some pynetbox versions)
            if isinstance(ep, dict):
                obj = ep.get("object", ep)
                device = obj.get("device", {})
                dev_name = device.get("name") if isinstance(device, dict) else str(device)
                iface_name = obj.get("name", "")
                return (dev_name, iface_name)
        except Exception:
            pass
        return (None, None)

