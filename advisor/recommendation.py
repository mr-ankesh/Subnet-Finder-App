"""
Assembles the final recommendation per
advisor_kb/templates/recommendation_template.md's fixed section order.

Almost the entire template is deterministic — the table, the requests list,
prerequisites, prefilled/still-needed fields, deviations and warnings are all
built directly from rule_result/pattern/prefill data, with nothing left for
the LLM to decide. Only "### Why this pattern" (2-4 sentences) is genuinely
LLM-authored prose (per the explanation-stage system prompt's own scope:
"fill the template's prose sections"). If the LLM is unavailable or fails,
a deterministic fallback sentence is used instead — this feature must not
break just because no LLM provider is configured (same "AI enhances, never
gates" convention as notifications.py's AI-drafted email subject/body).
"""
import logging

from advisor import prompts
from advisor.rules_engine import resolve_constant

log = logging.getLogger(__name__)


def _replication_reason(pattern_id: str) -> str:
    if pattern_id == "storage_archive_retention":
        return "Archive access tier supports LRS/GRS only — documented deviation from the ZRS baseline"
    return "Presight standard for all storage"


def _fallback_why(pattern: dict, answers: dict) -> str:
    purpose = (answers.get("purpose") or "").replace("_", " ")
    consumer = ", ".join(c.replace("_", " ") for c in (answers.get("consumer") or [])) or "the requesting workload"
    return (f"Based on what you described ({purpose or 'your storage need'}, accessed by {consumer}), "
            f"{pattern['name']} is the closest match in the Presight-approved catalog: {pattern['summary'].strip()}")


def _fallback_why_generic(pattern: dict, answers: dict) -> str:
    """Same spirit as _fallback_why, generalized for AKS/VM/Postgres/AppGW —
    each service's own question bank uses a different field name for "what
    is this for" (workload_description/vm_purpose/purpose), so this doesn't
    assume storage's purpose/consumer vocabulary."""
    descriptor = (answers.get("purpose") or answers.get("workload_description")
                  or answers.get("vm_purpose") or "").replace("_", " ")
    return (f"Based on what you described ({descriptor or 'your requirement'}), {pattern['name']} is the "
            f"closest match in the Presight-approved catalog: {pattern['summary'].strip()}")


def _why_this_pattern(pattern: dict, answers: dict, rule_result: dict, fallback_fn=_fallback_why) -> str:
    try:
        prompt_text = prompts.get_system_prompts()["explanation"]
        user_content = (
            f"Selected pattern: {pattern['name']} ({pattern['id']})\n"
            f"Pattern summary: {pattern['summary']}\n"
            f"User's answers: {answers}\n"
            f"Escalations: {rule_result.get('escalations')}\n"
            f"Deviations: {rule_result.get('deviations')}\n\n"
            "Write ONLY the 2-4 sentence 'Why this pattern' explanation — "
            "reference the user's own purpose/consumer/access answers explicitly. "
            "No headers, no other sections."
        )
        text = prompts.call_llm(prompt_text, user_content).strip()
        return text or fallback_fn(pattern, answers)
    except Exception as exc:
        log.warning("advisor: explanation-stage LLM call failed, using fallback: %s", exc)
        return fallback_fn(pattern, answers)


def build_recommendation(pattern: dict, answers: dict, rule_result: dict, prefill_payload: dict) -> dict:
    design = pattern["design"]
    derived = rule_result["derived"]
    replication = resolve_constant("storage_account", "replication", pattern["id"])

    data_protection = design.get("data_protection", {})
    dp_bits = []
    if data_protection.get("blob_soft_delete") or data_protection.get("share_soft_delete"):
        dp_bits.append("soft delete")
    if data_protection.get("versioning") == "enabled":
        dp_bits.append("versioning")
    if data_protection.get("change_feed") == "enabled":
        dp_bits.append("change feed")

    backup = design.get("backup", {})

    rows = [
        {"setting": "Account type", "value": design.get("storage_kind", "StorageV2"), "why": "Presight standard"},
        {"setting": "Performance", "value": design.get("performance_tier", "Standard"), "why": "Presight standard"},
        {"setting": "Replication", "value": replication, "why": _replication_reason(pattern["id"])},
        {"setting": "Access tier", "value": derived.get("access_tier", "Hot"),
         "why": "Based on how often you'll read the data"},
        {"setting": "Network access", "value": "Private endpoint only", "why": "Required by Azure Policy"},
        {"setting": "DNS", "value": derived.get("private_dns_zone", ""),
         "why": "Links the private endpoint to your VNET"},
        {"setting": "Encryption", "value": "Customer-managed key (RSA-HSM)",
         "why": "Presight encrypts all data with CMK"},
        {"setting": "Transport", "value": "TLS 1.2, HTTPS only", "why": "Presight standard"},
        {"setting": "Data protection", "value": ", ".join(dp_bits) or "soft delete", "why": "Recovery from accidental deletion"},
        {"setting": "Backup", "value": backup.get("vault_type", "—"),
         "why": backup.get("note", "Platform baseline")},
        {"setting": "Monitoring", "value": "Defender for Storage + Log Analytics", "why": "Platform baseline"},
    ]

    requests = [{"label": r["label"], "note": r.get("note", "")} for r in pattern["required_requests"]
                if not r.get("condition") or (r.get("condition") == "consumer includes end_user_zpa"
                                               and derived.get("zpa_routing_required"))]

    fields = prefill_payload["fields"]
    prefilled_list = [f"{k.replace('_', ' ').title()}: {v}" for k, v in fields.items()
                       if k != "business_justification" and v not in (None, "", False)]

    return {
        "pattern_id": pattern["id"],
        "pattern_name": pattern["name"],
        "pattern_summary": pattern["summary"].strip(),
        "why_this_pattern": _why_this_pattern(pattern, answers, rule_result),
        "table_rows": rows,
        "requests": requests,
        "prerequisites": pattern.get("prerequisites", []),
        "prefilled": prefilled_list,
        "user_must_provide": prefill_payload["user_must_provide"],
        "deviations": rule_result.get("deviations", []),
        "warnings": rule_result.get("warnings", []),
        "escalations": rule_result.get("escalations", []),
        "request_type": prefill_payload.get("request_type"),
        "cost_band": pattern.get("cost_band", "$"),
        "kb_version": pattern.get("kb_version", "1.0.0"),
    }


