"""
M3: Configuration Engine — Template-based config generation.

Exports:
    TemplateRenderer — Jinja2 template rendering for all vendors
    ConfigDiffer     — Unified / side-by-side config diff
    SyntaxValidator  — Per-vendor syntax checks
"""

from backend.config_engine.renderer import TemplateRenderer
from backend.config_engine.differ import ConfigDiffer
from backend.config_engine.validator import SyntaxValidator

__all__ = [
    "TemplateRenderer",
    "ConfigDiffer",
    "SyntaxValidator",
]
