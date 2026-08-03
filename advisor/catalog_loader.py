"""
Loads and validates the AI Architecture Advisor knowledge base (advisor_kb/).

The KB is the source of truth (see advisor_kb/README.md) — this module never
invents a default. It only loads static YAML/Markdown config once per process
(safe: identical across all prod replicas, see CLAUDE.md's local-vs-prod
table) and validates it against advisor_kb/catalog/_schema.md's rules.

A malformed KB file must fail loudly here, at first load, not silently at
request time — see AdvisorKBError.
"""
import functools
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

KB_ROOT = Path(__file__).resolve().parent.parent / "advisor_kb"

# advisor_kb/catalog/_schema.md: "Every pattern file in catalog/ must contain
# these keys." Positional/name-stable — do not rename without updating the KB.
REQUIRED_PATTERN_KEYS = [
    "id", "name", "service", "status", "summary", "when_to_use", "not_for",
    "match", "design", "security_floor", "required_requests", "prerequisites",
    "diagram", "cost_band", "source",
]
VALID_STATUSES = {"approved", "conditional", "exception"}
MATCH_SUBKEYS = ("required", "preferred", "disqualify")


class AdvisorKBError(Exception):
    """Raised when a KB file is malformed. Always carries the offending file
    and key so the failure is actionable, not a bare traceback."""

    def __init__(self, path, key, reason):
        self.path = path
        self.key = key
        self.reason = reason
        super().__init__(f"{path}: key '{key}' — {reason}")


def _load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise AdvisorKBError(path, "<parse>", f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise AdvisorKBError(path, "<root>", "top-level document must be a mapping")
    return data


def _validate_pattern(path: Path, data: dict) -> dict:
    for key in REQUIRED_PATTERN_KEYS:
        if key not in data:
            raise AdvisorKBError(path, key, "required key is missing")

    if data["status"] not in VALID_STATUSES:
        raise AdvisorKBError(path, "status",
                              f"'{data['status']}' not in {sorted(VALID_STATUSES)}")

    match = data["match"]
    if not isinstance(match, dict):
        raise AdvisorKBError(path, "match", "must be a mapping")
    for sub in MATCH_SUBKEYS:
        if sub in match and not isinstance(match[sub], dict):
            raise AdvisorKBError(path, f"match.{sub}", "must be a mapping")

    if not isinstance(data["when_to_use"], list) or not isinstance(data["not_for"], list):
        raise AdvisorKBError(path, "when_to_use/not_for", "must be lists")

    if not isinstance(data["required_requests"], list):
        raise AdvisorKBError(path, "required_requests", "must be a list")

    diagram_path = KB_ROOT / "diagrams" / data["diagram"]
    if not diagram_path.exists():
        raise AdvisorKBError(path, "diagram", f"referenced file not found: {data['diagram']}")

    return data


def load_catalog(service_dir: str = "catalog") -> dict:
    """Load + validate every pattern YAML under advisor_kb/<service_dir>/
    (files starting with '_' are schema docs, not patterns). Returns
    {pattern_id: pattern_dict}, in catalog file declaration order (dict
    insertion order is preserved — pattern_matcher.py relies on this for its
    file-order tiebreak)."""
    directory = KB_ROOT / service_dir
    if not directory.is_dir():
        raise AdvisorKBError(directory, "<directory>", "catalog directory not found")

    catalog = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = _load_yaml(path)
        data = _validate_pattern(path, data)
        pattern_id = data["id"]
        if pattern_id in catalog:
            raise AdvisorKBError(path, "id", f"duplicate pattern id '{pattern_id}'")
        catalog[pattern_id] = data
    if not catalog:
        raise AdvisorKBError(directory, "<directory>", "no patterns found")
    return catalog


def load_yaml_file(relative_path: str) -> dict:
    """Load a single non-catalog KB file (questions/rules/mapping) with the
    same loud-failure behaviour as load_catalog."""
    path = KB_ROOT / relative_path
    if not path.exists():
        raise AdvisorKBError(path, "<file>", "KB file not found")
    return _load_yaml(path)


def load_text_file(relative_path: str) -> str:
    path = KB_ROOT / relative_path
    if not path.exists():
        raise AdvisorKBError(path, "<file>", "KB file not found")
    return path.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def get_catalog() -> dict:
    """Process-wide cached catalog — static config, safe to cache per the
    module docstring. Raises AdvisorKBError on first call if the KB is
    malformed, so a bad KB fails at first use, not deep inside a request."""
    return load_catalog("catalog")


@functools.lru_cache(maxsize=1)
def get_questions() -> dict:
    return load_yaml_file("questions/storage_questions.yaml")


@functools.lru_cache(maxsize=1)
def get_rules() -> dict:
    return load_yaml_file("rules/storage_decision_matrix.yaml")


@functools.lru_cache(maxsize=1)
def get_mapping() -> dict:
    return load_yaml_file("mapping/storage_request_mapping.yaml")
