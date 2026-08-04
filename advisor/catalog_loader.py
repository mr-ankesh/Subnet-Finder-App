"""
Loads and validates the AI Architecture Advisor knowledge base (advisor_kb/).

The KB is the source of truth (see advisor_kb/README.md) — this module never
invents a default. Resolution order mirrors config.py's DB-override chain:
an explicit per-conversation pin (see pinned_to()) > the active DB-stored KB
version (advisor/kb_store.py) > the files on disk. With no DB version ever
uploaded, every read comes from disk exactly as before KB management existed
(advisor_kb/ is baked into the container image; see kb_store.py's own
docstring for why uploads never touch disk).

A malformed KB file must fail loudly here, at first load, not silently at
request time — see AdvisorKBError.

Caching: the six accessors below are process-wide lru_caches — safe when the
KB is static disk content (identical across every prod replica), which is
all this module used to have to handle. Now that a KB version can change at
runtime via Settings -> Knowledge Base, _maybe_reload() gates every accessor
on a cheap "which version is currently the source" check (itself backed by
kb_store.get_active_version()'s own 5s TTL cache) and clears every lru_cache
the moment that changes — so an activation takes effect on every replica
within one TTL window, not at next restart.
"""
import contextlib
import contextvars
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
    "diagram", "cost_band", "source", "selectable",
]
# Optional — validated (warned on if missing/stale), never required. See
# advisor/kb_validate.py's staleness warning pass.
OPTIONAL_PATTERN_KEYS = ["last_verified", "verified_by"]
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
# (see advisor/services.py's docstring for why) — selectable: false.
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


# ── Source resolution: pin > active DB version > disk ──────────────────────

_NO_PIN = object()
_kb_version_pin = contextvars.ContextVar("advisor_kb_version_pin", default=_NO_PIN)


@contextlib.contextmanager
def pinned_to(version_id):
    """Pin every catalog_loader read within this `with` block to a specific
    DB-stored KB version id, or to disk if `version_id` is None. Used by
    advisor/orchestrator.py to wrap a whole conversation turn so an in-flight
    conversation always resolves against the KB version recorded on it at
    creation (advisor_conversations.kb_version_id), even if a different
    version is activated by an admin mid-conversation. Nested/re-entrant
    calls restore the previous pin (or "no pin") on exit via ContextVar.reset,
    so this composes safely if ever nested."""
    token = _kb_version_pin.set(version_id)
    try:
        yield
    finally:
        _kb_version_pin.reset(token)


def _current_source():
    """('pin', version_id) | ('disk', None) | ('db', version_id)."""
    pin = _kb_version_pin.get()
    if pin is not _NO_PIN:
        return ("disk", None) if pin is None else ("pin", pin)
    try:
        import advisor.kb_store as kb_store
        active = kb_store.get_active_version()
    except Exception:
        log.warning("advisor kb_store unreachable; falling back to disk KB", exc_info=True)
        active = None
    return ("db", active["id"]) if active else ("disk", None)


_loaded_key = None
_loaded_files = None  # dict when source is DB-backed; None means "read KB_ROOT from disk"


def _maybe_reload():
    global _loaded_key, _loaded_files
    key = _current_source()
    if key == _loaded_key:
        return
    _get_catalog_impl.cache_clear()
    _get_questions_impl.cache_clear()
    _get_rules_impl.cache_clear()
    _get_mapping_impl.cache_clear()
    _get_platform_constants_impl.cache_clear()
    _get_composer_file_impl.cache_clear()
    kind, version_id = key
    if kind == "disk":
        _loaded_files = None
    else:
        import advisor.kb_store as kb_store
        _loaded_files = kb_store.get_files(version_id)
    _loaded_key = key


def _source_exists(relative_path: str) -> bool:
    _maybe_reload()
    if _loaded_files is not None:
        return relative_path in _loaded_files
    return (KB_ROOT / relative_path).exists()


def _read_source(relative_path: str) -> str:
    _maybe_reload()
    if _loaded_files is not None:
        if relative_path not in _loaded_files:
            raise AdvisorKBError(relative_path, "<file>", "KB file not found")
        return _loaded_files[relative_path]
    path = KB_ROOT / relative_path
    if not path.exists():
        raise AdvisorKBError(path, "<file>", "KB file not found")
    return path.read_text(encoding="utf-8")


def _list_relative(directory_prefix: str) -> list:
    """Filenames (not full paths) directly under advisor_kb/<directory_prefix>/
    from whichever source is currently active — mirrors Path.glob('*.yaml')'s
    immediate-children-only semantics."""
    _maybe_reload()
    if _loaded_files is not None:
        prefix = f"{directory_prefix}/"
        names = {rel[len(prefix):] for rel in _loaded_files
                 if rel.startswith(prefix) and "/" not in rel[len(prefix):]}
        return sorted(n for n in names if n.endswith(".yaml"))
    directory = KB_ROOT / directory_prefix
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.glob("*.yaml"))


# ── Parsing ──────────────────────────────────────────────────────────────

