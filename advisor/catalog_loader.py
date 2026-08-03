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

# advisor_kb/MIGRATION.md §1: per-service file map. Keys match every KB
# file's own `service:` field (advisor_kb/rules/*.yaml, questions/*.yaml,
# mapping/*.yaml all agree) — NOT necessarily the real RequestType, which
# only ever lives in a mapping file's own `target_request_type` (storage's
# service is `storage_account`, but its target_request_type is
# `storage_account_create`; see prefill.py). All patterns across every
# service live in one flat catalog/ directory (distinguished by their own
# `service:` field) — only questions/rules/mapping are genuinely per-service
# files. key_vault has a catalog entry (other patterns can cite it) but no
# question/rules/mapping row: it's reference-only, not a selectable service
# (see advisor/services.py's docstring for why).
SERVICE_FILES = {
    "storage_account": {
        "questions": "questions/storage_questions.yaml",
        "rules": "rules/storage_decision_matrix.yaml",
        "mapping": "mapping/storage_request_mapping.yaml",
    },
    "aks_cluster": {
        "questions": "questions/aks_questions.yaml",
        "rules": "rules/aks_decision_matrix.yaml",
        "mapping": "mapping/aks_request_mapping.yaml",
    },
    "vm_create": {
        "questions": "questions/vm_questions.yaml",
        "rules": "rules/vm_decision_matrix.yaml",
        "mapping": "mapping/vm_request_mapping.yaml",
    },
    "postgres_create": {
        "questions": "questions/postgres_questions.yaml",
        "rules": "rules/postgres_decision_matrix.yaml",
        "mapping": "mapping/postgres_request_mapping.yaml",
    },
    "app_gateway": {
        "questions": "questions/appgw_questions.yaml",
        "rules": "rules/appgw_decision_matrix.yaml",
        "mapping": "mapping/appgw_request_mapping.yaml",
    },
}


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


def _resolve_inherits(catalog: dict) -> dict:
    """Pattern `design.inherits: <base_pattern_id>` (e.g. aks_gpu_nodepool
    inheriting aks_private_standard's design): shallow-merge the base
    pattern's design dict under the child's own — child keys win, so the
    child only needs to declare what's genuinely different. Resolved once
    here so pattern_matcher/prefill/recommendation only ever see a fully
    resolved `design`, never inheritance logic."""
    for pattern_id, pattern in catalog.items():
        design = pattern.get("design") or {}
        base_id = design.get("inherits")
        if not base_id:
            continue
        base = catalog.get(base_id)
        if base is None:
            raise AdvisorKBError(pattern_id, "design.inherits",
                                  f"base pattern '{base_id}' not found in catalog")
        merged = dict(base.get("design") or {})
        merged.update(design)
        merged.pop("inherits", None)
        pattern["design"] = merged
    return catalog


def load_catalog(service_dir: str = "catalog") -> dict:
    """Load + validate every pattern YAML under advisor_kb/<service_dir>/
    (files starting with '_' are schema docs, not patterns). Returns
    {pattern_id: pattern_dict}, in catalog file declaration order (dict
    insertion order is preserved — pattern_matcher.py relies on this for its
    file-order tiebreak). All six services' patterns live in this one flat
    directory, distinguished by each pattern's own `service:` field —
    callers that need only one service's patterns filter by that field."""
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
    return _resolve_inherits(catalog)


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


def _service_file(service: str, key: str) -> str:
    files = SERVICE_FILES.get(service)
    if files is None:
        raise AdvisorKBError(service, "<service>", f"unknown advisor service '{service}'")
    return files[key]


@functools.lru_cache(maxsize=None)
def get_questions(service: str) -> dict:
    return load_yaml_file(_service_file(service, "questions"))


@functools.lru_cache(maxsize=None)
def get_rules(service: str) -> dict:
    return load_yaml_file(_service_file(service, "rules"))


@functools.lru_cache(maxsize=None)
def get_mapping(service: str) -> dict:
    return load_yaml_file(_service_file(service, "mapping"))


@functools.lru_cache(maxsize=1)
def get_platform_constants() -> dict:
    """Shared, service-agnostic reference facts (naming pattern, DNS zones,
    encryption floor, etc.) — loaded once and attached to every service's
    rule result. Never referenced inside a decision matrix's `when:`
    conditions (confirmed: none of the five matrices do); it's rendering
    data, not rule input."""
    return load_yaml_file("rules/platform_constants.yaml")


@functools.lru_cache(maxsize=None)
def get_composer_file(name: str) -> dict:
    """A composer/*.yaml file (e.g. infosec_gate.yaml), referenced by a
    decision matrix escalation's `message_ref`. Its `user_message` is
    rendered verbatim by recommendation.py — never passed through the LLM —
    so this loader does no interpretation, just loads the raw dict."""
    return load_yaml_file(f"composer/{name}")
