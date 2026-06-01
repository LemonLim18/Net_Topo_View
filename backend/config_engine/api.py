"""
M3 Config Engine API — REST endpoints for template rendering, diffing, and validation.

Exposes the Config Engine (TemplateRenderer, ConfigDiffer, SyntaxValidator)
to the M11 frontend Configuration view.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from common_services.service import get_effective_profile
from config_engine.renderer import TemplateRenderer
from config_engine.differ import ConfigDiffer
from config_engine.validator import SyntaxValidator
from db.manager import DatabaseManager

logger = logging.getLogger("config_engine.api")

router = APIRouter(prefix="/config", tags=["Config Engine (M3)"])

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
_renderer = TemplateRenderer()
_differ = ConfigDiffer()
_validator = SyntaxValidator()
_DB_PATH = "db/netauto.db"
TEMP_DISABLE_DEFAULT_GATEWAY = True

# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class RenderRequest(BaseModel):
    vendor: str
    feature: str
    params: Dict[str, Any]

class RenderResponse(BaseModel):
    config: str
    undo_config: Optional[str] = None

class DiffRequest(BaseModel):
    old_config: str
    new_config: str
    from_label: str = "current"
    to_label: str = "proposed"

class DiffResponse(BaseModel):
    unified: str
    additions: int
    deletions: int
    changes: int
    is_identical: bool
    added_lines: List[str]
    removed_lines: List[str]
    summary: str

class ValidateRequest(BaseModel):
    vendor: str
    config_text: str

class ValidateResponse(BaseModel):
    valid: bool
    errors: List[str]

class TemplateInfo(BaseModel):
    vendor: str
    feature: str
    has_undo: bool
    schema_exists: bool


def _maybe_inject_common_services(params: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(params or {})

    device_id = None
    for key in ("device_id", "target_device_id", "netbox_device_id"):
        raw = merged.get(key)
        if raw is None or raw == "":
            continue
        try:
            device_id = int(raw)
            break
        except Exception:
            continue

    role = merged.get("role")
    site = merged.get("site")
    effective = get_effective_profile(_DB_PATH, site=site, role=role, device_id=device_id)

    if not TEMP_DISABLE_DEFAULT_GATEWAY and (merged.get("gateway") is None or str(merged.get("gateway")).strip() == "") and device_id is not None:
        device = DatabaseManager(_DB_PATH).get_device_by_id(device_id)
        if device and str(device.role or "").strip().lower() == "access" and device.oob_gateway_ip:
            merged["gateway"] = device.oob_gateway_ip

    for key in ("snmp_community", "snmp_location", "snmp_contact", "snmp_host_ip", "syslog_port", "syslog_device_port", "syslog_host_ip", "ntp_server_ip"):
        if (merged.get(key) is None or str(merged.get(key)).strip() == "") and effective.get(key):
            merged[key] = effective.get(key)
    # Backward-compatible template variable: keep syslog_port device-facing for rendering.
    if merged.get("syslog_device_port") not in (None, ""):
        merged["syslog_port"] = merged.get("syslog_device_port")

    return merged

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=List[TemplateInfo])
async def list_templates(vendor: Optional[str] = None):
    """List all available config templates, optionally filtered by vendor."""
    try:
        templates = _renderer.list_templates(vendor=vendor)
        return templates
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/schemas/{feature}")
async def get_schema(feature: str):
    """Get the JSON schema for a template feature (defines required params)."""
    try:
        schema = _renderer.get_schema(feature)
        if schema is None:
            raise HTTPException(404, f"No schema found for feature '{feature}'")
        return schema
    except FileNotFoundError:
        raise HTTPException(404, f"No schema found for feature '{feature}'")


@router.post("/render", response_model=RenderResponse)
async def render_template(req: RenderRequest):
    """
    Render a Jinja2 config template with the provided parameters.
    
    Returns the generated CLI commands and optionally the undo/rollback commands.
    """
    effective_params = _maybe_inject_common_services(req.params)

    try:
        config = _renderer.render(req.vendor, req.feature, effective_params)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Render failed: {e}")
        raise HTTPException(500, f"Template rendering failed: {e}")

    # Try rendering the undo template (optional — not all features have one)
    undo_config = None
    try:
        undo_config = _renderer.render_undo(req.vendor, req.feature, effective_params)
    except (FileNotFoundError, Exception):
        pass  # Undo template is optional

    return RenderResponse(config=config, undo_config=undo_config)


@router.post("/diff", response_model=DiffResponse)
async def diff_configs(req: DiffRequest):
    """
    Compare two configuration texts and return a structured diff.
    
    Use this to show the user what will change before deploying.
    """
    result = _differ.full_diff(
        req.old_config,
        req.new_config,
        from_label=req.from_label,
        to_label=req.to_label,
    )
    summary = _differ.summary(req.old_config, req.new_config)

    return DiffResponse(
        unified=result.unified,
        additions=result.additions,
        deletions=result.deletions,
        changes=result.changes,
        is_identical=result.is_identical,
        added_lines=result.added_lines,
        removed_lines=result.removed_lines,
        summary=summary,
    )


@router.post("/validate", response_model=ValidateResponse)
async def validate_config(req: ValidateRequest):
    """
    Validate generated configuration text for syntax correctness.
    
    Uses vendor-specific rules to check for common errors.
    """
    try:
        errors = _validator.validate(req.vendor, req.config_text)
        return ValidateResponse(valid=len(errors) == 0, errors=errors)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        raise HTTPException(500, f"Validation error: {e}")


# ---------------------------------------------------------------------------
# Template & Schema CRUD (for Ansible Studio file tree)
# ---------------------------------------------------------------------------

try:
    from auth.auth import get_current_user as _get_user
    from auth.rbac import require_role as _require_role
except ImportError:
    async def _get_user():   # type: ignore
        return {"username": "anonymous", "role": "admin"}
    def _require_role(role: str):  # type: ignore
        return _get_user


@router.get("/templates/{vendor}/{feature}/source")
async def get_template_source(vendor: str, feature: str):
    """Return the raw Jinja2 source of a template (for editing in Monaco)."""
    try:
        content = _renderer.get_template_content(vendor, feature)
        return {"vendor": vendor, "feature": feature, "content": content}
    except FileNotFoundError:
        raise HTTPException(404, f"Template '{vendor}/{feature}.j2' not found")


class SaveTemplateRequest(BaseModel):
    content: str


@router.put("/templates/{vendor}/{feature}/source")
async def save_template_source(
    vendor: str,
    feature: str,
    body: SaveTemplateRequest,
    _user: dict = Depends(_require_role("admin")),
):
    """Write back edited Jinja2 source (admin only)."""
    from pathlib import Path
    tpl_file = Path(_renderer.templates_dir) / vendor / f"{feature}.j2"
    if not tpl_file.parent.exists():
        raise HTTPException(404, f"Vendor directory '{vendor}' not found")
    tpl_file.write_text(body.content, encoding="utf-8")
    # Invalidate cache if renderer caches compiled templates
    _renderer._env = None  # force reload on next render
    logger.info("Template %s/%s updated by %s", vendor, feature, _user.get("username"))
    return {"status": "saved", "vendor": vendor, "feature": feature}


@router.get("/schemas/{feature}/source")
async def get_schema_source(feature: str):
    """Return the raw JSON schema source for in-browser editing."""
    from pathlib import Path
    schema_file = Path(_renderer.schemas_dir) / f"{feature}.json"
    if not schema_file.exists():
        raise HTTPException(404, f"Schema '{feature}.json' not found")
    return {"feature": feature, "content": schema_file.read_text(encoding="utf-8")}


@router.put("/schemas/{feature}/source")
async def save_schema_source(
    feature: str,
    body: SaveTemplateRequest,
    _user: dict = Depends(_require_role("admin")),
):
    """Write back edited JSON schema (admin only)."""
    import json as _json
    from pathlib import Path
    # Validate it's actual JSON before saving
    try:
        _json.loads(body.content)
    except _json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}")
    schema_file = Path(_renderer.schemas_dir) / f"{feature}.json"
    schema_file.write_text(body.content, encoding="utf-8")
    # Clear cached schema
    _renderer._schema_cache.pop(feature, None)
    logger.info("Schema %s updated by %s", feature, _user.get("username"))
    return {"status": "saved", "feature": feature}


@router.delete("/templates/{vendor}/{feature}")
async def delete_template(
    vendor: str,
    feature: str,
    _user: dict = Depends(_require_role("admin")),
):
    """Delete a Jinja2 template file (admin only)."""
    from pathlib import Path
    tpl_file = Path(_renderer.templates_dir) / vendor / f"{feature}.j2"
    if not tpl_file.exists():
        raise HTTPException(404, f"Template '{vendor}/{feature}.j2' not found")
    tpl_file.unlink()
    _renderer._env = None  # force reload
    logger.info("Template %s/%s deleted by %s", vendor, feature, _user.get("username"))
    return {"status": "deleted", "vendor": vendor, "feature": feature}


@router.post("/templates/{vendor}/{feature}/create")
async def create_template(
    vendor: str,
    feature: str,
    body: SaveTemplateRequest,
    _user: dict = Depends(_require_role("admin")),
):
    """Create a new Jinja2 template file (admin only)."""
    from pathlib import Path
    vendor_dir = Path(_renderer.templates_dir) / vendor
    vendor_dir.mkdir(parents=True, exist_ok=True)
    tpl_file = vendor_dir / f"{feature}.j2"
    if tpl_file.exists():
        raise HTTPException(409, f"Template '{vendor}/{feature}.j2' already exists")
    tpl_file.write_text(body.content, encoding="utf-8")
    _renderer._env = None  # force reload
    logger.info("Template %s/%s created by %s", vendor, feature, _user.get("username"))
    return {"status": "created", "vendor": vendor, "feature": feature}

