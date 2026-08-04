"""
Turn orchestrator for the advisor's persistent, conversational chat.

The STATE MACHINE owns the pending-question pointer. Every function here
that advances a conversation goes through question_engine.py (service
mode) or advisor/composer/intake.py (environment mode) — the exact same
pure-function engines the original single-shot flow already used and
already had verified. The LLM is consulted in exactly two places, both
narrow: classify_turn() (guided vs. freeform vs. both) and
advisor.freeform.answer() (already-retrieved KB text, narrated). Neither
is ever allowed to move the pending-question pointer, record an answer
question_engine/intake didn't validate, or invent a number.

Classification failure must not block input: any classifier exception,
timeout, or malformed response falls back to treating the turn as a
guided answer for the pending question — the guided flow must keep
working with the LLM completely dead, same guarantee the rest of the
advisor already provides.
"""
import json
import logging
import re

from advisor import conversations, freeform, prompts
from advisor import question_engine, rules_engine, recommendation as single_recommendation
from advisor import prefill as single_prefill
from advisor import catalog_loader
from advisor.catalog_loader import get_catalog
from advisor.composer import intake, composition_engine, sequencer
from advisor.composer import infosec as composer_infosec
from advisor.composer import render as composer_render

log = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_CLASSIFY_SYSTEM_PROMPT = """You classify one chat turn in a guided intake conversation.
The user was just asked a specific question (given below). Decide whether their reply:
  - "guided": answers that question (a value, a choice, "yes"/"no", a number, etc.)
  - "freeform": asks something else instead (a definition, a tangent, "what does X mean?")
  - "both": does both in one message (answers AND asks something else)

Reply with ONLY a JSON object, nothing else, in exactly this shape:
{"mode": "guided"|"freeform"|"both", "confidence": "high"|"low",
 "answer_value": <the answer, or null>, "question_text": <the freeform question, or null>}

If you are not confident which of the three applies, set "confidence": "low" — a wrongly
recorded answer is worse than one extra turn to clarify."""


def classify_turn(pending_question: dict, text: str, *, is_chip: bool,
                   chip_value=None) -> dict:
    """Chip clicks and text_parsed (inventory-parse) questions are ALWAYS
    guided, deterministically — never sent to the classifier at all. Every
    other failure mode (LLM error, timeout, malformed JSON, low confidence)
    also falls back to guided, per the module docstring.

    `text` is always the human-readable transcript content; `chip_value` is
    the actual value to record for a chip click (e.g. the boolean True
    behind a "Yes" chip) — they're deliberately separate, since a yes_no/
    single_choice answer's real value is rarely the same type as its label.

    `pending_question is None` happens for every synthetic/dynamic question
    that isn't in a static question bank — the inventory confirm-back turn,
    a correction confirmation, an environment composer `ask:` follow-up.
    These are deliberately always-guided too, same as text_parsed: they're
    narrow yes/no or confirm prompts, not places where "the user asked
    something else instead" is a meaningful distinction to classify."""
    if is_chip or pending_question is None or pending_question.get("type") == "text_parsed":
        value = chip_value if (is_chip and chip_value is not None) else text
        return {"mode": "guided", "answer_value": value, "question_text": None}

    fallback = {"mode": "guided", "answer_value": text, "question_text": None}
    try:
        user_content = (
            f"Pending question: {pending_question.get('question', '')}\n"
            f"Question type: {pending_question.get('type', 'text')}\n"
            f"User's reply: {text!r}"
        )
        raw = prompts.call_llm(_CLASSIFY_SYSTEM_PROMPT, user_content)
        match = _JSON_BLOCK_RE.search(raw)
        parsed = json.loads(match.group(0) if match else raw)
        if parsed.get("confidence") == "low":
            return {"mode": "freeform", "answer_value": None, "question_text": text}
        mode = parsed.get("mode")
        if mode not in ("guided", "freeform", "both"):
            return fallback
        return {"mode": mode, "answer_value": parsed.get("answer_value"),
                "question_text": parsed.get("question_text")}
    except Exception as exc:
        log.warning("advisor orchestrator: classification failed, falling back to guided: %s", exc)
        return fallback


