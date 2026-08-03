"""
Builds the ordered, prefilled request list for /api/advisor/environment/requests
from sequencer.py's wave output.

Scope decision: this does NOT re-run each embedded service's own full
rules_engine.evaluate_full + build_prefill_aks/vm/postgres/appgw pipeline.
Those pipelines need each service's OWN full question set answered (AKS's
tier/CMK/private-API questions, VM's OS/auth-mode questions, ...) — the
environment intake is deliberately SHORT (environment_questions.yaml's own
description: "Depth comes from the rules... not filling in a form") and
never asks most of them. Forcing a pattern selection from that much missing
data would mean either fabricating answers (never do this) or having
blockers/tiebreaks fire on fields that were never asked, which is worse
than being honest about what's actually known. Every wave item instead gets
the same uniform, always-safe prefill: business/governance tags plus the
handful of fields the environment intake genuinely collected — never a
per-service settings table it can't back up.
"""
import re

_TAG_FIELDS = ("business_unit", "application_name", "owner_email", "criticality", "environment",
               "data_classification")


def _common_fields(answers: dict) -> dict:
    return {k: answers.get(k) for k in _TAG_FIELDS if answers.get(k) not in (None, "")}


def _known_fields_for(raw_type: str, answers: dict) -> dict:
    fields = {}
    if raw_type == "aks_cluster":
        text = str(answers.get("aks_scale") or "")
        m = re.search(r"\d+", text)
        if m:
            fields["node_count"] = int(m.group(0))
        if answers.get("gpu_required"):
            fields["gpu_node_pool"] = True
    elif raw_type == "vm_create":
        if answers.get("vm_count"):
            fields["vm_count"] = answers["vm_count"]
        if answers.get("vm_purpose"):
            fields["workload_description"] = answers["vm_purpose"]
    elif raw_type == "postgres_create":
        if answers.get("database_criticality"):
            fields["ha_requirement"] = ("zone_redundant" if answers["database_criticality"]
                                         == "zone_redundant" else "single")
    elif raw_type == "app_gateway":
        if answers.get("public_details"):
            fields["public_hostname"] = answers["public_details"].split(",")[0].strip()
    elif raw_type == "storage_account_create":
        pass  # no storage-specific field the environment intake collects beyond the tag set
    return fields


def build_request_list(answers: dict, waves: list) -> list:
    """Flattens every wave's requests into one ordered list, each carrying
    {wave, label, submittable_request_type, secondary_note, prefill}."""
    common = _common_fields(answers)
    ordered = []
    for wave in waves:
        for req in wave["requests"]:
            prefill = dict(common)
            prefill.update(_known_fields_for(req["type"], answers))
            ordered.append({
                "wave": wave["wave"],
                "label": req["label"],
                "type": req["type"],
                "submittable_request_type": req["submittable_request_type"],
                "secondary_note": req["secondary_note"],
                "note": req.get("note"),
                "prefill": prefill,
            })
    return ordered
