"""
Template Renderer — Jinja2-based config generation for multi-vendor networks.

Loads .j2 templates from backend/templates/{vendor}/ and renders them
with user-supplied parameters. Also validates params against JSON schemas.

Usage:
    renderer = TemplateRenderer()
    config = renderer.render("cisco_ios", "ospf", {
        "process_id": 1,
        "router_id": "1.1.1.1",
        "networks": [{"network": "10.0.0.0", "wildcard": "0.0.0.255", "area": 0}]
    })
    rollback = renderer.render_undo("cisco_ios", "ospf", same_params)
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateError

logger = logging.getLogger("config_engine.renderer")

# Resolve template and schema directories relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # backend/
_TEMPLATES_DIR = _PROJECT_ROOT / "templates"
_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

# Supported vendors (must match folder names under templates/)
SUPPORTED_VENDORS = ["cisco_ios", "cisco_xe", "huawei_vrp", "juniper_junos"]


class TemplateRenderer:
    """
    Renders Jinja2 templates for multi-vendor network configuration.

    Template files live under backend/templates/{vendor}/{feature}.j2
    Undo templates live under backend/templates/{vendor}/undo_{feature}.j2
    """

    def __init__(self, templates_dir: Optional[str] = None, schemas_dir: Optional[str] = None):
        self.templates_dir = Path(templates_dir) if templates_dir else _TEMPLATES_DIR
        self.schemas_dir = Path(schemas_dir) if schemas_dir else _SCHEMAS_DIR

        # Create a Jinja2 environment with the templates root
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        # Cache loaded schemas
        self._schema_cache: Dict[str, dict] = {}

        logger.info(f"TemplateRenderer initialized — templates: {self.templates_dir}")

    # ------------------------------------------------------------------
    # Core rendering
    # ------------------------------------------------------------------

    def render(self, vendor: str, feature: str, params: dict) -> str:
        """
        Render a config template for a given vendor and feature.

        Args:
            vendor:  One of 'cisco_ios', 'cisco_xe', 'huawei_vrp', 'juniper_junos'
            feature: Template name, e.g. 'ospf', 'bgp', 'interface'
            params:  Dictionary of parameters to pass to the template

        Returns:
            Rendered CLI configuration as a string.

        Raises:
            FileNotFoundError: If the template file doesn't exist.
            ValueError: If required parameters are missing.
            TemplateError: If the template has syntax errors.
        """
        self._validate_vendor(vendor)

        # Validate params against schema (if schema exists)
        validation_errors = self._validate_params(feature, params)
        if validation_errors:
            raise ValueError(
                f"Parameter validation failed for '{feature}':\n"
                + "\n".join(f"  - {e}" for e in validation_errors)
            )

        # Load and render the template
        template_path = f"{vendor}/{feature}.j2"
        try:
            template = self.env.get_template(template_path)
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Template not found: {template_path}\n"
                f"Looked in: {self.templates_dir}"
            )

        try:
            rendered = template.render(**params)
        except TemplateError as e:
            raise TemplateError(f"Error rendering {template_path}: {e}")

        logger.info(f"Rendered {vendor}/{feature} ({len(rendered)} chars)")
        return rendered.strip()

    def render_undo(self, vendor: str, feature: str, params: dict) -> str:
        """
        Render the rollback/undo template for a given vendor and feature.

        Undo templates follow the naming convention: undo_{feature}.j2
        They generate the commands needed to reverse the config change.

        Returns:
            Rendered rollback CLI commands as a string.
        """
        self._validate_vendor(vendor)

        undo_path = f"{vendor}/undo_{feature}.j2"
        try:
            template = self.env.get_template(undo_path)
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Undo template not found: {undo_path}\n"
                f"Looked in: {self.templates_dir}"
            )

        try:
            rendered = template.render(**params)
        except TemplateError as e:
            raise TemplateError(f"Error rendering {undo_path}: {e}")

        logger.info(f"Rendered undo for {vendor}/{feature}")
        return rendered.strip()

    # ------------------------------------------------------------------
    # Template discovery
    # ------------------------------------------------------------------

    def list_templates(self, vendor: Optional[str] = None) -> List[Dict[str, str]]:
        """
        List all available templates, optionally filtered by vendor.

        Returns:
            List of dicts with keys: vendor, feature, has_undo, schema_exists
        """
        templates = []

        vendors = [vendor] if vendor else SUPPORTED_VENDORS
        for v in vendors:
            vendor_dir = self.templates_dir / v
            if not vendor_dir.is_dir():
                continue

            for f in sorted(vendor_dir.glob("*.j2")):
                name = f.stem  # e.g., 'ospf' from 'ospf.j2'

                # Skip undo templates in the main listing
                if name.startswith("undo_"):
                    continue

                undo_exists = (vendor_dir / f"undo_{name}.j2").exists()
                schema_exists = (self.schemas_dir / f"{name}.json").exists()

                templates.append({
                    "vendor": v,
                    "feature": name,
                    "has_undo": undo_exists,
                    "schema_exists": schema_exists,
                })

        return templates

    def get_template_content(self, vendor: str, feature: str) -> str:
        """Return the raw Jinja2 source of a template (for preview/editing)."""
        self._validate_vendor(vendor)
        template_file = self.templates_dir / vendor / f"{feature}.j2"

        if not template_file.exists():
            raise FileNotFoundError(f"Template not found: {template_file}")

        return template_file.read_text(encoding="utf-8")

    def get_schema(self, feature: str) -> Optional[dict]:
        """Load the JSON schema for a feature template."""
        if feature in self._schema_cache:
            return self._schema_cache[feature]

        schema_file = self.schemas_dir / f"{feature}.json"
        if not schema_file.exists():
            return None

        with open(schema_file, "r", encoding="utf-8") as f:
            schema = json.load(f)

        self._schema_cache[feature] = schema
        return schema

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_vendor(self, vendor: str) -> None:
        """Raise ValueError if vendor is not supported."""
        if vendor not in SUPPORTED_VENDORS:
            raise ValueError(
                f"Unsupported vendor: '{vendor}'. "
                f"Supported: {', '.join(SUPPORTED_VENDORS)}"
            )

    def _validate_params(self, feature: str, params: dict) -> List[str]:
        """
        Validate parameters against the JSON schema for a feature.
        Performs basic type coercion (e.g. string "1500" to int 1500) where possible.
        Returns a list of error messages (empty = valid).
        """
        schema = self.get_schema(feature)
        if not schema:
            return []  # No schema = no validation

        errors = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field in required:
            if field not in params:
                desc = properties.get(field, {}).get("description", field)
                errors.append(f"Missing required parameter: '{field}' ({desc})")

        # Type checking and coercion for provided params
        for field, value in params.items():
            if field in properties:
                expected_type = properties[field].get("type")
                if not expected_type:
                    continue

                coerced_value, err_msg = self._coerce_value(value, expected_type)
                if err_msg:
                    errors.append(f"Parameter '{field}' {err_msg}")
                else:
                    params[field] = coerced_value

        return errors

    @staticmethod
    def _coerce_value(value: Any, expected: str) -> (Any, Optional[str]):
        """
        Attempt to coerce a value to the expected JSON schema type.
        Returns (coerced_value, None) on success, or (value, error_message) on failure.
        """
        if expected == "string":
            return str(value), None
        
        if expected == "integer":
            if isinstance(value, int):
                return value, None
            try:
                # Handle numeric strings
                if isinstance(value, str):
                    if value.strip() == "": return 0, None # Allow empty as 0 for numbers
                    return int(value), None
                return int(value), None
            except (ValueError, TypeError):
                return value, f"should be type 'integer', got '{type(value).__name__}'"

        if expected == "number":
            if isinstance(value, (int, float)):
                return value, None
            try:
                if isinstance(value, str):
                    if value.strip() == "": return 0.0, None
                    return float(value), None
                return float(value), None
            except (ValueError, TypeError):
                return value, f"should be type 'number', got '{type(value).__name__}'"

        if expected == "boolean":
            if isinstance(value, bool):
                return value, None
            if isinstance(value, str):
                val_lower = value.lower().strip()
                if val_lower in ("true", "1", "yes", "on"):
                    return True, None
                if val_lower in ("false", "0", "no", "off", ""):
                    return False, None
            return bool(value), None

        if expected == "array":
            if isinstance(value, list):
                return value, None
            return value, f"should be type 'array', got '{type(value).__name__}'"

        if expected == "object":
            if isinstance(value, dict):
                return value, None
            return value, f"should be type 'object', got '{type(value).__name__}'"

        return value, None