def _load_yaml_text(source_label, text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AdvisorKBError(source_label, "<parse>", f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise AdvisorKBError(source_label, "<root>", "top-level document must be a mapping")
    return data


def _load_yaml(relative_path: str) -> dict:
    return _load_yaml_text(relative_path, _read_source(relative_path))


def _validate_pattern(source_label, data: dict) -> dict:
    for key in REQUIRED_PATTERN_KEYS:
        if key not in data:
            raise AdvisorKBError(source_label, key, "required key is missing")

    if data["status"] not in VALID_STATUSES:
        raise AdvisorKBError(source_label, "status",
                              f"'{data['status']}' not in {sorted(VALID_STATUSES)}")

    if not isinstance(data["selectable"], bool):
        raise AdvisorKBError(source_label, "selectable", "must be true or false")

    match = data["match"]
    if not isinstance(match, dict):
        raise AdvisorKBError(source_label, "match", "must be a mapping")
    for sub in MATCH_SUBKEYS:
        if sub in match and not isinstance(match[sub], dict):
            raise AdvisorKBError(source_label, f"match.{sub}", "must be a mapping")

    if not isinstance(data["when_to_use"], list) or not isinstance(data["not_for"], list):
        raise AdvisorKBError(source_label, "when_to_use/not_for", "must be lists")

    if not isinstance(data["required_requests"], list):
        raise AdvisorKBError(source_label, "required_requests", "must be a list")

    if not _source_exists(f"diagrams/{data['diagram']}"):
        raise AdvisorKBError(source_label, "diagram", f"referenced file not found: {data['diagram']}")

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
    file-order tiebreak). All five services' patterns live in this one flat
    directory, distinguished by each pattern's own `service:` field —
    callers that need only one service's patterns filter by that field."""
    names = _list_relative(service_dir)
    catalog = {}
    for name in names:
        if name.startswith("_"):
            continue
        relative_path = f"{service_dir}/{name}"
        data = _load_yaml(relative_path)
        data = _validate_pattern(relative_path, data)
        pattern_id = data["id"]
        if pattern_id in catalog:
            raise AdvisorKBError(relative_path, "id", f"duplicate pattern id '{pattern_id}'")
        catalog[pattern_id] = data
    if not catalog:
        raise AdvisorKBError(service_dir, "<directory>", "no patterns found")
    return _resolve_inherits(catalog)


def load_yaml_file(relative_path: str) -> dict:
    """Load a single non-catalog KB file (questions/rules/mapping) with the
    same loud-failure behaviour as load_catalog."""
    return _load_yaml(relative_path)


def load_text_file(relative_path: str) -> str:
    return _read_source(relative_path)


# ── Cached accessors ─────────────────────────────────────────────────────
# Public names are stable — every other advisor/ module calls these exactly
# as before. Each is now a thin wrapper: check whether the active KB source
# changed (cheap; TTL-cached two layers down in kb_store), then defer to a
# private lru_cache'd implementation that does the real parsing work.

@functools.lru_cache(maxsize=1)
def _get_catalog_impl() -> dict:
    return load_catalog("catalog")


def get_catalog() -> dict:
    """Process-wide cached catalog — cleared automatically when the active
    KB source changes (activation, revert, or entering/leaving a pinned_to()
    block). Raises AdvisorKBError if the KB is malformed, so a bad KB fails
    at first use, not deep inside a request."""
    _maybe_reload()
    return _get_catalog_impl()


def _service_file(service: str, key: str) -> str:
    files = SERVICE_FILES.get(service)
    if files is None:
        raise AdvisorKBError(service, "<service>", f"unknown advisor service '{service}'")
    return files[key]


@functools.lru_cache(maxsize=None)
def _get_questions_impl(service: str) -> dict:
    return load_yaml_file(_service_file(service, "questions"))


def get_questions(service: str) -> dict:
    _maybe_reload()
    return _get_questions_impl(service)


@functools.lru_cache(maxsize=None)
def _get_rules_impl(service: str) -> dict:
    return load_yaml_file(_service_file(service, "rules"))


def get_rules(service: str) -> dict:
    _maybe_reload()
    return _get_rules_impl(service)


@functools.lru_cache(maxsize=None)
def _get_mapping_impl(service: str) -> dict:
    return load_yaml_file(_service_file(service, "mapping"))


def get_mapping(service: str) -> dict:
    _maybe_reload()
    return _get_mapping_impl(service)


@functools.lru_cache(maxsize=1)
def _get_platform_constants_impl() -> dict:
    return load_yaml_file("rules/platform_constants.yaml")


def get_platform_constants() -> dict:
    """Shared, service-agnostic reference facts (naming pattern, DNS zones,
    encryption floor, etc.) — loaded once per KB generation and attached to
    every service's rule result. Never referenced inside a decision matrix's
    `when:` conditions; it's rendering data, not rule input."""
    _maybe_reload()
    return _get_platform_constants_impl()


@functools.lru_cache(maxsize=None)
def _get_composer_file_impl(name: str) -> dict:
    return load_yaml_file(f"composer/{name}")


def get_composer_file(name: str) -> dict:
    """A composer/*.yaml file (e.g. infosec_gate.yaml), referenced by a
    decision matrix escalation's `message_ref`. Its `user_message` is
    rendered verbatim by recommendation.py — never passed through the LLM —
    so this loader does no interpretation, just loads the raw dict."""
    _maybe_reload()
    return _get_composer_file_impl(name)