# ── Correction detection ────────────────────────────────────────────────

_CORRECTION_INTENT_RE = re.compile(
    r"\bactually\b|\binstead\b|\bchange (?:it |that )?to\b|\bmake it\b|\bshould be\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b(\d+)\b")

# field -> keywords that identify it in a correction sentence, shared best-
# effort vocabulary across both modes (environment mode's *_count fields,
# plus a handful of common single-service fields). Ambiguous/unrecognized
# text produces a clarifying question rather than guessing which field.
_FIELD_KEYWORDS = {
    "vm_count": ("vm", "vms", "virtual machine"),
    "aks_count": ("aks", "cluster", "kubernetes"),
    "postgres_count": ("postgres", "database", "db"),
    "storage_count": ("storage",),
    "appgw_count": ("gateway", "appgw"),
    "node_count": ("node", "nodes"),
    "criticality": ("critical", "criticality"),
    "environment": ("environment", "env"),
}


def detect_correction(text: str, answers: dict) -> dict:
    """Deterministic-first: a regex correction-intent phrase combined with a
    number, mapped against already-answered fields. Returns None if no
    correction-intent phrase is present, or if the target field can't be
    confidently identified — in the ambiguous case the caller should ask a
    clarifying question rather than guess, per the module's "never silently
    mutate a recorded answer from a freeform turn" rule."""
    if not _CORRECTION_INTENT_RE.search(text or ""):
        return None
    numbers = _NUMBER_RE.findall(text)
    if not numbers:
        return {"field": None, "ambiguous": True, "raw_text": text}
    low = text.lower()
    candidates = [field for field, kws in _FIELD_KEYWORDS.items()
                  if any(kw in low for kw in kws) and field in answers]
    if len(candidates) != 1:
        return {"field": None, "ambiguous": True, "raw_text": text}
    field = candidates[0]
    return {"field": field, "old_value": answers.get(field), "new_value": int(numbers[-1]),
            "ambiguous": False, "raw_text": text}


# ── Recommendation building (service mode) — mirrors app.py's existing
# _advisor_build_prefill_and_recommendation for the single-shot flow, kept
# as a separate copy here rather than imported from app.py to avoid a
# Flask-module dependency in this pure-logic package. ─────────────────────

_SERVICE_PREFILL_BUILDERS = {
    "aks_cluster": single_prefill.build_prefill_aks,
    "vm_create": single_prefill.build_prefill_vm,
    "postgres_create": single_prefill.build_prefill_postgres,
    "app_gateway": single_prefill.build_prefill_appgw,
}


def _build_service_recommendation(service, pattern, answers, result):
    if service == "storage_account":
        prefill_payload = single_prefill.build_prefill(pattern, answers, result)
        rec = single_recommendation.build_recommendation(pattern, answers, result, prefill_payload)
    else:
        builder = _SERVICE_PREFILL_BUILDERS[service]
        prefill_payload = builder(pattern, answers, result)
        rec = single_recommendation.build_recommendation_generic(pattern, answers, result, prefill_payload)
    return prefill_payload, rec


