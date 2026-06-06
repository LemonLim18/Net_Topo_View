#!/usr/bin/env python3
"""
Validate that intent_catalog YAML loads (run from repo with PYTHONPATH=backend).

  cd backend && python -m ansible.validate_intent_catalog
"""

from __future__ import annotations


def main() -> int:
    from ansible.intent_pipeline.operation_adapter import list_canonical_operations
    from ansible.intent_pipeline.schema_loader import load_intent_catalog

    catalog = load_intent_catalog()
    assert catalog.global_fields, "global_fields.yaml should define global_fields"
    assert catalog.priority_order, "keyword_map should define priority_order"
    assert len(catalog.categories) >= 6, "expected 6+ category YAML files"
    canon = list_canonical_operations()
    assert len(canon) >= 10, "operation_adapter should define canonical operations"
    # Smoke: formatter
    text = catalog.format_operations_block(["vlan", "interface"])
    assert "create_vlan" in text and "configure_interface" in text
    print("intent_catalog OK:", len(catalog.categories), "categories,", len(canon), "canonical ops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
