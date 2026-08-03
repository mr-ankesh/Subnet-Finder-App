"""
Ask-order flow control for advisor_kb/questions/storage_questions.yaml:
skip_if, follow_up_if, default_if_unknown. Pure flow logic — it does NOT
map free text to option values (that's the question-stage LLM's job, one
layer up in the route; by the time record_answer() is called the answer is
already a normalized value or one of the question's own option values).

`stop_if`/`escalate_if` in the question bank are deliberately NOT executed
here — they duplicate, in plain English, blockers/escalations that
rules_engine.py evaluates incrementally after every answer. rules_engine
stays the one authoritative halt/escalate source (see its own docstring).

want_diagram (order 900, ask_last) is excluded from this module's ordering
entirely — it's offered by the frontend after the recommendation renders,
via a direct call to /api/advisor/diagram, not threaded through this flow.
"""
from advisor.catalog_loader import get_questions
from advisor.condition_eval import evaluate, apply_set


def _questions_in_order() -> list:
    qs = get_questions()["questions"]
    return sorted((q for q in qs if not q.get("ask_last")), key=lambda q: q["order"])


def find_question(question_id: str) -> dict:
    for q in get_questions()["questions"]:
        if q["id"] == question_id:
            return q
    return None


def next_question(state: dict) -> dict:
    """Returns the next question dict to ask, or None once the flow is
    exhausted. `state` = {"answers": {...}, "pending_followups": [...]}."""
    pending = state.get("pending_followups") or []
    if pending:
        return pending[0]

    answers = state.get("answers", {})
    for q in _questions_in_order():
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

    question = find_question(question_id)
    if question is None:
        # Could be a follow_up_if sub-question, not in the top-level list.
        question = next((p for p in pending if p["id"] == question_id), None)

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
