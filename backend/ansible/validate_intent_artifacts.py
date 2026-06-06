#!/usr/bin/env python3
"""
Validate intent_graph.yml operations against intent_schemas/*.yml.

Run from repo root (with backend on PYTHONPATH), e.g.:
  python -m ansible.validate_intent_artifacts
Or:
  cd backend && python -m ansible.validate_intent_artifacts
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from ansible.intent_artifacts import invalidate_caches, validate_intent_graph_against_schemas
    except ImportError:
        print("Import failed: run from project root with PYTHONPATH including the backend directory.", file=sys.stderr)
        return 2
    invalidate_caches()
    errors = validate_intent_graph_against_schemas()
    if errors:
        print("intent_graph / intent_schemas validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("intent_graph.yml: all operations have matching intent_schemas/*.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
