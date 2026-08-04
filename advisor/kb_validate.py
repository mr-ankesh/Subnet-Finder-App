"""
Atomic validation gate for an advisor_kb/ upload — stages a-i, run against
the candidate file set BEFORE any of it is persisted (see advisor/kb_store.py).

The KB is executable configuration: a malformed condition string or a
renamed pattern id doesn't error, it produces confidently wrong architecture
advice. Two failure modes already happened once — condition_eval silently
returning True for an operator-less string, and the KB being wrong about AKS
networking for weeks — and this module exists to make both structurally
impossible for any future upload, not just today's shipped KB.

validate_kb(files) never short-circuits on the first failure: every stage
runs, so a rejected upload reports every problem at once (file + key), per
the spec's "return every error with file and key." Nothing here mutates
`files` or touches kb_store/catalog_loader's live source — this module only
ever reads the candidate dict it's given.
"""
import logging
import re

import yaml

from advisor import catalog_loader, condition_eval
from advisor.catalog_loader import AdvisorKBError
from advisor.composer import network_planner

log = logging.getLogger(__name__)

# advisor/services.py's own docstring: "container_registry" and "snet_pe" are
# real tokens that appear as composition_rules.yaml `add:` targets but are
# NOT catalog pattern ids (container_registry is a display-only "you'll also
# need" flag; snet_pe is a subnet id from network_sizing.yaml, not a
# pattern). Treating either as an unresolved pattern reference would reject
# the KB that ships today — see CLAUDE.md's "Environment composer" section.
_NON_PATTERN_ADD_TARGETS = {"container_registry", "snet_pe"}
# advisor_kb/rules/*.yaml's `add_service` is a SERVICE id (for "you'll also
# need" display), not necessarily a SERVICE_FILES key — container_registry
# is the one known non-service token used this way today.
_NON_SERVICE_ADD_SERVICE_TARGETS = {"container_registry"}


def _err(errors, file, key, message):
    errors.append({"file": file, "key": key, "message": message})


def _warn(warnings, file, key, message):
    warnings.append({"file": file, "key": key, "message": message})


# ── Generic tree walkers (schema-shape-tolerant, not per-file hand coding) ──

