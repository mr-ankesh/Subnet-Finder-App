"""
Deterministic renderer for environment_recommendation_template.md's shape.
This is the path that must never drift — every number here comes straight
from network_planner/composition_engine/sequencer's structured output, never
recomputed. An LLM narration pass (advisor/prompts.call_llm, using
prompts.get_environment_system_prompt()) may rewrite the opening summary
paragraph ONLY — the one place "explain, don't compute" is safe to apply —
with this renderer as the always-available fallback if the LLM is
unavailable or fails. Every table, every figure, the Pod CIDR paragraph, and
the InfoSec section are assembled here regardless of LLM availability.
"""
import logging

log = logging.getLogger(__name__)


def _env_label(answers: dict) -> str:
    env = (answers.get("environment") or "").lower()
    names = {"dev": "Development", "tst": "Test", "uat": "UAT", "prd": "Production",
             "snd": "Sandbox"}
    return f"{names.get(env, env.title() or 'Environment')} Environment"


def _fallback_summary(answers: dict, plan: dict, exposure: dict) -> str:
    n_subnets = len(plan["subnets"])
    if exposure.get("id") == "public_application":
        exposure_line = (
            "Everything runs privately; exactly one public IP exists, on the "
            "Application Gateway, and it accepts traffic only from Cloudflare."
        )
    else:
        exposure_line = "Everything stays private — no component has a public IP."
    return (
        f"A single spoke VNET peered to the Connectivity hub, carrying "
        f"{n_subnets} subnet{'s' if n_subnets != 1 else ''}. {exposure_line}"
    )


def render_summary(answers: dict, plan: dict, exposure: dict) -> str:
    """The one paragraph an LLM narration pass may rewrite; this is the
    deterministic fallback, and the only thing LLM narration is allowed to
    touch — never the tables or figures below it."""
    return _fallback_summary(answers, plan, exposure)


def render_components(components: dict) -> dict:
    inferred = components["inferred"]
    pending_ask = components.get("pending_ask")
    lines = None
    if inferred:
        lines = [{"added": c["id"], "why": c["tell_user"]} for c in inferred]
    return {
        "count": len(inferred),
        "rows": lines,
        "open_question": pending_ask["question"] if pending_ask else None,
    }


def render_network_plan(plan: dict) -> dict:
    """Never renders the Pod CIDR as a subnet row — it's returned as a
    separate `pod_cidr_paragraph` field. Arithmetic is a plain string built
    from plan's own terms, never re-derived."""
    terms = " + ".join(str(t) for t in plan["arithmetic_terms"])
    arithmetic_line = (
        f"{terms} = {plan['arithmetic_sum']} addresses, which fits a "
        f"{plan['vnet_size']} ({plan['capacity']}) at {plan['utilisation_pct']}% allocated, "
        f"leaving {plan['spare']} spare."
    )
    tight_fit_caveat = None
    if plan["flag_tripped"]:
        tight_fit_caveat = (
            f"This is a tight fit at {plan['utilisation_pct']}% allocated. Consider the next "
            "size up — subnets cannot be resized after deployment."
        )
    pod_paragraph = None
    if plan.get("pod_cidr"):
        pod_paragraph = plan["pod_cidr"]["note"]
        if plan.get("aks_private_zone_note"):
            pod_paragraph += " " + plan["aks_private_zone_note"]
    return {
        "vnet_count": plan["vnet_count"],
        "vnet_size": plan["vnet_size"],
        "subnet_rows": [
            {"name": s["id"].replace("snet_", "snet-"), "purpose": s["purpose"],
             "size": s["size"], "usable": s["usable"], "basis": s["basis"]}
            for s in plan["subnets"]
        ],
        "arithmetic_line": arithmetic_line,
        "tight_fit_caveat": tight_fit_caveat,
        "pod_cidr_paragraph": pod_paragraph,
    }


def render_private_connectivity(plan: dict) -> dict:
    return {
        "count": len(plan["private_endpoints"]),
        "rows": plan["private_endpoints"],
        "extra_zone_note": plan.get("aks_private_zone_note"),
    }


def render_public_access(exposure: dict) -> dict:
    """Verbatim from infosec_gate.yaml's user_message — never LLM-touched."""
    if exposure.get("id") != "public_application":
        return None
    ref = exposure["message_ref"]["user_message"]
    return {"heading": ref["heading"], "body": ref["body"].strip(),
            "next_step": ref["next_step"].strip(), "public_ip_count": 1,
            "public_ip_location": "Application Gateway"}


