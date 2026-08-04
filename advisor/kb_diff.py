"""
Semantic dry-run diff between two advisor_kb/ file sets — effects, not text.

"A reviewer needs to see that a blocker stopped firing, not that line 47
changed." diff_kb() never returns a line-based text diff; every section
describes a concrete effect (a pattern added/removed, a condition string
changed, a service becoming (non-)selectable, a security floor loosened) so
an admin reviewing an upload before activation can see what actually
changes, not just what bytes moved.

Best-effort, not a merge tool: every file is parsed independently and a
parse failure is simply skipped here (kb_validate.py is what rejects a
malformed upload — this module only ever runs on an already-validated file
set, via the routes layer, so a parse failure here should not happen in
practice; skipping rather than raising keeps diff_kb usable defensively
regardless). Condition-string diffing pairs up "leftover" old/new strings
per file positionally after removing exact matches — a lightweight
heuristic good enough for a human review aid, not a precise structural diff.
"""
import logging

from advisor import catalog_loader
from advisor.catalog_loader import AdvisorKBError

log = logging.getLogger(__name__)


def _parse(files: dict, path: str):
    if path not in files:
        return None
    try:
        return catalog_loader._load_yaml_text(path, files[path])
    except AdvisorKBError:
        return None


def _catalog(files: dict) -> dict:
    """{pattern_id: pattern_dict} for every catalog/*.yaml file that parses
    and has an id — best-effort, mirrors kb_validate's stage (a)+(b) parse
    step without re-running full schema validation (that's not this
    module's job)."""
    out = {}
    for path in files:
        if not (path.startswith("catalog/") and path.endswith(".yaml")):
            continue
        if path.rsplit("/", 1)[-1].startswith("_"):
            continue
        data = _parse(files, path)
        if isinstance(data, dict) and data.get("id"):
            out[data["id"]] = data
    return out


def _glossary_terms(files: dict) -> dict:
    data = _parse(files, "glossary.yaml")
    if not isinstance(data, dict):
        return {}
    return {t["term"]: t for t in (data.get("terms") or []) if isinstance(t, dict) and t.get("term")}


def _walk_by_key(data, keys):
    if isinstance(data, dict):
        for k, v in data.items():
            if k in keys:
                yield (k, v)
            yield from _walk_by_key(v, keys)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_by_key(item, keys)


def _condition_strings(data, keys) -> list:
    out = []
    for key, value in _walk_by_key(data, keys):
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict) and isinstance(value.get("condition"), str):
            out.append(value["condition"])
    return out


def _diff_patterns(old_catalog: dict, new_catalog: dict) -> dict:
    old_ids, new_ids = set(old_catalog), set(new_catalog)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    modified = []
    for pattern_id in sorted(old_ids & new_ids):
        old_p, new_p = old_catalog[pattern_id], new_catalog[pattern_id]
        changes = []
        for key in sorted(set(old_p) | set(new_p)):
            if key in ("last_verified", "verified_by"):
                continue  # staleness metadata churn isn't a meaningful pattern change to surface here
            if old_p.get(key) != new_p.get(key):
                changes.append({"field": key, "old": old_p.get(key), "new": new_p.get(key)})
        if changes:
            modified.append({"id": pattern_id, "changes": changes})
    return {"added": added, "removed": removed, "modified": modified}


def _diff_conditions(old_files: dict, new_files: dict) -> list:
    changed = []
    all_paths = {p for p in set(old_files) | set(new_files) if p.endswith(".yaml")}
    for path in sorted(all_paths):
        if path.startswith("rules/"):
            keys = {"when", "if"}
        elif path.startswith("questions/") or path == "composer/environment_questions.yaml":
            keys = {"skip_if", "follow_up_if"}
        elif path.startswith("mapping/"):
            keys = {"include_if"}
        else:
            continue
        old_data = _parse(old_files, path)
        new_data = _parse(new_files, path)
        olds = _condition_strings(old_data, keys) if old_data else []
        news = _condition_strings(new_data, keys) if new_data else []
        olds_remaining = list(olds)
        news_remaining = []
        for n in news:
            if n in olds_remaining:
                olds_remaining.remove(n)
            else:
                news_remaining.append(n)
        for o, n in zip(olds_remaining, news_remaining):
            changed.append({"file": path, "old": o, "new": n})
    return changed