def _walk_by_key(data, keys):
    """Yields (key, value) for every dict entry anywhere in `data` (nested
    dicts/lists) whose key is in `keys`."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k in keys:
                yield (k, v)
            yield from _walk_by_key(v, keys)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_by_key(item, keys)


def _walk_condition_strings(data, keys):
    """Yields (key, condition_string) for every dict entry anywhere in
    `data` whose key is in `keys` and whose value is either a bare
    condition string (when/if/include_if, and skip_if's short form) or a
    {condition: "...", ...} wrapper dict (skip_if/follow_up_if's long
    form) — both shapes are real, current KB schema (see
    question_engine.py's own docstring)."""
    for key, value in _walk_by_key(data, keys):
        if isinstance(value, str):
            yield (key, value)
        elif isinstance(value, dict) and isinstance(value.get("condition"), str):
            yield (key, value["condition"])


def _yaml_files(files: dict, prefix: str = None):
    for path, content in files.items():
        if prefix is not None and not path.startswith(prefix):
            continue
        if not path.endswith(".yaml"):
            continue
        if path.rsplit("/", 1)[-1].startswith("_"):
            continue
        yield path, content


def _try_parse(path, content, errors) -> dict:
    """Stage (a): parses one file, recording a stage-a error and returning
    None on failure so downstream stages can skip it rather than double
    report or crash on missing data."""
    try:
        return catalog_loader._load_yaml_text(path, content)
    except AdvisorKBError as exc:
        _err(errors, path, exc.key, exc.reason)
        return None


# ── Stage (a) + (b): YAML parses, catalog schema ────────────────────────────

def _stage_ab_catalog(files: dict, errors: list, warnings: list) -> dict:
    """Returns {pattern_id: pattern_dict} for every catalog file that parsed
    and passed schema validation — used by later stages (d, g, h)."""
    catalog = {}
    for path, content in _yaml_files(files, "catalog/"):
        data = _try_parse(path, content, errors)
        if data is None:
            continue
        ok = True
        for key in catalog_loader.REQUIRED_PATTERN_KEYS:
            if key not in data:
                _err(errors, path, key, "required key is missing")
                ok = False
        if not ok:
            continue
        if data["status"] not in catalog_loader.VALID_STATUSES:
            _err(errors, path, "status",
                 f"'{data['status']}' not in {sorted(catalog_loader.VALID_STATUSES)}")
        if not isinstance(data.get("selectable"), bool):
            _err(errors, path, "selectable", "must be true or false")
        match = data.get("match")
        if not isinstance(match, dict):
            _err(errors, path, "match", "must be a mapping")
        else:
            for sub in catalog_loader.MATCH_SUBKEYS:
                if sub in match and not isinstance(match[sub], dict):
                    _err(errors, path, f"match.{sub}", "must be a mapping")
        if not isinstance(data.get("when_to_use"), list) or not isinstance(data.get("not_for"), list):
            _err(errors, path, "when_to_use/not_for", "must be lists")
        if not isinstance(data.get("required_requests"), list):
            _err(errors, path, "required_requests", "must be a list")

        diagram = data.get("diagram")
        if diagram and f"diagrams/{diagram}" not in files:
            _err(errors, path, "diagram", f"referenced file not found in upload: {diagram}")

        # last_verified/verified_by: WARN, never reject (spec §6). No date is
        # fabricated here or anywhere else in this module for a pattern that
        # doesn't declare one — that would be inventing provenance.
        if not data.get("last_verified"):
            _warn(warnings, path, "last_verified", "pattern has no last_verified date")
        elif not data.get("verified_by"):
            _warn(warnings, path, "verified_by", "pattern has a last_verified date but no verified_by")

        pattern_id = data.get("id")
        if pattern_id:
            if pattern_id in catalog:
                _err(errors, path, "id", f"duplicate pattern id '{pattern_id}'")
            else:
                catalog[pattern_id] = data
    if not catalog:
        _err(errors, "catalog/", "<directory>", "no valid patterns found in upload")
    return catalog


# ── Stage (c): condition strings ────────────────────────────────────────────

def _stage_c_conditions(files: dict, catalog_paths: set, errors: list, warnings: list):
    """STRICT (hard reject) for the fields actually run through
    condition_eval.evaluate() at runtime: rules/*.yaml's when/if, and
    questions/*.yaml + composer/environment_questions.yaml's skip_if/
    follow_up_if. questions.yaml's stop_if/escalate_if are deliberately
    excluded — they're plain-English duplicates of rules_engine's real
    blockers/escalations, never evaluated as condition language (see
    question_engine.py's own docstring) — validating them as strict syntax
    would reject the KB that ships today.

    mapping/*.yaml's include_if is WARNING-only: it's evaluated via
    evaluate_safe() at runtime (fails closed, tolerant by design), and the
    shipped KB already contains genuine prose there (e.g.
    "egress_destinations specified") that evaluate_safe intentionally
    treats as False rather than an error — rejecting it here would reject
    working, in-production KB content."""
    for path, content in _yaml_files(files, "rules/"):
        data = _try_parse(path, content, errors)
        if data is None:
            continue
        for key, cond in _walk_condition_strings(data, {"when", "if"}):
            try:
                condition_eval.validate_condition(cond)
            except ValueError as exc:
                _err(errors, path, key, str(exc))

    question_prefixes = ("questions/",)
    for path, content in files.items():
        if not path.endswith(".yaml"):
            continue
        if not (path.startswith(question_prefixes) or path == "composer/environment_questions.yaml"):
            continue
        data = _try_parse(path, content, errors)
        if data is None:
            continue
        for key, cond in _walk_condition_strings(data, {"skip_if", "follow_up_if"}):
            try:
                condition_eval.validate_condition(cond)
            except ValueError as exc:
                _err(errors, path, key, str(exc))

    for path, content in _yaml_files(files, "mapping/"):
        data = _try_parse(path, content, errors)
        if data is None:
            continue
        for key, cond in _walk_condition_strings(data, {"include_if"}):
            try:
                condition_eval.validate_condition(cond)
            except ValueError as exc:
                _warn(warnings, path, key, f"include_if is not strict condition syntax "
                                            f"(tolerated at runtime via evaluate_safe): {exc}")


# ── Stage (d): referential integrity ────────────────────────────────────────

def _stage_d_references(files: dict, catalog: dict, errors: list):
    pattern_ids = set(catalog.keys())

    # design.inherits (within catalog itself)
    for pattern_id, pattern in catalog.items():
        base_id = (pattern.get("design") or {}).get("inherits")
        if base_id and base_id not in pattern_ids:
            _err(errors, f"catalog/{pattern_id}", "design.inherits",
                 f"base pattern '{base_id}' not found in this upload's catalog")

    # rules/*.yaml: escalation `redirect:` targets a pattern id (confirmed
    # usage: postgres_decision_matrix.yaml's self_managed -> vm_workload_standard)
    for path, content in _yaml_files(files, "rules/"):
        data = _try_parse(path, content, errors)
        if data is None:
            continue
        for key, target in _walk_by_key(data, {"redirect"}):
            if isinstance(target, str) and target not in pattern_ids:
                _err(errors, path, "redirect", f"target pattern id '{target}' not found in this upload's catalog")

        # add_service: a SERVICE id (SERVICE_FILES key), not a pattern id —
        # container_registry is the one known non-service display token.
        for key, svc in _walk_by_key(data, {"add_service"}):
            if isinstance(svc, str) and svc not in catalog_loader.SERVICE_FILES \
                    and svc not in _NON_SERVICE_ADD_SERVICE_TARGETS:
                _err(errors, path, "add_service", f"'{svc}' is not a known service id")

    # composer/composition_rules.yaml: `add:` mostly targets a pattern id,
    # with two known non-pattern tokens (container_registry, snet_pe — see
    # module docstring) that are NOT rejected.
    comp_path = "composer/composition_rules.yaml"
    if comp_path in files:
        data = _try_parse(comp_path, files[comp_path], errors)
        if data is not None:
            for key, target in _walk_by_key(data, {"add"}):
                if isinstance(target, str) and target not in pattern_ids \
                        and target not in _NON_PATTERN_ADD_TARGETS:
                    _err(errors, comp_path, "add", f"target '{target}' is not a known pattern id "
                                                    f"or recognized non-pattern token")


# ── Stage (f): glossary related: terms resolve ──────────────────────────────

_GLOSSARY_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_term(text: str) -> str:
    """Same normalization as advisor/glossary.py's _normalize() — related:
    entries are resolved by the app through that same case/punctuation-
    insensitive, alias-aware index (a user clicking a related-term chip goes
    through find_term()), so validating against a raw exact `term:` string
    match would be stricter than reality and reject legitimately-resolvable
    references."""
    return " ".join(_GLOSSARY_WORD_RE.findall((text or "").lower()))


def _stage_f_glossary(files: dict, errors: list):
    path = "glossary.yaml"
    if path not in files:
        return  # absence is a stage (a)/directory-listing concern elsewhere, not this stage's job
    data = _try_parse(path, files[path], errors)
    if data is None:
        return
    terms = data.get("terms") or []
    known = set()
    for t in terms:
        if not isinstance(t, dict) or not t.get("term"):
            continue
        known.add(_normalize_term(t["term"]))
        for alias in t.get("aliases") or []:
            known.add(_normalize_term(alias))
    for t in terms:
        if not isinstance(t, dict):
            continue
        for rel in (t.get("related") or []):
            if _normalize_term(rel) not in known:
                _err(errors, path, f"terms[{t.get('term')}].related",
                     f"related term '{rel}' does not resolve to another glossary term or alias")


# ── Stage (g) + (h): selectable bidirectional + SERVICE_FILES completeness ──

def _stage_gh_services(files: dict, catalog: dict, errors: list):
    # (h) unconditional: every SERVICE_FILES-declared service's 3 files must
    # be present, regardless of what the catalog says — SERVICE_FILES is a
    # fixed code-side contract, not derived from the KB.
    for svc, file_map in catalog_loader.SERVICE_FILES.items():
        for kind, rel_path in file_map.items():
            if rel_path not in files:
                _err(errors, rel_path, "<file>", f"service '{svc}' is missing its {kind} file")

    # (g) bidirectional: selectable:true needs a file mapping to exist FOR
    # that pattern's service; selectable:false must not have files present
    # for a service with no selectable pattern (a forgotten question bank
    # vs. a deliberate omission must stay distinguishable).
    services_with_selectable = set()
    for pattern_id, pattern in catalog.items():
        if pattern.get("selectable") is True:
            services_with_selectable.add(pattern.get("service"))
            if pattern.get("service") not in catalog_loader.SERVICE_FILES:
                _err(errors, f"catalog/{pattern_id}", "selectable",
                     f"selectable: true but service '{pattern.get('service')}' has no "
                     f"question/rules/mapping file mapping (not in SERVICE_FILES)")

    for svc, file_map in catalog_loader.SERVICE_FILES.items():
        has_any_files = any(rel_path in files for rel_path in file_map.values())
        if has_any_files and svc not in services_with_selectable:
            _err(errors, file_map["questions"], "selectable",
                 f"service '{svc}' has question/rules/mapping files present in this upload "
                 f"but no catalog pattern marks it selectable: true")


# ── Stage (i): canonical_examples arithmetic ────────────────────────────────

def _stage_i_canonical_examples(files: dict, errors: list):
    path = "composer/network_sizing.yaml"
    if path not in files:
        return
    data = _try_parse(path, files[path], errors)
    if data is None:
        return
    examples = data.get("canonical_examples") or {}
    for name, example in examples.items():
        if not isinstance(example, dict) or "subnets" not in example:
            continue
        subnets = example["subnets"]
        try:
            plan = network_planner.compute_vnet_plan(subnets)
        except Exception as exc:
            _err(errors, path, f"canonical_examples.{name}.subnets", f"could not compute plan: {exc}")
            continue

        def _num(x):
            try:
                return int(str(x).split()[0])
            except (ValueError, IndexError):
                return None

        arithmetic = example.get("arithmetic", "")
        stated_sum = _num(arithmetic.rsplit("=", 1)[-1]) if "=" in arithmetic else None
        if stated_sum is not None and stated_sum != plan["arithmetic_sum"]:
            _err(errors, path, f"canonical_examples.{name}.arithmetic",
                 f"stated sum {stated_sum} != computed sum {plan['arithmetic_sum']}")

        stated_vnet = example.get("vnet")
        if stated_vnet is not None and stated_vnet != plan["vnet_size"]:
            _err(errors, path, f"canonical_examples.{name}.vnet",
                 f"stated VNET size {stated_vnet} != computed {plan['vnet_size']}")

        stated_capacity = example.get("capacity")
        if stated_capacity is not None and stated_capacity != plan["capacity"]:
            _err(errors, path, f"canonical_examples.{name}.capacity",
                 f"stated capacity {stated_capacity} != computed {plan['capacity']}")

        stated_pct = example.get("utilisation_percent")
        if stated_pct is not None and float(stated_pct) != plan["utilisation_pct"]:
            _err(errors, path, f"canonical_examples.{name}.utilisation_percent",
                 f"stated utilisation {stated_pct} != computed {plan['utilisation_pct']}")

        stated_spare = example.get("spare")
        if stated_spare is not None and stated_spare != plan["spare"]:
            _err(errors, path, f"canonical_examples.{name}.spare",
                 f"stated spare {stated_spare} != computed {plan['spare']}")

        stated_flag = example.get("flag_tripped")
        if stated_flag is not None and bool(stated_flag) != plan["flag_tripped"]:
            _err(errors, path, f"canonical_examples.{name}.flag_tripped",
                 f"stated flag_tripped {stated_flag} != computed {plan['flag_tripped']}")


# ── Everything else: every remaining YAML file at least parses ─────────────

def _stage_a_remaining(files: dict, errors: list):
    """Stage (a) for every YAML file not already parsed by a more specific
    stage above (composer/*.yaml other than network_sizing.yaml/
    composition_rules.yaml, glossary.yaml already covered by stage f,
    questions/rules/mapping already covered by stage c, catalog already
    covered by stage b) — every remaining YAML file must still at least
    parse cleanly."""
    already = set()
    for path in files:
        if path.startswith(("catalog/", "questions/", "rules/", "mapping/")) and path.endswith(".yaml"):
            already.add(path)
    already.add("glossary.yaml")
    already.add("composer/network_sizing.yaml")
    already.add("composer/composition_rules.yaml")
    for path, content in files.items():
        if not path.endswith(".yaml") or path in already or path.rsplit("/", 1)[-1].startswith("_"):
            continue
        _try_parse(path, content, errors)


def validate_kb(files: dict) -> dict:
    """files: {relative_path: content}. Runs every stage regardless of
    earlier failures so a rejected upload reports everything at once.
    Returns {"ok": bool, "errors": [...], "warnings": [...]}."""
    errors = []
    warnings = []

    catalog = _stage_ab_catalog(files, errors, warnings)          # (a) + (b)
    _stage_c_conditions(files, set(files.keys()), errors, warnings)  # (c)
    _stage_d_references(files, catalog, errors)                   # (d)
    # (e) is folded into stage b's per-pattern diagram check above.
    _stage_f_glossary(files, errors)                               # (f)
    _stage_gh_services(files, catalog, errors)                     # (g) + (h)
    _stage_i_canonical_examples(files, errors)                     # (i)
    _stage_a_remaining(files, errors)                              # (a) for everything else

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