def build_blocked_response(blocker: dict, answers: dict) -> dict:
    return {
        "blocked": True,
        "message": blocker["message"],
        "blocker_id": blocker["blocker_id"],
        "captured_so_far": {k: v for k, v in answers.items() if v not in (None, "", [])},
    }


def _requests_list_generic(pattern: dict) -> list:
    """Generic (service-agnostic) version of build_recommendation()'s
    requests-list builder above. New services' required_requests `condition`
    fields are genuine plain-English prose across the whole KB (confirmed:
    none of them are valid condition-language, e.g. "engineers need kubectl
    access") — rather than guessing true/false and silently hiding a
    required Azure request, every item is always shown, with its condition
    surfaced as a visible caveat instead of used as a filter."""
    out = []
    for r in pattern.get("required_requests", []):
        entry = {"label": r["label"], "note": r.get("note", "")}
        if r.get("condition"):
            entry["condition_note"] = f"Applies when: {r['condition']}"
        out.append(entry)
    return out


def build_recommendation_generic(pattern: dict, answers: dict, rule_result: dict,
                                  prefill_payload: dict) -> dict:
    """Same shape and spirit as build_recommendation() (storage), generalized
    for AKS/VM/Postgres/AppGW. Their catalog patterns' `design` dicts are too
    heterogeneous (nested lists/dicts — see e.g. appgw's `layers` list vs
    aks's `node_pools` dict) to force into storage's flat settings table, so
    `security_floor` (flat scalars in every pattern) becomes the settings
    table instead, and `design` is passed through as-is for the frontend to
    render generically — every value still comes straight from the KB
    pattern's own YAML, nothing invented here.

    Escalations already carry any message_ref content verbatim (loaded once
    by rules_engine, never touched by the LLM) and are passed straight
    through unchanged, matching the guardrail that the InfoSec gate (and any
    future message_ref) is rendered exactly as authored."""
    security_floor = pattern.get("security_floor", {})
    rows = [{"setting": k.replace("_", " ").title(), "value": v, "why": "Presight standard"}
            for k, v in security_floor.items()]

    fields = prefill_payload["fields"]
    prefilled_list = [f"{k.replace('_', ' ').title()}: {v}" for k, v in fields.items()
                       if k != "business_justification" and v not in (None, "", False)]

    return {
        "pattern_id": pattern["id"],
        "pattern_name": pattern["name"],
        "pattern_summary": pattern["summary"].strip(),
        "why_this_pattern": _why_this_pattern(pattern, answers, rule_result, fallback_fn=_fallback_why_generic),
        "table_rows": rows,
        "design": pattern.get("design", {}),
        "requests": _requests_list_generic(pattern),
        "prerequisites": pattern.get("prerequisites", []),
        "prefilled": prefilled_list,
        "user_must_provide": prefill_payload["user_must_provide"],
        "deviations": rule_result.get("deviations", []),
        "warnings": rule_result.get("warnings", []),
        "escalations": rule_result.get("escalations", []),
        "add_services": rule_result.get("add_services", []),
        "request_type": prefill_payload.get("request_type"),
        "cost_band": pattern.get("cost_band", "$"),
        "kb_version": pattern.get("kb_version", "2.0.0"),
    }


def build_redirect_response(rule_result: dict, catalog: dict, answers: dict) -> dict:
    """A `redirect` escalation (currently only Postgres's self_managed ->
    vm_workload_standard) redirects the WHOLE recommendation to a different
    service's pattern. Recommendation-only: renders the target pattern's own
    summary so the user understands what's being suggested, then asks them
    to restart the flow on that service for a real guided intake — never
    fabricates a cross-service prefill from answers shaped for a different
    service (the same "never invent" restraint as the curated-VM-image gap)."""
    redirect_esc = next(e for e in rule_result.get("escalations", []) if e.get("redirect"))
    target_id = redirect_esc["redirect"]
    target = catalog.get(target_id, {})
    return {
        "redirect": True,
        "target_pattern_id": target_id,
        "target_pattern_name": target.get("name", target_id),
        "target_pattern_summary": (target.get("summary") or "").strip(),
        "message": redirect_esc["message"],
        "restart_hint": "Start a new conversation and choose the matching service from the menu "
                        "for a full guided intake targeting this pattern.",
        "captured_so_far": {k: v for k, v in answers.items() if v not in (None, "", [])},
    }
