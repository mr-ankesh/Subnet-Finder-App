"""
The InfoSec public-exposure gate: detection, verbatim message rendering, and
a best-effort auto-drafted brief. Same discipline as Phase 2's AppGW gate —
`user_message` is rendered VERBATIM, never passed through the LLM, so the
guardrails ("never suggest a workaround", "never promise a timeline") are
structural, not a matter of prompt wording.
"""
import functools

from advisor.catalog_loader import get_composer_file

_MISSING = "(not yet provided — confirm with the requester)"


@functools.lru_cache(maxsize=1)
def _gate() -> dict:
    return get_composer_file("infosec_gate.yaml")


def gate_fires(answers: dict) -> bool:
    return answers.get("exposure") == "public_internet"


def get_message_ref() -> dict:
    """heading/body/next_step, rendered verbatim — never LLM-touched."""
    return _gate()["user_message"]


def _backend_description(answers: dict) -> str:
    parts = []
    if int(answers.get("aks_count") or 0) > 0:
        parts.append("your AKS cluster")
    if int(answers.get("vm_count") or 0) > 0:
        parts.append("VM(s)")
    if int(answers.get("postgres_count") or 0) > 0:
        parts.append("PostgreSQL")
    if int(answers.get("storage_count") or 0) > 0:
        parts.append("Storage")
    return ", ".join(parts) if parts else "the backend"


def _split_public_details(answers: dict) -> dict:
    """environment_questions.yaml asks ONE combined free-text field
    (`public_details`: "What hostname will it be published on, and who's
    the audience?"), but infosec_gate.yaml's brief template expects
    `public_hostname` and `audience` as separate fields — a genuine
    KB-vs-question-bank mismatch, same class as the field-name mismatches
    found in the six-service build. Split on the first comma as a
    best-effort recovery (matching the KB's own example phrasing,
    "app.presight.ai, public customers"); never invented if there's
    nothing to split."""
    if answers.get("public_hostname") or answers.get("audience"):
        return answers
    details = answers.get("public_details")
    if not details:
        return answers
    out = dict(answers)
    if "," in details:
        hostname, audience = details.split(",", 1)
        out["public_hostname"] = hostname.strip()
        out["audience"] = audience.strip()
    else:
        out["public_hostname"] = details.strip()
    return out


def _field_value(answers: dict, field_spec: dict) -> str:
    if "value" in field_spec:
        return field_spec["value"]
    v = answers.get(field_spec.get("from"))
    return str(v) if v not in (None, "") else _MISSING


def draft_brief(answers: dict) -> dict:
    """Fills infosec_brief_template's sections from the environment intake's
    answers. Any field the intake never actually asks (pii_present,
    auth_model, authz_model, traffic_estimate, business_owner, purpose,
    audience — the environment questions ask `public_details` as one free
    -text field, not these individually) renders as an explicit
    "not yet provided" placeholder — never invented, never silently
    dropped, so the brief stays honest about what still needs a human."""
    answers = _split_public_details(answers)
    template = _gate()["infosec_brief_template"]
    app_name = answers.get("application_name") or _MISSING
    sections = []
    for section in template["sections"]:
        entry = {"heading": section["heading"]}
        if "fields" in section:
            entry["fields"] = [
                {"label": f["label"], "value": _field_value(answers, f)}
                for f in section["fields"]
            ]
        if "content" in section:
            entry["content"] = section["content"].replace(
                "{backend_description}", _backend_description(answers))
        if "checklist" in section:
            entry["checklist"] = list(section["checklist"])
        if "attach" in section:
            entry["attach"] = section["attach"]
        sections.append(entry)

    return {
        "title": template["title"].replace("{application_name}", app_name),
        "sections": sections,
    }


def tone_rules() -> list:
    return _gate()["tone_rules"]
