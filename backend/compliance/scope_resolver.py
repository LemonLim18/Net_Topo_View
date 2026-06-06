from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from db.manager import DatabaseManager
from netbox.client import NetBoxClient


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _now_s() -> float:
    return time.time()


@dataclass
class ScopeResolution:
    device: Dict[str, Any]
    partial: bool
    reason: str


class NetBoxScopeResolver:
    """Hybrid scope resolver.

    - Prefers NetBox device detail when available (authoritative SoT).
    - Caches per-device enriched scope attributes with TTL.
    - Falls back to local DB-only device fields when NetBox is unavailable.
    """

    def __init__(self, *, ttl_seconds: Optional[int] = None):
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else _env_int("COMPLIANCE_NETBOX_SCOPE_TTL_SECONDS", 300)
        # cache key: netbox_id or hostname
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.nb = NetBoxClient.get_client()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        ent = self._cache.get(key)
        if not ent:
            return None
        ts, val = ent
        if (_now_s() - ts) <= float(self.ttl_seconds):
            return dict(val)
        self._cache.pop(key, None)
        return None

    def _cache_put(self, key: str, val: Dict[str, Any]) -> None:
        self._cache[key] = (_now_s(), dict(val))

    def resolve_device(self, db: DatabaseManager, device_id: int) -> ScopeResolution:
        dev = db.get_device_by_id(device_id)
        if not dev:
            return ScopeResolution(device={"id": device_id}, partial=True, reason="device_not_found")

        base = {
            "id": dev.id,
            "hostname": dev.hostname,
            "vendor": dev.vendor,
            "role": dev.role,
            "netbox_id": getattr(dev, "netbox_id", None),
        }

        nb_id = getattr(dev, "netbox_id", None)
        cache_key = f"nb:{nb_id}" if nb_id else f"host:{dev.hostname}"
        cached = self._cache_get(cache_key)
        if cached:
            return ScopeResolution(device={**base, **cached}, partial=False, reason="cache_hit")

        # If NetBox is unavailable, fall back immediately.
        if not getattr(self.nb, "api", None):
            return ScopeResolution(device=base, partial=True, reason="netbox_unavailable")

        # Prefer netbox_id lookups for correctness.
        try:
            nb_dev = None
            if nb_id:
                nb_dev = self.nb.get_device_by_id(int(nb_id))
            if nb_dev is None:
                # fallback to name lookup
                by_name = self.nb.get_device_by_name(dev.hostname)
                nb_dev = by_name[0] if by_name else None
        except Exception:
            nb_dev = None

        if nb_dev is None:
            return ScopeResolution(device=base, partial=True, reason="netbox_device_not_found")

        enriched: Dict[str, Any] = {}

        # Role slug/name
        try:
            role = getattr(nb_dev, "role", None)
            enriched["netbox_role_slug"] = getattr(role, "slug", None) if role is not None else None
            enriched["netbox_role_name"] = getattr(role, "name", None) if role is not None else None
        except Exception:
            enriched["netbox_role_slug"] = None

        # Platform slug
        try:
            platform = getattr(nb_dev, "platform", None)
            enriched["netbox_platform_slug"] = getattr(platform, "slug", None) if platform is not None else None
        except Exception:
            enriched["netbox_platform_slug"] = None

        # Tenant/site
        try:
            tenant = getattr(nb_dev, "tenant", None)
            enriched["netbox_tenant"] = getattr(tenant, "slug", None) or getattr(tenant, "name", None) if tenant is not None else None
        except Exception:
            enriched["netbox_tenant"] = None
        try:
            site = getattr(nb_dev, "site", None)
            enriched["netbox_site"] = getattr(site, "slug", None) or getattr(site, "name", None) if site is not None else None
        except Exception:
            enriched["netbox_site"] = None

        # Tags (NetBox uses tags as common grouping primitive)
        tags: List[str] = []
        try:
            nb_tags = getattr(nb_dev, "tags", None)
            if nb_tags:
                for t in list(nb_tags):
                    slug = getattr(t, "slug", None)
                    name = getattr(t, "name", None)
                    if slug:
                        tags.append(str(slug))
                    elif name:
                        tags.append(str(name))
        except Exception:
            pass
        enriched["netbox_tags"] = sorted({str(t).strip() for t in tags if str(t).strip()})

        # Device groups: NetBox OSS doesn't have first-class device groups in dcim.devices;
        # we model groups as tags + tenant + site buckets for scope matching.
        groups: List[str] = []
        if enriched.get("netbox_site"):
            groups.append(f"site:{enriched['netbox_site']}")
        if enriched.get("netbox_tenant"):
            groups.append(f"tenant:{enriched['netbox_tenant']}")
        for t in enriched.get("netbox_tags") or []:
            groups.append(f"tag:{t}")
        enriched["device_groups"] = sorted({g.strip().lower() for g in groups if str(g).strip()})

        self._cache_put(cache_key, enriched)
        return ScopeResolution(device={**base, **enriched}, partial=False, reason="netbox_live")

    def scope_options(self) -> Dict[str, Any]:
        """Return cached scope option lists from NetBox (best-effort)."""
        if not getattr(self.nb, "api", None):
            return {"available": False, "reason": "netbox_unavailable", "roles": [], "tags": [], "sites": [], "tenants": []}
        out = {"available": True, "roles": [], "tags": [], "sites": [], "tenants": []}
        try:
            out["roles"] = sorted({str(r.slug) for r in list(self.nb.api.dcim.device_roles.all()) if getattr(r, "slug", None)})
        except Exception:
            out["roles"] = []
        try:
            # tags are under extras.tags
            out["tags"] = sorted({str(t.slug) for t in list(self.nb.api.extras.tags.all()) if getattr(t, "slug", None)})
        except Exception:
            out["tags"] = []
        try:
            out["sites"] = sorted({str(s.slug) for s in list(self.nb.api.dcim.sites.all()) if getattr(s, "slug", None)})
        except Exception:
            out["sites"] = []
        try:
            out["tenants"] = sorted({str(t.slug) for t in list(self.nb.api.tenancy.tenants.all()) if getattr(t, "slug", None)})
        except Exception:
            out["tenants"] = []
        return out

