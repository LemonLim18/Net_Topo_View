from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Pattern


_DEFAULT_IGNORE_PATTERNS = [
    r"^!?\s*last configuration change\b.*$",
    r"^!?\s*last configuration was updated\b.*$",
    r"^!?\s*last configuration was saved\b.*$",
    r"^!?\s*ntp clock-period\b.*$",
    r"^!?\s*current configuration\b.*$",
    r"^!?\s*building configuration\b.*$",
    r"^!?\s*time-source\b.*$",
    r"^!?\s*uptime is\b.*$",
    r"^!?\s*configuration register\b.*$",
    r"^!?\s*generated on\b.*$",
]


def _compile_patterns(patterns: Iterable[str]) -> List[Pattern[str]]:
    out: List[Pattern[str]] = []
    for p in patterns:
        try:
            out.append(re.compile(str(p), flags=re.IGNORECASE))
        except Exception:
            continue
    return out


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


@dataclass
class NormalizedConfig:
    text: str
    lines: List[str]
    removed_count: int


class ConfigNormalizer:
    """Vendor-aware config normalizer.

    Per docs: mandatory step before diffing.
    """

    def normalize(
        self,
        raw_text: str,
        vendor: Optional[str] = None,
        ignore_patterns: Optional[List[str]] = None,
    ) -> NormalizedConfig:
        vendor_key = (vendor or "").strip().lower()
        ignore = list(_DEFAULT_IGNORE_PATTERNS)
        if ignore_patterns:
            ignore.extend([str(x) for x in ignore_patterns if str(x).strip()])
        ignore_re = _compile_patterns(ignore)

        removed = 0
        out_lines: List[str] = []
        in_banner = False
        banner_end_token: Optional[str] = None

        for raw in (raw_text or "").splitlines():
            line0 = str(raw or "").rstrip("\r\n")

            # Banner stripping (best-effort)
            if in_banner:
                removed += 1
                if banner_end_token and banner_end_token in line0:
                    in_banner = False
                    banner_end_token = None
                continue

            # Start of banner blocks for IOS-like
            m_banner = re.match(r"^\s*banner\s+\S+\s+(\S)\s*$", line0, flags=re.IGNORECASE)
            if m_banner:
                in_banner = True
                banner_end_token = m_banner.group(1)
                removed += 1
                continue

            # Junos multi-line banners (set system login message)
            if vendor_key.startswith("juniper") and re.match(r"^\s*set\s+system\s+login\s+message\b", line0, re.I):
                removed += 1
                continue

            line = _collapse_ws(line0)
            if not line:
                removed += 1
                continue

            # Drop comments/noise markers
            if line.startswith("!") or line.startswith("#"):
                removed += 1
                continue

            # Drop volatile patterns
            if any(rx.search(line) for rx in ignore_re):
                removed += 1
                continue

            # Canonicalize spacing around common separators
            line = re.sub(r"\s*,\s*", ",", line)
            line = re.sub(r"\s*=\s*", "=", line)

            # Vendor-specific trivial canonicalization
            if vendor_key.startswith("huawei") or vendor_key.startswith("vrp"):
                # Normalize "undo" to "no" semantics for diff readability (not for playbooks)
                line = re.sub(r"^\s*undo\s+", "no ", line, flags=re.IGNORECASE)

            out_lines.append(line)

        # Stable order for diffing: preserve order but collapse consecutive duplicates
        deduped: List[str] = []
        prev = None
        for l in out_lines:
            if prev == l:
                removed += 1
                continue
            deduped.append(l)
            prev = l

        text = "\n".join(deduped).strip()
        return NormalizedConfig(text=text, lines=deduped, removed_count=removed)

