"""
Runs advisor_kb/composer/composition_rules.yaml's 8-phase pipeline. Unlike
rules_engine.py (which SELECTS one catalog pattern per service), this
engine COMPOSES cross-service outcomes for a whole environment: which
components are silently added vs. asked about, the exposure model, the
network plan (delegated to network_planner.py), deviations and warnings.
There is no "pattern_selection" phase here — the environment IS the
composition, not a single chosen pattern.
"""
import functools
import re

from advisor.catalog_loader import get_composer_file
from advisor.condition_eval import evaluate_safe, AttrDict
from advisor.composer import network_planner


@functools.lru_cache(maxsize=1)
def _rules() -> dict:
    return get_composer_file("composition_rules.yaml")


def evaluate_environment_blockers(answers: dict) -> dict:
    """Re-run after every answer, same incremental-check discipline as
    rules_engine.evaluate_blockers. Returns {"blocked": bool, "message":
    str|None, "blocker_id": str|None}."""
    ns = AttrDict(answers)
    for rule in _rules()["environment_blockers"]:
        if evaluate_safe(rule["when"], ns):
            return {"blocked": True, "message": rule["message"].strip(),
                    "blocker_id": rule["id"]}
    return {"blocked": False, "message": None, "blocker_id": None}


def _resolve_aks_node_count(answers: dict) -> int:
    if answers.get("_aks_node_count") is not None:
        return int(answers["_aks_node_count"])
    m = re.search(r"\d+", str(answers.get("aks_scale") or ""))
    return int(m.group(0)) if m else 0


def infer_missing_components(answers: dict) -> dict:
    """Splits infer_missing_components rules into:
    - `inferred`: silent `add:` results — rendered in the "Components"
      table, each carrying {id, reason, tell_user}.
    - `pending_ask`: the FIRST unresolved `ask:` rule (never more than one
      at a time, per "ask rather than assume, once") — {id, question,
      reason}, or None once every ask-rule is resolved.
    - `defaults`: silent `default:` results (e.g. bastion_or_zpa -> ZPA) —
      informational, never surfaced as a question or an "added" line.

    An `ask:` rule is resolved once its id appears in
    `answers["_resolved_asks"]` — set by the caller (intake.py) the moment
    the user answers either way. This is deliberately NOT inferred from the
    target field's value (e.g. storage_count == 0): a "no" answer to
    storage_for_aks leaves storage_count at 0, identical to "never asked",
    so only an explicit resolved-marker distinguishes "asked and declined"
    from "not yet asked" — without it the same question would be asked on
    every turn.
    """
    ns = AttrDict(answers)
    resolved = set(answers.get("_resolved_asks") or [])
    inferred, defaults = [], []
    pending_ask = None
    for rule in _rules()["infer_missing_components"]:
        if rule["id"] == "pe_subnet":
            # "any PaaS service present" isn't valid condition-language, but
            # more importantly: network_planner already adds snet_pe to the
            # subnet table whenever a PaaS service is present (its own
            # any_paas check), and worked_example.md's Components table lists
            # exactly 3 added items — Key Vault, ACR, App Gateway — never a
            # "private endpoint subnet" row. A subnet isn't a service the
            # user would recognize as new work the way a whole Key Vault is,
            # so this rule is deliberately never surfaced as an "inferred
            # component" here; the subnet itself still shows up in the
            # network plan.
            continue
        if not evaluate_safe(rule["when"], ns):
            continue
        if "add" in rule:
            inferred.append({"id": rule["add"], "reason": rule["reason"].strip(),
                              "tell_user": rule.get("tell_user", rule["reason"]).strip()})
        elif "ask" in rule:
            if rule["id"] in resolved:
                continue
            if pending_ask is None:
                pending_ask = {"id": rule["id"], "question": rule["ask"].strip(),
                                "reason": rule["reason"].strip()}
        elif "default" in rule:
            defaults.append({"id": rule["id"], "value": rule["default"],
                              "reason": rule["reason"].strip()})
    return {"inferred": inferred, "pending_ask": pending_ask, "defaults": defaults}