def _finalize_service_turn(state: dict) -> dict:
    """Runs the deterministic pipeline once the question flow is exhausted.
    Returns {"kind": "blocked"|"redirect"|"tiebreak"|"escalate"|"recommendation",
    ...}. Mirrors app.py's api_advisor_chat tail exactly."""
    service = state["service"]
    result = rules_engine.evaluate_full(service, state["answers"])
    if result["blocked"]:
        rec = single_recommendation.build_blocked_response(result["blocker"], state["answers"])
        return {"kind": "blocked", **rec}

    redirect_esc = next((e for e in result["escalations"] if e.get("redirect")), None)
    if redirect_esc:
        catalog = get_catalog()
        rec = single_recommendation.build_redirect_response(result, catalog, state["answers"])
        return {"kind": "redirect", **rec}

    selection = result["selection"]
    if selection["outcome"] == "ask_tiebreak":
        return {"kind": "tiebreak", "question": selection["question"]}
    if selection["outcome"] == "no_match" or not selection.get("winner"):
        return {"kind": "escalate", "message": selection.get("message",
                "Unable to match a standard pattern.")}

    catalog = get_catalog()
    pattern = catalog[selection["winner"]]
    prefill_payload, rec = _build_service_recommendation(service, pattern, state["answers"], result)
    state["derived"] = result["derived"]
    state["escalations"] = result["escalations"]
    state["deviations"] = result["deviations"]
    state["warnings"] = result["warnings"]
    state["selected_pattern"] = pattern["id"]
    return {"kind": "recommendation", "recommendation": rec, "prefill_payload": prefill_payload,
            "selected_pattern": pattern["id"]}


def _finalize_environment_turn(state: dict) -> dict:
    """Environment-mode equivalent, mirrors app.py's
    _advisor_environment_compose + api_advisor_environment_plan."""
    answers = state["answers"]
    composition = composition_engine.evaluate_full(answers)
    if composition["blocked"]:
        return {"kind": "blocked", "message": composition["blocker"]["message"]}
    waves = sequencer.build_waves(answers)
    critical_path = sequencer.critical_path(answers)
    parallelism_message = sequencer.parallelism_message()
    brief = (composer_infosec.draft_brief(answers)
             if composer_infosec.gate_fires(answers) else None)
    full = composer_render.render_full(answers, composition, waves, critical_path,
                                        parallelism_message, brief)
    return {"kind": "recommendation", "recommendation": full, "prefill_payload": None,
            "selected_pattern": None}


# ── Main entry point ─────────────────────────────────────────────────────

def _kb_pin_for(conversation_id: int):
    """The KB version this conversation is pinned to — its kb_version_id
    (an advisor_kb_versions row id) if the conversation was created after a
    DB KB version existed, else None (disk). Wrapping every entry point in
    catalog_loader.pinned_to(this) is what makes 'a conversation finishes
    against the KB it started on' a mechanically enforced guarantee rather
    than an accident of caching — see catalog_loader.py's module docstring."""
    conv = conversations.get_conversation(conversation_id)
    return conv.get("kb_version_id") if conv else None


def start_conversation(conversation_id: int, mode: str) -> dict:
    with catalog_loader.pinned_to(_kb_pin_for(conversation_id)):
        return _start_conversation_impl(conversation_id, mode)


def _start_conversation_impl(conversation_id: int, mode: str) -> dict:
    """Computes and persists the first question/message for a brand-new
    conversation. Returns the same shape as process_turn's "question" event."""
    state = conversations.get_state(conversation_id)
    if mode == "service":
        engine_state = {"service": None, "answers": {}, "pending_followups": []}
        question = question_engine.next_question(engine_state)
    else:
        engine_state = intake.new_state()
        question = intake.next_question(engine_state)
    conversations.save_state(conversation_id, engine_state, state["version"],
                              pending_question_id=question["id"] if question else None)
    conversations.append_message(conversation_id, "assistant", "guided", question["question"],
                                  metadata={"question": question})
    return {"type": "question", "question": question}


def process_turn(conversation_id: int, mode: str, text: str, question_id: str,
                  is_chip: bool = False, chip_value=None) -> dict:
    with catalog_loader.pinned_to(_kb_pin_for(conversation_id)):
        return _process_turn_impl(conversation_id, mode, text, question_id, is_chip, chip_value)


