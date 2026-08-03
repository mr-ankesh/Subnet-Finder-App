"""
Executes a service's advisor_kb/rules/<service>_decision_matrix.yaml phases,
in that matrix's own declared execution_order, against the answers captured
so far. This runs BEFORE the LLM and its output is authoritative — the LLM
may only narrate what this module decided (see advisor_kb/README.md's
non-negotiable).

Blockers/escalations are safe to re-run after every single answer (not just
once at the end): a rule referencing a field that hasn't been answered yet
simply doesn't fire, so calling evaluate_blockers() incrementally is how
"answering 'no subscription' halts immediately" (rather than after the whole
questionnaire) is implemented, without a second parallel halting mechanism.

Storage's matrix declares 7 phases (blockers, escalations, constants,
derivations, pattern_selection, deviations, warnings); the five new
services' matrices declare 6 (no `constants` — that phase is storage's own
per-pattern exception list, e.g. archive's LRS/GRS, not shared). evaluate_full
iterates whatever execution_order the active matrix declares instead of
assuming the fixed 7, so this one engine serves every service.

Cross-service mechanics new in the six-service delta:
  - `add_service` (an escalation's own field, or a derivation's
    `set: "add_service = X"`) flags a companion service the recommendation
    should also list ("You'll also need..."). Derivations can set this
    several times (persistent storage, a database, "always" -> container
    registry) — apply_set's plain overwrite semantics would lose all but the
    last one, so add_service assignments are intercepted and accumulated
    into a list instead of written into ctx like any other field.
  - `redirect` (an escalation field, e.g. Postgres's self_managed ->
    vm_workload_standard): carried through on the escalation entry;
    recommendation.py decides how to render it (informational only, never a
    fabricated cross-service prefill — see its own docstring).
  - `message_ref` (an escalation field, e.g. AppGW's infosec_onboarding ->
    infosec_gate.yaml): the referenced composer file's `user_message` is
    loaded here and attached to the escalation entry verbatim, so
    recommendation.py can render it without ever passing it through the LLM.
"""
import logging

from advisor.catalog_loader import get_catalog, get_rules, get_composer_file
from advisor.condition_eval import evaluate, evaluate_safe, apply_set
from advisor import pattern_matcher

log = logging.getLogger(__name__)

# Free-text phrases that mean "give me a public endpoint" — the KB's own
# question banks deliberately never ask about public access directly
# (conversation_rules: "Never ask about ... public access — those are
# Presight standards, not user choices"), so the only way this signal can
# arise is from something the user typed unprompted. This is an explicit,
# narrow keyword heuristic, not an invented policy — the policy itself
# (blocked, route to platform+security) comes entirely from the KB; this
# only detects the intent that should trigger it.
_PUBLIC_ACCESS_PHRASES = (
    "public access", "publicly accessible", "public endpoint",
    "expose it publicly", "make it public", "no private endpoint",
    "without a private endpoint", "public ip",
)


def detect_public_access_request(free_text: str) -> bool:
    text = (free_text or "").lower()
    return any(p in text for p in _PUBLIC_ACCESS_PHRASES)


def _run_logic(ctx: dict, logic_list: list) -> None:
    matched_any = False
    for entry in logic_list:
        if "if" in entry:
            if evaluate(entry["if"], ctx):
                apply_set(ctx, entry.get("set"))
                matched_any = True
        elif "else" in entry:
            if not matched_any:
                apply_set(ctx, entry.get("set"))


def _apply_set_collecting_add_service(ctx: dict, set_str: str, add_services: list) -> None:
    """Same 'field = value[; field = value]' language as apply_set(), except
    `add_service` assignments accumulate into add_services instead of
    overwriting a single ctx key (see module docstring)."""
    if not set_str:
        return
    remaining = []
    for stmt in set_str.split(";"):
        stmt = stmt.strip()
        if not stmt or "=" not in stmt:
            continue
        field, _, value = stmt.partition("=")
        field, value = field.strip(), value.strip()
        if field == "add_service":
            if value not in add_services:
                add_services.append(value)
        else:
            remaining.append(f"{field} = {value}")
    if remaining:
        apply_set(ctx, "; ".join(remaining))


def _run_derivations(ctx: dict, derivations: list, warnings_out: list, add_services: list) -> None:
    for d in derivations:
        top_when = d.get("when")
        if top_when and not evaluate(top_when, ctx):
            continue
        if "logic" in d:
            _run_logic(ctx, d["logic"])
        elif "set" in d:
            _apply_set_collecting_add_service(ctx, d["set"], add_services)
            if d.get("warn"):
                warnings_out.append(d["warn"])


def _tiebreak_applies(tb: dict, catalog: dict, tied_ids: list, ctx: dict) -> bool:
    """Two tiebreak_questions shapes coexist:
      - storage's: `when` is plain-English prose naming the tied pattern ids
        ("score tied between X and Y") — matched by substring, as the
        original storage-only code did.
      - appgw's: `when` is a real evaluable condition ("exposure == unsure")
        — evaluated normally. Wrapped in evaluate_safe so storage's prose
        (not valid condition syntax) fails closed instead of raising."""
    when = tb.get("when", "")
    if tied_ids:
        mentioned = {pid for pid in catalog if pid in when}
        if set(tied_ids) <= mentioned:
            return True
    return evaluate_safe(when, ctx)


