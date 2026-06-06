"""
M13d: CVE Correlation Matcher
================================
Maps device (vendor, os_version) pairs from the M14 database to known
CVEs stored in cve_data.json.

Uses version prefix matching: if a device runs "17.6.3a" and a CVE
affects version "17.6", the CVE matches.

Usage::

    matcher = CVEMatcher()
    cves = matcher.match_device("cisco_ios", "17.6.3a")
    for cve in cves:
        print(f"  {cve['id']} (CVSS {cve['cvss']}): {cve['description']}")
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("security.cve")

# Load CVE data once at import time
_CVE_DATA_PATH = os.path.join(os.path.dirname(__file__), "cve_data.json")
_CVE_DATABASE: List[Dict[str, Any]] = []

try:
    with open(_CVE_DATA_PATH, "r") as f:
        _CVE_DATABASE = json.load(f).get("cves", [])
    logger.info("Loaded %d CVEs from %s", len(_CVE_DATABASE), _CVE_DATA_PATH)
except FileNotFoundError:
    logger.warning("CVE data file not found: %s", _CVE_DATA_PATH)
except json.JSONDecodeError as e:
    logger.error("Failed to parse CVE data: %s", e)


class CVEMatcher:
    """
    Matches device vendor + OS version against known CVE database.

    Version matching uses prefix comparison:
    - Device version "15.9(3)M7" matches CVE version "15.9"
    - Device version "V200R005C00" matches CVE version "V200R005"
    """

    def __init__(self):
        self._cves = _CVE_DATABASE

    def match_device(self, vendor: str, os_version: str) -> List[Dict[str, Any]]:
        """
        Find CVEs that affect a device with the given vendor and OS version.

        Args:
            vendor:     Device vendor (cisco_ios, huawei_vrp, juniper_junos).
            os_version: Full OS version string from the device.

        Returns:
            List of matching CVE dicts sorted by CVSS score (highest first).
        """
        if not os_version:
            return []

        matches = []
        version_lower = os_version.lower()

        for cve in self._cves:
            if vendor not in cve.get("affected_vendors", []):
                continue

            for affected_ver in cve.get("affected_versions", []):
                if self._version_matches(version_lower, affected_ver.lower()):
                    matches.append(cve)
                    break

        # Sort by CVSS score descending
        matches.sort(key=lambda c: c.get("cvss", 0), reverse=True)
        return matches

    def scan_all_devices(self, db) -> Dict[int, List[Dict[str, Any]]]:
        """
        Scan all devices in the database for matching CVEs.

        Args:
            db: DatabaseManager instance.

        Returns:
            Dict mapping device_id → list of matched CVEs.
        """
        results = {}
        devices = db.get_all_devices()

        for device in devices:
            vendor = device.vendor or ""
            os_version = device.os_version or ""
            if not vendor or not os_version:
                continue

            cves = self.match_device(vendor, os_version)
            if cves:
                results[device.id] = cves
                logger.info("Device %s (%s %s): %d CVEs matched",
                            device.hostname, vendor, os_version, len(cves))

        return results

    def get_all_cves(self) -> List[Dict[str, Any]]:
        """Return all CVEs in the database."""
        return self._cves

    def get_cve_by_id(self, cve_id: str) -> Optional[Dict[str, Any]]:
        """Look up a specific CVE by its ID."""
        for cve in self._cves:
            if cve["id"] == cve_id:
                return cve
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics of the CVE database."""
        total = len(self._cves)
        by_severity = {}
        by_vendor = {}
        for cve in self._cves:
            sev = cve.get("severity", "unknown")
            by_severity[sev] = by_severity.get(sev, 0) + 1
            for v in cve.get("affected_vendors", []):
                by_vendor[v] = by_vendor.get(v, 0) + 1
        return {
            "total_cves": total,
            "by_severity": by_severity,
            "by_vendor": by_vendor,
        }

    @staticmethod
    def _version_matches(device_version: str, cve_version: str) -> bool:
        """
        Check if a device version matches a CVE affected version.

        Uses prefix matching: "15.9(3)m7" matches "15.9",
        "v200r005c00" matches "v200r005".
        """
        return device_version.startswith(cve_version)
