"""
Grounded free-form answering for the advisor's persistent chat. Every
answer is assembled from KB content in a fixed lookup order — glossary.yaml
first, then the active service's catalog pattern/rules, then
platform_constants.yaml, then (environment mode only) advisor_kb/composer/
— and every statement is labelled one of:

    presight_standard   — from the KB
    general_azure        — explanation, not policy
    outside_scope         — not covered; never inferred from general Azure norms

The LLM (advisor/prompts.call_llm) may only REPHRASE already-retrieved KB
text for the reader's level. It never originates a Presight-specific claim
that isn't already in the retrieved fields, and it never touches a CIDR,
subnet size, address count or percentage — those are always inserted
verbatim from the caller's already-computed network_plan, never
re-derived (network_planner.py remains the sole arithmetic source, same
rule as the environment composer itself).

If nothing in the KB matches, the answer is `outside_scope` and is NOT
passed through the LLM at all — the safest way to guarantee "never invent
a Presight practice not in the KB" is to simply not ask a model to
free-associate when there is no grounding to constrain it.
"""
import logging
import re

from advisor import glossary
from advisor.catalog_loader import get_catalog, get_platform_constants, get_composer_file
from advisor import prompts

log = logging.getLogger(__name__)

_NUMERIC_QUESTION_RE = re.compile(
    r"\b(cidr|subnet size|address count|percent|percentage|utilisation|utilization|"
    r"how big|how many addresses)\b|/\d{1,2}\b", re.IGNORECASE)

_SUBNET_NOTE_FIELDS = ("formula_note", "sizing_note", "note", "recommended_default_reason")


def _narrate(base_text: str, hard_facts: list) -> str:
    """Best-effort LLM rephrase of already-retrieved KB text, with a hard
    fallback to the verbatim text if the LLM is unavailable or fails —
    same discipline as every other advisor narration step. `hard_facts` are
    quoted back into the prompt as constraints the model must not
    contradict or add to."""
    try:
        system = (
            "You explain an already-retrieved fact to a reader who asked a plain-English "
            "question. Rephrase for clarity only. Do not add any claim, number, or policy "
            "statement that isn't in the text given to you. Do not contradict it. Plain "
            "English, no more than a short paragraph."
        )
        facts = "\n".join(f"- {f}" for f in hard_facts)
        user = f"Facts (must not be altered or contradicted):\n{facts}\n\nText to explain:\n{base_text}"
        narrated = prompts.call_llm(system, user).strip()
        return narrated or base_text
    except Exception as exc:
        log.warning("advisor freeform: LLM narration failed, using verbatim KB text: %s", exc)
        return base_text


def _from_glossary(term: dict) -> dict:
    parts = [term["plain_english"].strip()]
    at_presight = term.get("at_presight", "").strip()
    if at_presight:
        parts.append(at_presight)
    why = term.get("why_it_matters", "").strip()
    if why:
        parts.append(why)
    base_text = "\n\n".join(parts)
    hard_facts = [f"at_presight: {at_presight}"] if at_presight else []
    text = _narrate(base_text, hard_facts) if hard_facts else base_text
    return {
        "text": text,
        "labels": ["presight_standard"] if at_presight else ["general_azure"],
        "related_terms": term.get("related") or [],
        "source": f"glossary:{term['term']}",
    }


def _numeric_question(question_text: str) -> bool:
    return bool(_NUMERIC_QUESTION_RE.search(question_text or ""))


def _subnet_reasoning_from_composer(question_text: str, network_plan: dict) -> dict:
    """CIDR/size questions in environment mode: explain from network_sizing.yaml's
    own reasoning text, with the actual figure inserted from the ALREADY-COMPUTED
    network_plan — never recomputed, never re-derived."""
    sizing = get_composer_file("network_sizing.yaml")
    subnets_by_id = {s["id"]: s for s in sizing["subnets"]}

    low = (question_text or "").lower()
    target = None
    for subnet in (network_plan or {}).get("subnets", []):
        short_id = subnet["id"].replace("snet_", "")
        if short_id in low or subnet["id"] in low or subnet["purpose"].lower() in low:
            target = subnet
            break

    if target is None:
        # Generic CIDR/arithmetic question, not about one specific subnet —
        # explain the method, cite the actual arithmetic already computed.
        arithmetic = " + ".join(str(t) for t in (network_plan or {}).get("arithmetic_terms", []))
        base_text = (
            f"{sizing['vnet_sizing']['method'].strip()} For this environment: "
            f"{arithmetic} = {network_plan.get('arithmetic_sum')} addresses, "
            f"{network_plan.get('utilisation_pct')}% of a {network_plan.get('vnet_size')}."
        )
        return {"text": base_text, "labels": ["presight_standard"],
                "related_terms": ["CIDR", "subnet"], "source": "composer:network_sizing.vnet_sizing"}

    kb_subnet = subnets_by_id.get(target["id"], {})
    reasoning = next((kb_subnet[f] for f in _SUBNET_NOTE_FIELDS if kb_subnet.get(f)), None)
    base_text = (
        f"{target['purpose']} is sized {target['size']} ({target['usable']} usable "
        f"addresses). {(reasoning or '').strip()} {target['basis']}"
    ).strip()
    hard_facts = [f"size: {target['size']}", f"usable: {target['usable']}"]
    text = _narrate(base_text, hard_facts)
    return {"text": text, "labels": ["presight_standard"],
            "related_terms": ["CIDR", "subnet", target["id"]], "source": f"composer:{target['id']}"}