def _select_pattern(service: str, ctx: dict, rules: dict) -> dict:
    ps = rules["pattern_selection"]
    for override in ps.get("overrides", []):
        if evaluate(override["when"], ctx):
            return {"outcome": "override", "winner": override["select"],
                    "override_id": override.get("id")}

    catalog = {pid: p for pid, p in get_catalog().items() if p.get("service") == service}
    result = pattern_matcher.score(catalog, ctx)

    if result["outcome"] in ("tie_zero", "no_match"):
        tied = result.get("tied", [])
        for tb in ps.get("tiebreak_questions", []):
            if _tiebreak_applies(tb, catalog, tied, ctx):
                return {"outcome": "ask_tiebreak", "winner": None,
                        "question": tb["ask"], "tied": tied}
        if ps.get("default"):
            return {"outcome": "matched", "winner": ps["default"], "candidates": result["candidates"]}
        no_match = ps.get("no_match")
        if no_match:
            return {"outcome": "no_match", "winner": None,
                    "message": no_match["message"], "action": no_match["action"]}
        return {"outcome": "no_match", "winner": None,
                "message": "Unable to match a standard pattern for this service — escalating "
                           "to the platform team rather than guessing.",
                "action": "ESCALATE_TO_PLATFORM_TEAM"}

    return result


def evaluate_blockers(service: str, answers: dict) -> dict:
    """Run only the blockers phase against whatever answers exist so far.
    Called after every single answer — see module docstring."""
    rules = get_rules(service)
    ctx = dict(answers)
    for blocker in rules["blockers"]:
        if evaluate(blocker["when"], ctx):
            return {"blocked": True, "blocker_id": blocker["id"],
                    "message": blocker["message"], "source": blocker.get("source")}
    return {"blocked": False}


def evaluate_full(service: str, answers: dict) -> dict:
    """Run every phase the active service's matrix declares, in its own
    execution_order. `answers` is never mutated; a working copy (ctx) picks
    up escalation overrides and derived values."""
    rules = get_rules(service)
    order = rules["execution_order"]
    ctx = dict(answers)

    blocker_result = evaluate_blockers(service, ctx)
    if blocker_result["blocked"]:
        return {
            "blocked": True, "blocker": blocker_result, "escalations": [],
            "constants": {}, "derived": {}, "selection": None, "deviations": [],
            "warnings": [], "add_services": [], "ctx": ctx,
        }

    escalations = []
    add_services = []
    if "escalations" in order:
        for esc in rules.get("escalations", []):
            if evaluate(esc["when"], ctx):
                # Some escalations (e.g. AppGW's infosec_onboarding) carry no
                # plain `message` at all — their entire text lives in the
                # message_ref composer file instead, rendered verbatim below.
                entry = {"id": esc["id"], "flag": esc.get("flag"), "message": esc.get("message", "")}
                if esc.get("message_ref"):
                    entry["message_ref"] = get_composer_file(esc["message_ref"]).get("user_message")
                if esc.get("redirect"):
                    entry["redirect"] = esc["redirect"]
                if esc.get("blocking_for"):
                    entry["blocking_for"] = esc["blocking_for"]
                if esc.get("blocking_note"):
                    entry["blocking_note"] = esc["blocking_note"]
                escalations.append(entry)
                if esc.get("override"):
                    apply_set(ctx, esc["override"])
                if esc.get("add_service") and esc["add_service"] not in add_services:
                    add_services.append(esc["add_service"])

    # constants: static Presight standards, passed through as-is (only
    # storage's matrix declares this phase — the per-pattern exception, e.g.
    # archive's LRS/GRS, is resolved once the pattern is known — see
    # resolve_constant()). Absent for the five new services.
    constants = rules.get("constants", {}) if "constants" in order else {}

    warnings = []
    if "derivations" in order:
        _run_derivations(ctx, rules.get("derivations", []), warnings, add_services)

    selection = None
    if "pattern_selection" in order:
        selection = _select_pattern(service, ctx, rules)
        if selection.get("winner"):
            ctx["selected_pattern"] = selection["winner"]

    deviations = []
    if "deviations" in order and selection and selection.get("winner"):
        for dev in rules.get("deviations", []):
            if evaluate(dev["when"], ctx):
                deviations.append(dev["state"])

    if "warnings" in order:
        for warn in rules.get("warnings", []):
            if evaluate(warn["when"], ctx):
                warnings.append(warn["message"])

    return {
        "blocked": False, "blocker": None, "escalations": escalations,
        "constants": constants, "derived": ctx, "selection": selection,
        "deviations": deviations, "warnings": warnings,
        "add_services": add_services, "ctx": ctx,
    }


def resolve_constant(service: str, name: str, selected_pattern_id: str):
    """A constant's value, honouring its `except` list for the selected
    pattern (e.g. replication is ZRS for everyone except
    storage_archive_retention). Only meaningful for storage today (the only
    service with a `constants` phase)."""
    rules = get_rules(service)
    spec = rules.get("constants", {}).get(name)
    if not spec:
        return None
    for exc in spec.get("except", []):
        if exc.get("pattern") == selected_pattern_id:
            return exc["value"]
    return spec.get("value")