def exposure_analysis(answers: dict) -> dict:
    """Only `fully_private`/`public_application` are reachable from the
    current single `exposure` question (internal_only/public_internet/
    unsure) — `mixed_exposure` needs per-component exposure the intake
    doesn't collect, so it's deliberately left unreachable here rather than
    fabricating a trigger condition for it."""
    for outcome in _rules()["exposure_analysis"]:
        if outcome["id"] == "mixed_exposure":
            continue
        if evaluate_safe(outcome["when"], AttrDict(answers)):
            result = {"id": outcome["id"], "outcome": outcome["outcome"],
                      "message": outcome.get("message", "").strip()}
            if "message_ref" in outcome:
                result["message_ref"] = get_composer_file(outcome["message_ref"])
            if "enforce" in outcome:
                result["enforce"] = outcome["enforce"]
            return result
    return {"id": None, "outcome": None, "message": None}  # exposure unanswered/"unsure"


def _vnet_prefix_number(prefix: str) -> int:
    return int(str(prefix).lstrip("/"))


def environment_deviations(answers: dict, network_plan: dict) -> list:
    """`oversized_request`'s condition ("computed_vnet_size larger than
    /21") isn't valid condition-language — a SMALLER prefix number means a
    LARGER address space, which condition_eval has no notion of — so it's
    checked with an explicit Python comparison, not forced through
    evaluate(). Same treatment already given to other unparseable prose
    conditions elsewhere in this KB."""
    ns = dict(answers)
    ns["ha_requested"] = answers.get("database_criticality") == "zone_redundant"
    out = []
    for rule in _rules()["environment_deviations"]:
        if rule["id"] == "oversized_request":
            if _vnet_prefix_number(network_plan["vnet_size"]) < 21:
                out.append({"id": rule["id"], "state": rule["state"].strip()})
            continue
        if evaluate_safe(rule["when"], AttrDict(ns)):
            out.append({"id": rule["id"], "state": rule["state"].strip()})
    return out


def environment_warnings(answers: dict) -> list:
    ns = AttrDict(answers)
    return [{"id": rule["id"], "message": rule["message"].strip()}
            for rule in _rules()["environment_warnings"] if evaluate_safe(rule["when"], ns)]


def shared_services() -> dict:
    return _rules()["shared_services"]


def dependency_graph() -> dict:
    return _rules()["dependency_graph"]


def evaluate_full(answers: dict) -> dict:
    """Runs the whole composition_rules.yaml execution_order. `answers`
    must be fully resolved (intake complete, no pending blockers/asks) —
    intake.py is responsible for not calling this until that's true.
    network_planner.build_network_plan does the actual arithmetic for the
    network_plan phase; everything else here is cross-service rule
    evaluation."""
    blockers = evaluate_environment_blockers(answers)
    if blockers["blocked"]:
        return {"blocked": True, "blocker": blockers}

    components = infer_missing_components(answers)
    inferred_ids = {c["id"] for c in components["inferred"]}

    exposure = exposure_analysis(answers)

    # Warnings/deviations key off *_count fields (e.g. cert_ownership's
    # "appgw_count > 0"), but an inferred Application Gateway
    # (appgw_public_cloudflare, added because exposure is public — the user
    # never asked for one directly) must still count as "present" for those
    # checks. network_planner gets this same effective presence via its own
    # inferred_ids parameter; this mirrors it for the rules that run here.
    effective = dict(answers)
    if "appgw_public_cloudflare" in inferred_ids:
        effective["appgw_count"] = max(int(answers.get("appgw_count") or 0), 1)

    plan_answers = dict(answers)
    plan_answers["_aks_node_count"] = _resolve_aks_node_count(answers)
    network_plan = network_planner.build_network_plan(plan_answers, inferred_ids)
    network_plan["mandatory_spoke_wiring"] = network_planner.mandatory_spoke_wiring()

    deviations = environment_deviations(effective, network_plan)
    warnings = environment_warnings(effective)

    return {
        "blocked": False,
        "components": components,
        "exposure": exposure,
        "network_plan": network_plan,
        "dependency_graph": dependency_graph(),
        "shared_services": shared_services(),
        "deviations": deviations,
        "warnings": warnings,
    }