def render_hub_integration(mandatory_spoke_wiring: list) -> list:
    """Normalizes to {step, detail} only. One entry in network_sizing.yaml's
    mandatory_spoke_wiring ("Hub-spoke peering") has an unquoted YAML flow
    value ("note: Both directions, correct gateway transit settings") whose
    trailing comma splits it into a stray bare key with a null value under
    YAML's flow-mapping parsing — a pre-existing authoring issue in the KB
    file itself. Only pulling the known `step`/`detail`/`note` keys here
    means that stray key is silently dropped rather than leaking a
    null-valued field into the rendered output."""
    out = []
    for item in mandatory_spoke_wiring:
        out.append({"step": item.get("step"), "detail": item.get("detail") or item.get("note")})
    return out


def render_build_sequence(waves: list, critical_path: str, parallelism_message: str) -> dict:
    rows = []
    for w in waves:
        labels = " · ".join(r["label"] for r in w["requests"])
        rows.append({"wave": w["wave"], "name": w["name"], "requests": labels,
                      "note": w.get("blocking_for") or ""})
    return {"parallelism_message": parallelism_message, "rows": rows,
            "critical_path": critical_path}


_SECURITY_POSTURE_BULLETS = [
    "Public network access disabled on every PaaS service",
    "Private endpoints and private DNS integration throughout",
    "CMK encryption via Key Vault Premium, RSA-HSM key, user-assigned managed identity",
    "TLS 1.2 minimum everywhere",
    "No public IP on any backend resource",
    "WAF in Prevention mode at go-live, OWASP Core Rule Set",
    "NSGs on every subnet; default route via the hub firewall",
    "Resource locks on Key Vault and Storage",
    "Diagnostics to the Operational LAW; security logs to the Security LAW and Sentinel",
]


def render_security_posture() -> list:
    return list(_SECURITY_POSTURE_BULLETS)


def render_before_you_start(answers: dict, components: dict, warnings: list, exposure: dict) -> list:
    items = [{"done": bool(answers.get("subscription_available")), "text": "Azure subscription"}]
    warning_ids = {w["id"] for w in warnings}
    if "aks_surge_headroom" in warning_ids:
        items.append({"done": False, "text": "Confirm expected AKS node count and the "
                      "cluster's max-surge setting, so the subnet is sized right first time"})
    if "pod_cidr_non_overlap" in warning_ids:
        items.append({"done": False, "text": "Confirm the Pod CIDR with TechOps alongside "
                      "the VNET allocation"})
    if exposure.get("id") == "public_application":
        items.append({"done": False, "text": "Decide the public hostname and confirm who "
                      "owns the domain"})
        items.append({"done": False, "text": "TLS certificate for that hostname, to be "
                      "stored in Key Vault"})
        items.append({"done": False, "text": "Name a technical owner for the public "
                      "endpoint — InfoSec will need one"})
    if components.get("pending_ask"):
        items.append({"done": False, "text": components["pending_ask"]["question"]})
    return items


def render_prefill_summary(answers: dict) -> dict:
    prefilled = ["Business unit", "Environment", "Application name", "Owner", "Criticality",
                 "Data classification", "Resource counts"]
    need_to_add = ["Subscription ID", "Resource group names", "Specific VM sizes and OS images"]
    return {"prefilled": prefilled, "need_to_add": need_to_add}


def render_full(answers: dict, composition: dict, waves: list, critical_path: str,
                 parallelism_message: str, infosec_brief: dict = None) -> dict:
    """Everything the frontend/template needs, fully structured — never a
    single opaque markdown blob, so the UI can render the subnet table,
    arithmetic block, Pod CIDR paragraph, and wave table as distinct pieces
    per the spec, and so verification can assert on structure directly."""
    plan = composition["network_plan"]
    exposure = composition["exposure"]
    return {
        "environment_label": _env_label(answers),
        "summary": render_summary(answers, plan, exposure),
        "components": render_components(composition["components"]),
        "network_plan": render_network_plan(plan),
        "hub_integration": render_hub_integration(plan["mandatory_spoke_wiring"]),
        "private_connectivity": render_private_connectivity(plan),
        "public_access": render_public_access(exposure),
        "build_sequence": render_build_sequence(waves, critical_path, parallelism_message),
        "security_posture": render_security_posture(),
        "before_you_start": render_before_you_start(
            answers, composition["components"], composition["warnings"], exposure),
        "prefill_summary": render_prefill_summary(answers),
        "deviations": composition["deviations"],
        "warnings": composition["warnings"],
        "infosec_brief": infosec_brief,
    }
