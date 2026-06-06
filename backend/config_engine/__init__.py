"""
M3: Configuration Engine — Template-based config generation.

Exports:
    TemplateRenderer — Jinja2 template rendering for all vendors
    ConfigDiffer     — Unified / side-by-side config diff
    SyntaxValidator  — Per-vendor syntax checks
"""

from config_engine.renderer import TemplateRenderer
from config_engine.differ import ConfigDiffer
from config_engine.validator import SyntaxValidator

__all__ = [
    "TemplateRenderer",
    "ConfigDiffer",
    "SyntaxValidator",
]
