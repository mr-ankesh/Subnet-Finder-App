"""
Ask-order flow control for advisor_kb/questions/<service>_questions.yaml:
skip_if, follow_up_if, default_if_unknown. Pure flow logic — it does NOT
map free text to option values (that's the question-stage LLM's job, one
layer up in the route; by the time record_answer() is called the answer is
already a normalized value or one of the question's own option values).

Service selection (advisor/services.py's SERVICE_QUESTION) is the literal
first question of every conversation and is handled entirely by this module,
before any per-service question bank is even loaded — `state["service"]` is
None until it's answered.

`stop_if`/`escalate_if` in the question bank are deliberately NOT executed
here — they duplicate, in plain English, blockers/escalations that
rules_engine.py evaluates incrementally after every answer. rules_engine
stays the one authoritative halt/escalate source (see its own docstring).

want_diagram (order 900, ask_last) is excluded from this module's ordering
entirely — it's offered by the frontend after the recommendation renders,
via a direct call to /api/advisor/diagram, not threaded through this flow.

Schema normalization: the six-service KB delta introduced two question
shapes that didn't exist in the storage-only build —
  - `options` as a plain list of strings (`[dev, tst, uat]`) as well as the
    original list of {value, label} dicts.
  - `skip_if` as a bare condition string as well as the original
    {condition, set} dict.
  - a new `type: integer` question (vm_count) needing whole-number coercion.
Both option/skip_if shapes are normalized once, at load time, so every
downstream consumer (this module, prefill.py, the frontend payload) only
ever sees the {value, label} / {condition, set} shape.
"""
from advisor.catalog_loader import get_questions
from advisor.condition_eval import evaluate, apply_set
from advisor.services import SERVICE_QUESTION, SERVICE_QUESTION_ID, is_valid


def _normalize_options(options: list) -> list:
    if not options:
        return []
    if isinstance(options[0], dict):
        return options
    return [{"value": v, "label": str(v).replace("_", " ").title()} for v in options]


def _normalize_skip_if(skip_if):
    if skip_if is None:
        return None
    if isinstance(skip_if, dict):
        return skip_if
    return {"condition": skip_if, "set": None}


def _normalize_question(q: dict) -> dict:
    q = dict(q)
    if "options" in q:
        q["options"] = _normalize_options(q["options"])
    if "skip_if" in q:
        q["skip_if"] = _normalize_skip_if(q["skip_if"])
    return q


def _service_questions(service: str) -> list:
    return [_normalize_question(q) for q in get_questions(service)["questions"]]


def _questions_in_order(service: str) -> list:
    qs = _service_questions(service)
    return sorted((q for q in qs if not q.get("ask_last")), key=lambda q: q["order"])


def find_question(service, question_id: str) -> dict:
    """`service` may be None (only the service-selection question is
    findable before a service is chosen)."""
    if question_id == SERVICE_QUESTION_ID:
        return SERVICE_QUESTION
    if not service:
        return None
    for q in _service_questions(service):
        if q["id"] == question_id:
            return q
    return None


def _coerce_value(question: dict, value):
    if question.get("type") == "integer" and value not in (None, "unsure"):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return value
    return value


def next_question(state: dict) -> dict:
    """Returns the next question dict to ask, or None once the flow is
    exhausted. `state` = {"service": id|None, "answers": {...},
    "pending_followups": [...]}. Service selection always comes first."""
    if not state.get("service"):
        return SERVICE_QUESTION

    pending = state.get("pending_followups") or []
    if pending:
        return pending[0]

    answers = state.get("answers", {})
    for q in _questions_in_order(state["service"]):
        if q["id"] in answers:
            continue
        skip = q.get("skip_if")
        if skip and evaluate(skip["condition"], answers):
            if skip.get("set"):
                apply_set(answers, skip["set"])
            continue
        return q
    return None


def record_answer(state: dict, question_id: str, value) -> dict:
    """Records a (already-normalized) answer, applying default_if_unknown,
    queuing any follow_up_if sub-questions, and clearing this question from
    the pending-followup queue if that's where it came from."""
    answers = state.setdefault("answers", {})
    pending = state.setdefault("pending_followups", [])

    if question_id == SERVICE_QUESTION_ID:
        if is_valid(value):
            state["service"] = value
        return state

    question = find_question(state.get("service"), question_id)
    if question is None:
        # Could be a follow_up_if sub-question, not in the top-level list.
        question = next((p for p in pending if p["id"] == question_id), None)

    value = _coerce_value(question, value) if question else value

    if question and value == "unsure" and question.get("default_if_unknown"):
        value = question["default_if_unknown"]

    answers[question_id] = value

    state["pending_followups"] = [p for p in pending if p["id"] != question_id]

    if question:
        follow_up = question.get("follow_up_if")
        if follow_up and evaluate(follow_up["condition"], {"answer": value, **answers}):
            for sub_q in follow_up["ask"]:
                if sub_q["id"] not in answers:
                    state["pending_followups"].append(sub_q)

    return state


def is_complete(state: dict) -> bool:
    return next_question(state) is None
