"""
Resource-name rendering from the admin-configurable naming templates.

Templates live in SETTINGS_SPEC (TPL_* keys) and support these placeholders:
  {vnet} {request_id} {region} {cidr_mask} {purpose} {date}
The global NAME_PREFIX / NAME_SUFFIX are joined with '-' around the rendered
template, and the result is sanitised to an Azure-legal resource name.
"""
import re
from datetime import datetime

from config import cfg

PLACEHOLDERS = ["vnet", "request_id", "region", "cidr_mask", "purpose", "date"]


def _slug(value) -> str:
    """Lowercase, alnum + dash — safe inside an Azure resource name."""
    s = re.sub(r"[^A-Za-z0-9-]+", "-", str(value or "")).strip("-")
    return s.lower()


def sanitize(name: str, max_len: int = 80) -> str:
    """Clamp to Azure resource-name rules: alnum/-/_/. , starts alnum, ends alnum or _."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    s = s[:max_len].rstrip("-.")
    return s or "unnamed"


def render_name(template_key: str, *, vnet="", request_id="", region="", cidr_mask="", purpose="",
                template_override=None, prefix_override=None, suffix_override=None) -> str:
    """
    Render a TPL_* naming template with prefix/suffix applied.
    The *_override kwargs let the settings UI preview unsaved values.
    """
    template = template_override if template_override is not None else (getattr(cfg, template_key) or "")
    ctx = {
        "vnet":       _slug(vnet),
        "request_id": _slug(request_id),
        "region":     _slug(region),
        "cidr_mask":  _slug(str(cidr_mask).lstrip("/")),
        "purpose":    _slug(purpose)[:30],
        "date":       datetime.utcnow().strftime("%Y%m%d"),
    }
    try:
        rendered = template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        # Unknown placeholder in a custom template — substitute what we can.
        rendered = template
        for k, v in ctx.items():
            rendered = rendered.replace("{%s}" % k, v)

    prefix = prefix_override if prefix_override is not None else cfg.NAME_PREFIX
    suffix = suffix_override if suffix_override is not None else cfg.NAME_SUFFIX
    parts = [p for p in (prefix, rendered, suffix) if p]
    return sanitize("-".join(parts))