def _from_platform_constants(question_text: str) -> dict:
    constants = get_platform_constants()
    low = (question_text or "").lower()
    keyword_sections = {
        "encryption": ["encrypt", "cmk", "key vault", "hsm"],
        "network_security": ["public", "private endpoint", "policy", "expose"],
        "public_exposure": ["public", "expose", "internet"],
        "identity": ["identity", "credential", "password"],
        "backup": ["backup", "recovery"],
        "sovereignty": ["sovereign", "regulat", "confidential corp"],
    }
    for section, keywords in keyword_sections.items():
        if section not in constants:
            continue
        if any(k in low for k in keywords):
            base_text = _readable_constants_section(constants[section])
            text = _narrate(base_text, [base_text])
            return {"text": text, "labels": ["presight_standard"], "related_terms": [],
                    "source": f"platform_constants:{section}"}
    return None


def _readable_constants_section(section_data: dict) -> str:
    """platform_constants.yaml sections are small dicts of scalar facts
    (public_paas_endpoints: denied, private_endpoint: required_for_paas, ...),
    not prose — join them into a plain sentence rather than dumping a raw
    dict repr, which would technically be grounded but unreadable."""
    if "rule" in section_data:
        return str(section_data["rule"]).strip()
    parts = []
    for key, value in section_data.items():
        if key in ("source", "policy_basis") or not isinstance(value, (str, bool)):
            continue
        label = key.replace("_", " ")
        parts.append(f"{label}: {value}")
    return ". ".join(parts) if parts else str(section_data)


def _from_catalog(service: str, question_text: str) -> dict:
    if not service:
        return None
    low = (question_text or "").lower()
    catalog = get_catalog()
    for pattern in catalog.values():
        if pattern.get("service") != service:
            continue
        for item in pattern.get("when_to_use", []):
            item_low = str(item).lower()
            if len(item_low) > 8 and (item_low in low or low in item_low
                                       or any(w in low for w in item_low.split() if len(w) > 5)):
                text = _narrate(item, [item])
                return {"text": text, "labels": ["presight_standard"], "related_terms": [],
                        "source": f"catalog:{pattern['id']}"}
    return None


def answer(question_text: str, mode: str, service: str = None,
           conversation_context: dict = None) -> dict:
    """conversation_context, when present, carries whatever the active
    engine has already computed — in particular `network_plan` for
    environment-mode conversations, so numeric questions are answered from
    it rather than recomputed."""
    context = conversation_context or {}

    # A CIDR/subnet-size-shaped question in environment mode is answered from
    # the already-computed network_plan FIRST, ahead of the glossary — a
    # generic glossary hit on a common word like "subnet" would otherwise
    # short-circuit into a definition instead of the specific sizing
    # reasoning the question actually asked for. Genuinely generic questions
    # ("what is a subnet?") don't match _NUMERIC_QUESTION_RE and still fall
    # through to the glossary below.
    if mode == "environment" and _numeric_question(question_text) and context.get("network_plan"):
        return _subnet_reasoning_from_composer(question_text, context["network_plan"])

    term = glossary.find_term(question_text)
    if term:
        return _from_glossary(term)

    found = _from_catalog(service, question_text)
    if found:
        return found

    found = _from_platform_constants(question_text)
    if found:
        return found

    return {
        "text": (
            "That's not something covered in Presight's standard guidance here — I don't "
            "want to guess at a policy that isn't documented. Worth checking with the "
            "platform team directly."
        ),
        "labels": ["outside_scope"],
        "related_terms": [],
        "source": None,
    }