def _process_turn_impl(conversation_id: int, mode: str, text: str, question_id: str,
                        is_chip: bool = False, chip_value=None) -> dict:
    """The main entry point. Returns one of:
      {"type": "question", "question": {...}}
      {"type": "correction_confirm", "message": "..."}
      {"type": "blocked"|"escalate"|"redirect", ...}
      {"type": "ready", "recommendation": {...}}
      {"type": "conflict"}   — stale optimistic-concurrency write
    Every branch appends the relevant transcript messages before returning.
    """
    conversations.append_message(conversation_id, "user", None, str(text))
    state_row = conversations.get_state(conversation_id)
    engine_state = state_row["answers"]  # full engine state, see conversations.py docstring
    version = state_row["version"]

    # A pending correction awaits this turn's yes/no confirmation.
    pending_correction = engine_state.get("pending_correction")
    if pending_correction:
        return _handle_correction_confirmation(conversation_id, mode, engine_state, version, text)

    if mode == "service":
        pending_question = question_engine.find_question(engine_state.get("service"), question_id)
    else:
        pending_question = intake.find_question(question_id)

    # Correction detection is a deterministic, rules-decide check — it runs
    # BEFORE classification and doesn't depend on the LLM classifier
    # recognizing "actually, make it 20 VMs" as freeform (a live classifier
    # might not always; "actually X" should never depend on that call
    # succeeding). Skipped for chip clicks (never a correction phrase) and
    # for a question's first-time text_parsed answer (the initial inventory
    # declaration isn't a correction — there's nothing recorded yet to
    # correct against).
    skip_correction_check = is_chip or (pending_question or {}).get("type") == "text_parsed"
    if not skip_correction_check:
        correction = detect_correction(text, engine_state.get("answers", {}))
        if correction and correction.get("ambiguous"):
            msg = ("I want to make sure I change the right thing — which answer would you "
                   "like to update?")
            conversations.append_message(conversation_id, "assistant", "freeform", msg)
            return {"type": "question", "question": pending_question}
        if correction:
            engine_state["pending_correction"] = correction
            conversations.save_state(conversation_id, engine_state, version,
                                      pending_question_id=state_row["pending_question_id"])
            confirm_msg = (f"Just to confirm — change {correction['field'].replace('_', ' ')} "
                            f"from {correction['old_value']} to {correction['new_value']}?")
            conversations.append_message(conversation_id, "assistant", "freeform", confirm_msg)
            return {"type": "question", "question": {"id": "_confirm_correction",
                    "question": confirm_msg, "type": "yes_no"}}

    classification = classify_turn(pending_question, text, is_chip=is_chip, chip_value=chip_value)
    turn_mode = classification["mode"]

    if turn_mode in ("freeform", "both"):
        freeform_text = classification["question_text"] or text
        service = engine_state.get("service") if mode == "service" else None
        context = {"network_plan": engine_state.get("_last_network_plan")} if mode == "environment" else {}
        answer_payload = freeform.answer(freeform_text, mode, service, context)
        conversations.append_message(conversation_id, "assistant", "freeform",
                                      answer_payload["text"], metadata=answer_payload)

    if turn_mode == "freeform":
        # Re-ask the SAME pending question — the state machine still owns the
        # pointer. pending_question_id in advisor_state is unchanged.
        conversations.append_message(conversation_id, "assistant", "guided",
                                      pending_question["question"] if pending_question else "")
        return {"type": "question", "question": pending_question}

    # guided, or the guided half of "both"
    answer_value = classification["answer_value"] if classification["answer_value"] is not None else text
    return _advance_guided(conversation_id, mode, engine_state, version, question_id, answer_value)


