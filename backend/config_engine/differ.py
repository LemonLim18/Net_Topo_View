"""
Config Differ — Compare two configurations and generate human-readable diffs.

Supports both unified diff (for terminal/API) and side-by-side diff
(for frontend display).

Usage:
    differ = ConfigDiffer()
    diff = differ.unified_diff(old_config, new_config)
    pairs = differ.side_by_side(old_config, new_config)
    summary = differ.summary(old_config, new_config)
"""

import difflib
import logging
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger("config_engine.differ")


@dataclass
class DiffResult:
    """Structured diff result with metadata."""
    unified: str               # Unified diff string
    additions: int             # Number of lines added
    deletions: int             # Number of lines removed
    changes: int               # Number of lines changed
    is_identical: bool         # True if configs are the same
    added_lines: List[str]     # Actual added lines
    removed_lines: List[str]   # Actual removed lines

    def to_dict(self) -> dict:
        return {
            "unified": self.unified,
            "additions": self.additions,
            "deletions": self.deletions,
            "changes": self.changes,
            "is_identical": self.is_identical,
            "added_lines": self.added_lines,
            "removed_lines": self.removed_lines,
        }


class ConfigDiffer:
    """
    Generates diffs between network configuration texts.

    Designed for comparing:
    - Running config vs proposed config (pre-deploy preview)
    - Running config vs golden template (drift detection)
    - Two snapshots of the same device (change tracking)
    """

    def unified_diff(
        self,
        old: str,
        new: str,
        from_label: str = "current",
        to_label: str = "proposed",
        context_lines: int = 3,
    ) -> str:
        """
        Return a unified diff string.

        Args:
            old: The current/old configuration text.
            new: The proposed/new configuration text.
            from_label: Label for the old config (shown in header).
            to_label: Label for the new config (shown in header).
            context_lines: Number of context lines around changes.

        Returns:
            Unified diff string (similar to 'diff -u' output).
        """
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_label,
            tofile=to_label,
            n=context_lines,
        )

        return "".join(diff)

    def side_by_side(
        self,
        old: str,
        new: str,
        width: int = 80,
    ) -> List[Tuple[Optional[str], Optional[str], str]]:
        """
        Return side-by-side comparison with change markers.

        Returns:
            List of tuples: (old_line, new_line, tag)
            where tag is one of: 'equal', 'replace', 'insert', 'delete'
        """
        old_lines = old.splitlines()
        new_lines = new.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        result = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for i in range(i1, i2):
                    result.append((old_lines[i], new_lines[j1 + (i - i1)], "equal"))
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    old_line = old_lines[i1 + k] if (i1 + k) < i2 else None
                    new_line = new_lines[j1 + k] if (j1 + k) < j2 else None
                    result.append((old_line, new_line, "replace"))
            elif tag == "insert":
                for j in range(j1, j2):
                    result.append((None, new_lines[j], "insert"))
            elif tag == "delete":
                for i in range(i1, i2):
                    result.append((old_lines[i], None, "delete"))

        return result

    def full_diff(
        self,
        old: str,
        new: str,
        from_label: str = "current",
        to_label: str = "proposed",
    ) -> DiffResult:
        """
        Generate a comprehensive DiffResult with all metrics.

        Returns:
            DiffResult with unified diff, counts, and line lists.
        """
        unified = self.unified_diff(old, new, from_label, to_label)

        added = []
        removed = []

        for line in unified.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:].strip())
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:].strip())

        is_identical = old.strip() == new.strip()

        return DiffResult(
            unified=unified,
            additions=len(added),
            deletions=len(removed),
            changes=len(added) + len(removed),
            is_identical=is_identical,
            added_lines=added,
            removed_lines=removed,
        )

    def summary(self, old: str, new: str) -> str:
        """
        One-line summary of changes: "3 additions, 2 deletions, 1 modification"
        """
        result = self.full_diff(old, new)

        if result.is_identical:
            return "No changes detected."

        parts = []
        if result.additions:
            parts.append(f"{result.additions} addition{'s' if result.additions != 1 else ''}")
        if result.deletions:
            parts.append(f"{result.deletions} deletion{'s' if result.deletions != 1 else ''}")

        return ", ".join(parts) if parts else "No changes detected."

    def html_diff(
        self,
        old: str,
        new: str,
        from_label: str = "current",
        to_label: str = "proposed",
    ) -> str:
        """
        Generate an HTML side-by-side diff table.
        Useful for rendering in the frontend dashboard.
        """
        old_lines = old.splitlines()
        new_lines = new.splitlines()

        html_diff = difflib.HtmlDiff(wrapcolumn=80)
        return html_diff.make_table(
            old_lines,
            new_lines,
            fromdesc=from_label,
            todesc=to_label,
        )
