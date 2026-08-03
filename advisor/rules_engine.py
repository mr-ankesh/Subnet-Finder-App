"""
Executes advisor_kb/rules/storage_decision_matrix.yaml's seven phases, in the
declared execution_order, against the answers captured so far. This runs
BEFORE the LLM and its output is authoritative — the LLM may only narrate
what this module decided (see advisor_kb/README.md's non-negotiable).

Blockers/escalations are safe to re-run after every single answer (not just
once at the end): a rule referencing a field that hasn't been answered yet
simply doesn't fire, so calling evaluate_partial() incrementally is how
"answering 'no subscription' halts immediately" (rather than after the whole
questionnaire) is implemented, without a second parallel halting mechanism.
"""
import logging

from advisor.catalog_loader import get_catalog, get_rules
from advisor.condition_eval import evaluate, apply_set
from advisor import pattern_matcher

log = logging.getLogger(__name__)

# Free-text phrases that mean "give me a public endpoint" — the KB's own
# question bank deliberately never asks about public access directly
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


def _run_derivations(ctx: dict, derivations: list, warnings_out: list) -> None:
    for d in derivations:
        top_when = d.get("when")
        if top_when and not evaluate(top_when, ctx):
            continue
        if "logic" in d:
            _run_logic(ctx, d["logic"])
        elif "set" in d:
            apply_set(ctx, d["set"])
            if d.get("warn"):
                warnings_out.append(d["warn"])


def _select_pattern(ctx: dict, rules: dict) -> dict:
    ps = rules["pattern_selection"]
    for override in ps.get("overrides", []):
        if evaluate(override["when"], ctx):
            return {"outcome": "override", "winner": override["select"],
                    "override_id": override["id"]}

    catalog = get_catalog()
    result = pattern_matcher.score(catalog, ctx)

    if result["outcome"] == "tie_zero":
        for tb in ps.get("tiebreak_questions", []):
            tied_set = set(result["tied"])
            mentioned = {pid for pid in catalog if pid in tb["when"]}
            if tied_set and tied_set <= mentioned:
                return {"outcome": "ask_tiebreak", "winner": None,
                        "question": tb["ask"], "tied": result["tied"]}
        # No tiebreak question covers this exact tie -> don't guess, escalate.
        result = {"outcome": "no_match", "winner": None, "candidates": result["candidates"]}

    if result["outcome"] == "no_match":
        no_match = ps["no_match"]
        return {"outcome": "no_match", "winner": None,
                "message": no_match["message"], "action": no_match["action"]}

    return result


def evaluate_blockers(answers: dict) -> dict:
    """Run only the blockers phase against whatever answers exist so far.
    Called after every single answer — see module docstring."""
    rules = get_rules()
    ctx = dict(answers)
    for blocker in rules["blockers"]:
        if evaluate(blocker["when"], ctx):
            return {"blocked": True, "blocker_id": blocker["id"],
                    "message": blocker["message"], "source": blocker.get("source")}
    return {"blocked": False}


def evaluate_full(answers: dict) -> dict:
    """Run all seven phases in execution_order. `answers` is never mutated;
    a working copy (ctx) picks up escalation overrides and derived values."""
    rules = get_rules()
    ctx = dict(answers)

    blocker_result = evaluate_blockers(ctx)
    if blocker_result["blocked"]:
        return {
            "blocked": True, "blocker": blocker_result, "escalations": [],
            "derived": {}, "selection": None, "deviations": [], "warnings": [],
            "ctx": ctx,
        }

    escalations = []
    for esc in rules["escalations"]:
        if evaluate(esc["when"], ctx):
            escalations.append({"id": esc["id"], "flag": esc["flag"], "message": esc["message"]})
            if esc.get("override"):
                apply_set(ctx, esc["override"])

    # constants: static Presight standards, passed through as-is (the actual
    # per-pattern exception, e.g. archive's LRS/GRS, is resolved once the
    # pattern is known — see resolve_constant()).
    constants = rules["constants"]

    warnings = []
    _run_derivations(ctx, rules["derivations"], warnings)

    selection = _select_pattern(ctx, rules)
    if selection["winner"]:
        ctx["selected_pattern"] = selection["winner"]

    deviations = []
    if selection["winner"]:
        for dev in rules["deviations"]:
            if evaluate(dev["when"], ctx):
                deviations.append(dev["state"])

    for warn in rules["warnings"]:
        if evaluate(warn["when"], ctx):
            warnings.append(warn["message"])

    return {
        "blocked": False, "blocker": None, "escalations": escalations,
        "constants": constants, "derived": ctx, "selection": selection,
        "deviations": deviations, "warnings": warnings, "ctx": ctx,
    }


def resolve_constant(name: str, selected_pattern_id: str):
    """A constant's value, honouring its `except` list for the selected
    pattern (e.g. replication is ZRS for everyone except storage_archive_retention)."""
    rules = get_rules()
    spec = rules["constants"].get(name)
    if not spec:
        return None
    for exc in spec.get("except", []):
        if exc.get("pattern") == selected_pattern_id:
            return exc["value"]
    return spec.get("value")
