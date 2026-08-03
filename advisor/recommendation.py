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


def _why_this_pattern(pattern: dict, answers: dict, rule_result: dict) -> str:
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
        return text or _fallback_why(pattern, answers)
    except Exception as exc:
        log.warning("advisor: explanation-stage LLM call failed, using fallback: %s", exc)
        return _fallback_why(pattern, answers)


def build_recommendation(pattern: dict, answers: dict, rule_result: dict, prefill_payload: dict) -> dict:
    design = pattern["design"]
    derived = rule_result["derived"]
    replication = resolve_constant("replication", pattern["id"])

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
