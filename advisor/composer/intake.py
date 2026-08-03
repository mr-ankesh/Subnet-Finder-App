"""
Environment-mode intake flow controller — advisor_kb/composer/environment_questions.yaml.

Deliberately NOT registered in catalog_loader.SERVICE_FILES / driven through
question_engine.py: the environment composer doesn't select a pattern, so
it doesn't fit that per-service machinery, and it needs three mechanics
question_engine.py doesn't have:
  - `type: text_parsed` (the resource_inventory question) — parses free text
    via inventory_parser and always confirms the parse back before moving
    on (environment_questions.yaml's own `confirm_back: true`).
  - a confirm-back turn, itself a question, not part of the static list.
  - dynamically-injected `ask:` follow-ups from composition_rules.yaml
    (e.g. storage_for_aks) that only exist once the static list is
    exhausted and depend on composition_engine's own rule evaluation.

Reuses question_engine.py's schema-normalization helpers as plain
functions (same {value,label}/{condition,set} normalization already proven
for the six-service build) rather than registering as a pseudo-service.

`subscription_available`'s `stop_if` is deliberately NOT executed here —
same discipline as question_engine.py's single-service flow: it duplicates,
in plain English, composition_engine.evaluate_environment_blockers's
no_subscription blocker, which is re-run after every answer and stays the
one authoritative halt source.
"""
import functools

from advisor.catalog_loader import get_composer_file
from advisor.condition_eval import evaluate, apply_set
from advisor.question_engine import _normalize_options, _normalize_skip_if
from advisor.composer import inventory_parser
from advisor.composer import composition_engine

CONFIRM_QUESTION_ID = "_confirm_inventory"
_INVENTORY_QUESTION_ID = "resource_inventory"
_AFFIRMATIVE = ("yes", "correct", "yep", "y", "confirmed", "right", "")


@functools.lru_cache(maxsize=1)
def _questions() -> dict:
    return get_composer_file("environment_questions.yaml")


def _normalize_question(q: dict) -> dict:
    q = dict(q)
    if "options" in q:
        q["options"] = _normalize_options(q["options"])
    if "skip_if" in q:
        q["skip_if"] = _normalize_skip_if(q["skip_if"])
    return q


def _questions_in_order() -> list:
    qs = [_normalize_question(q) for q in _questions()["questions"]]
    return sorted((q for q in qs if not q.get("ask_last")), key=lambda q: q["order"])


def find_question(question_id: str) -> dict:
    for q in _questions_in_order():
        if q["id"] == question_id:
            return q
    return None


def _coerce(question: dict, value):
    qtype = question.get("type")
    if qtype == "yes_no":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("yes", "true", "y", "1")
    if qtype == "integer" and value not in (None, "unsure"):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return value
    return value


def new_state() -> dict:
    return {"answers": {}, "pending_confirm": None, "resolved_asks": []}


def next_question(state: dict) -> dict:
    """Order: static questions (skip_if-aware) -> inventory confirm-back (if
    just parsed) -> dynamically-injected `ask:` follow-ups -> None (ready
    for composition_engine.evaluate_full)."""
    answers = state.get("answers", {})

    if state.get("pending_confirm"):
        return state["pending_confirm"]

    for q in _questions_in_order():
        if q["id"] in answers:
            continue
        skip = q.get("skip_if")
        if skip and evaluate(skip["condition"], answers):
            if skip.get("set"):
                apply_set(answers, skip["set"])
            continue
        return q

    # Static list exhausted — surface the first unresolved dynamically
    # injected `ask:` follow-up, one at a time, never more.
    pending_ask = composition_engine.infer_missing_components(
        {**answers, "_resolved_asks": state.get("resolved_asks") or []}
    )["pending_ask"]
    if pending_ask:
        return {"id": pending_ask["id"], "question": pending_ask["question"],
                "type": "yes_no", "dynamic": True}

    return None


def record_answer(state: dict, question_id: str, value) -> dict:
    """Records one answer and advances the flow. Returns the updated state."""
    answers = state.setdefault("answers", {})
    resolved = state.setdefault("resolved_asks", [])

    if question_id == CONFIRM_QUESTION_ID:
        state["pending_confirm"] = None
        reply = str(value or "").strip().lower()
        if reply not in _AFFIRMATIVE:
            # Treat anything else as a correction — re-parse it and merge
            # any nonzero counts over the original parse. A confirm-back
            # reply is either an affirmation or a correction like
            # "actually 12 VMs", never a fresh unrelated sentence.
            correction = inventory_parser.parse_inventory(str(value))
            for k, v in correction.items():
                if k != "other_services" and v:
                    answers[k] = v
        return state

    question = find_question(question_id)

    if question and question.get("type") == "text_parsed":
        parsed = inventory_parser.parse_inventory(str(value))
        answers.update(parsed)
        answers[question_id] = value
        if question.get("confirm_back"):
            confirmation_text = inventory_parser.format_confirmation(
                parsed, question["confirm_template"])
            state["pending_confirm"] = {
                "id": CONFIRM_QUESTION_ID, "question": confirmation_text, "type": "text",
            }
        return state

    if question is None:
        # A dynamically-injected `ask:` follow-up (e.g. storage_for_aks),
        # not part of the static question list.
        if question_id not in resolved:
            resolved.append(question_id)
        answered_yes = str(value).strip().lower() in ("yes", "true", "y", "1")
        if question_id == "storage_for_aks" and answered_yes:
            answers["storage_count"] = max(int(answers.get("storage_count") or 0), 1)
        return state

    value = _coerce(question, value)
    if value == "unsure" and question.get("default_if_unknown"):
        value = question["default_if_unknown"]
    answers[question_id] = value
    return state


def is_complete(state: dict) -> bool:
    return next_question(state) is None