def _diff_selectable(old_catalog: dict, new_catalog: dict) -> list:
    def _service_selectable(catalog):
        out = {}
        for svc in catalog_loader.SERVICE_FILES:
            out[svc] = any(p.get("selectable") for p in catalog.values() if p.get("service") == svc)
        return out

    old_sel, new_sel = _service_selectable(old_catalog), _service_selectable(new_catalog)
    return [{"service": svc, "old": old_sel[svc], "new": new_sel[svc]}
            for svc in old_sel if old_sel[svc] != new_sel[svc]]


def _diff_glossary(old_files: dict, new_files: dict) -> dict:
    old_terms, new_terms = set(_glossary_terms(old_files)), set(_glossary_terms(new_files))
    return {"added": sorted(new_terms - old_terms), "removed": sorted(old_terms - new_terms)}


def _diff_security_floor(old_catalog: dict, new_catalog: dict) -> list:
    out = []
    for pattern_id in sorted(set(old_catalog) & set(new_catalog)):
        old_floor = old_catalog[pattern_id].get("security_floor")
        new_floor = new_catalog[pattern_id].get("security_floor")
        if old_floor != new_floor:
            out.append({"pattern_id": pattern_id, "old": old_floor, "new": new_floor, "highlight": True})
    return out


def _diff_locked_fields(old_files: dict, new_files: dict) -> list:
    out = []
    all_paths = {p for p in set(old_files) | set(new_files)
                 if p.startswith("mapping/") and p.endswith(".yaml")}
    for path in sorted(all_paths):
        old_data = _parse(old_files, path) or {}
        new_data = _parse(new_files, path) or {}
        old_locked = old_data.get("locked_fields") or {}
        new_locked = new_data.get("locked_fields") or {}
        for field in sorted(set(old_locked) | set(new_locked)):
            old_v = (old_locked.get(field) or {}).get("value")
            new_v = (new_locked.get(field) or {}).get("value")
            if old_v != new_v:
                out.append({"file": path, "field": field, "old": old_v, "new": new_v, "highlight": True})
    return out


def _diff_sources(old_catalog: dict, new_catalog: dict) -> list:
    """source: entries are structured dicts ({doc, section, states}), not
    hashable strings — membership-compared via list containment, not set
    difference."""
    out = []
    for pattern_id in sorted(set(old_catalog) & set(new_catalog)):
        old_sources = old_catalog[pattern_id].get("source") or []
        new_sources = new_catalog[pattern_id].get("source") or []
        removed = [s for s in old_sources if s not in new_sources]
        if removed:
            out.append({"pattern_id": pattern_id, "removed": removed, "highlight": True})
    return out


def _diff_canonical_examples(old_files: dict, new_files: dict) -> list:
    path = "composer/network_sizing.yaml"
    old_data = _parse(old_files, path) or {}
    new_data = _parse(new_files, path) or {}
    old_ex = old_data.get("canonical_examples") or {}
    new_ex = new_data.get("canonical_examples") or {}
    out = []
    for name in sorted(set(old_ex) | set(new_ex)):
        o, n = old_ex.get(name), new_ex.get(name)
        if o != n:
            out.append({"example": name, "old": o, "new": n})
    return out


def diff_kb(old_files: dict, new_files: dict) -> dict:
    """old_files={} means 'no prior DB version' — everything in new_files is
    reported as added, not an error."""
    old_catalog = _catalog(old_files)
    new_catalog = _catalog(new_files)
    pattern_diff = _diff_patterns(old_catalog, new_catalog)
    return {
        "patterns_added": pattern_diff["added"],
        "patterns_removed": pattern_diff["removed"],
        "patterns_modified": pattern_diff["modified"],
        "conditions_changed": _diff_conditions(old_files, new_files),
        "services_selectable_changed": _diff_selectable(old_catalog, new_catalog),
        "glossary_added": _diff_glossary(old_files, new_files)["added"],
        "glossary_removed": _diff_glossary(old_files, new_files)["removed"],
        "security_floor_changed": _diff_security_floor(old_catalog, new_catalog),
        "locked_fields_changed": _diff_locked_fields(old_files, new_files),
        "sources_removed": _diff_sources(old_catalog, new_catalog),
        "canonical_examples_changed": _diff_canonical_examples(old_files, new_files),
    }