def _handle_correction_confirmation(conversation_id, mode, engine_state, version, text) -> dict:
    correction = engine_state.pop("pending_correction")
    affirmative = text.strip().lower() in ("yes", "y", "confirm", "correct", "true", "1")
    if not affirmative:
        msg = "No change made."
        conversations.save_state(conversation_id, engine_state, version)
        conversations.append_message(conversation_id, "assistant", "guided", msg)
        return {"type": "question", "question": None, "message": msg}

    engine_state.setdefault("answers", {})[correction["field"]] = correction["new_value"]
    invalidated = bool(conversations.get_state(conversation_id).get("recommendation"))
    ok = conversations.save_state(conversation_id, engine_state, version, recommendation=None)
    if not ok:
        return {"type": "conflict"}
    msg = (f"Updated {correction['field'].replace('_', ' ')} to {correction['new_value']}.")
    if invalidated:
        msg += " This invalidates the earlier recommendation — recomputing."
    conversations.append_message(conversation_id, "assistant", "guided", msg)

    if mode == "environment" and invalidated:
        result = _finalize_environment_turn(engine_state)
        return _persist_finalize_result(conversation_id, mode, engine_state, result)
    if mode == "service" and invalidated:
        result = _finalize_service_turn(engine_state)
        return _persist_finalize_result(conversation_id, mode, engine_state, result)
    return {"type": "question", "question": None, "message": msg}


def _advance_guided(conversation_id, mode, engine_state, version, question_id, answer_value) -> dict:
    if mode == "service":
        engine_state = question_engine.record_answer(engine_state, question_id, answer_value)
        next_q = question_engine.next_question(engine_state)
    else:
        engine_state = intake.record_answer(engine_state, question_id, answer_value)
        next_q = intake.next_question(engine_state)

    if next_q is not None:
        ok = conversations.save_state(conversation_id, engine_state, version,
                                       pending_question_id=next_q["id"])
        if not ok:
            return {"type": "conflict"}
        conversations.append_message(conversation_id, "assistant", "guided", next_q["question"],
                                      metadata={"question": next_q})
        return {"type": "question", "question": next_q}

    if mode == "service":
        blockers = rules_engine.evaluate_blockers(engine_state["service"], engine_state["answers"]) \
            if engine_state.get("service") else {"blocked": False}
        if blockers["blocked"]:
            rec = single_recommendation.build_blocked_response(blockers, engine_state["answers"])
            conversations.save_state(conversation_id, engine_state, version)
            conversations.append_message(conversation_id, "assistant", "guided", rec.get("message", ""))
            return {"type": "blocked", **rec}
        result = _finalize_service_turn(engine_state)
    else:
        blockers = composition_engine.evaluate_environment_blockers(engine_state["answers"])
        if blockers["blocked"]:
            conversations.save_state(conversation_id, engine_state, version)
            conversations.append_message(conversation_id, "assistant", "guided", blockers["message"])
            return {"type": "blocked", "message": blockers["message"]}
        result = _finalize_environment_turn(engine_state)
        if result["kind"] == "recommendation":
            engine_state["_last_network_plan"] = result["recommendation"]["network_plan"]

    return _persist_finalize_result(conversation_id, mode, engine_state, result, version)


def _persist_finalize_result(conversation_id, mode, engine_state, result, version=None) -> dict:
    if version is None:
        version = conversations.get_state(conversation_id)["version"]
    if result["kind"] == "blocked":
        conversations.save_state(conversation_id, engine_state, version)
        conversations.append_message(conversation_id, "assistant", "guided", result.get("message", ""))
        return {"type": "blocked", **result}
    if result["kind"] in ("redirect", "escalate", "tiebreak"):
        conversations.save_state(conversation_id, engine_state, version)
        conversations.append_message(conversation_id, "assistant", "guided",
                                      result.get("message") or result.get("question", ""))
        return {"type": result["kind"], **result}

    conversations.save_state(conversation_id, engine_state, version,
                              selected_pattern=result.get("selected_pattern"),
                              recommendation=result["recommendation"],
                              prefill_payload=result.get("prefill_payload"))
    conversations.set_status(conversation_id, "recommended")
    conversations.append_message(conversation_id, "assistant", "guided",
                                  "Here's the recommendation.", metadata={"recommendation": True})
    return {"type": "ready", "recommendation": result["recommendation"]}
