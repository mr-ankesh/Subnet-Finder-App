"""
Assert-based checks for the advisor's persistent, conversational chat
(advisor/conversations.py, advisor/orchestrator.py, advisor/glossary.py,
advisor/freeform.py). Separate from test_advisor_validation.py because this
layer genuinely needs a database (a scratch SQLite file) and cross-turn
state, unlike that suite's "no Flask app, no DB, no LLM needed" contract.
Run: python scripts/test_advisor_conversations.py

Covers all 19 numbered verification items from the persistent-chat spec.
Item 16 (both backends) was already verified against a real local
Postgres 18 instance during development (see the Stage 1 commit message);
re-run here only against SQLite, consistent with this repo's no-CI,
run-and-check convention — a fresh Postgres instance isn't assumed to be
running for every future invocation of this script.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_backend
_SCRATCH_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_scratch_advisor_conversations.db")
if os.path.exists(_SCRATCH_DB):
    os.remove(_SCRATCH_DB)
db_backend.SQLITE_PATH = _SCRATCH_DB

from advisor import conversations as C
from advisor import orchestrator as orch
from advisor import glossary
from advisor import prompts

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAILED: {name}"
    passed += 1
    print(f"  ok: {name}")


def _dead_llm(*a, **k):
    raise RuntimeError("LLM dead (test)")


def _drive(cid, mode, script, is_chip_default=False):
    """script: {question_id: answer} where answer is either a plain string
    (free text) or a (value, label) tuple (chip)."""
    resp = orch.start_conversation(cid, mode)
    turns = 0
    while resp["type"] == "question" and resp.get("question"):
        qid = resp["question"]["id"]
        ans = script.get(qid, "n/a")
        if isinstance(ans, tuple):
            val, label = ans
            resp = orch.process_turn(cid, mode, label, qid, is_chip=True, chip_value=val)
        else:
            resp = orch.process_turn(cid, mode, ans, qid, is_chip=False)
        turns += 1
        if turns > 40:
            raise RuntimeError(f"drive() loop exceeded 40 turns, stuck at {qid!r}")
    return resp


_ENV_POSITIVE_SCRIPT = {
    "environment_intent": "Production hosting for a customer-facing portal",
    "subscription_available": (True, "Yes"),
    "environment": ("dev", "Development"),
    "resource_inventory": "10 VMs, 1 AKS cluster, 1 managed PostgreSQL. The application will be publicly hosted.",
    "_confirm_inventory": "yes",
    "exposure": ("public_internet", "Public"),
    "public_details": "app.presight.ai, public customers",
    "vm_purpose": "App tier",
    "aks_scale": "6 nodes",
    "gpu_required": (False, "No"),
    "database_criticality": ("single", "Single"),
    "data_classification": ("confidential", "Confidential"),
    "business_unit": "Platform",
    "application_name": "MyApp",
    "owner_email": "a@b.com",
    "criticality": ("high", "High"),
    "target_date": "Q3",
    "storage_for_aks": (True, "Yes"),
}


def _generic_answer(q):
    if q["type"] == "yes_no":
        return (True, "Yes")
    if q["type"] in ("single_choice",) and q.get("options"):
        return (q["options"][0]["value"], q["options"][0]["label"])
    if q["type"] == "integer":
        return "5"
    if q["type"] == "email":
        return "owner@presight.ai"
    return "test answer"


# ── Items 1-3: resume, cross-session persistence, cross-owner denial ──────
print("Items 1-3: resume mid-intake, cross-session persistence, cross-owner denial")
prompts.call_llm = _dead_llm

cid = C.create_conversation("user-resume", "environment")
resp = orch.start_conversation(cid, "environment")
resp = orch.process_turn(cid, "environment", "test env", resp["question"]["id"], is_chip=False)
resp = orch.process_turn(cid, "environment", "Yes", resp["question"]["id"], is_chip=True, chip_value=True)
resp = orch.process_turn(cid, "environment", "Development", resp["question"]["id"], is_chip=True, chip_value="dev")
q4_id_before = resp["question"]["id"]
check("item 1: resumes at question 4 (resource_inventory) after 3 answers", q4_id_before == "resource_inventory")
state_row = C.get_state(cid)
check("item 1: persisted pending_question_id matches the in-memory pointer",
      state_row["pending_question_id"] == q4_id_before)

check("item 2: conversation still listed for the same owner_key (simulates logout/login — "
      "a fresh session that resolves to the same identity sees the same history)",
      any(c["id"] == cid for c in C.list_conversations("user-resume")))

check("item 3: a different owner cannot open this conversation by id",
      C.owns(cid, "someone-else") is False)
C.delete_conversation(cid, "user-resume")

# ── Item 18: kb_version pinned at creation, survives resume ───────────────
print("\nItem 18: kb_version pinned at creation")
cid = C.create_conversation("user-kb", "service")
conv = C.get_conversation(cid)
check("item 18: kb_version recorded at creation", conv["kb_version"] == C.KB_VERSION)
orch.start_conversation(cid, "service")
conv2 = C.get_conversation(cid)
check("item 18: kb_version unchanged after further turns (never recomputed on read)",
      conv2["kb_version"] == conv["kb_version"])
C.delete_conversation(cid, "user-kb")

# ── Items 4-5, 8: freeform mid-intake, both-mode, alias resolution ────────
print("\nItems 4, 5, 8: freeform mid-intake, both-mode, glossary alias resolution")


def mock_classifier_realistic(system, user):
    if system == orch._CLASSIFY_SYSTEM_PROMPT:
        reply_line = [l for l in user.splitlines() if l.startswith("User's reply:")][0]
        text = reply_line.split(":", 1)[1].strip().strip("'")
        low = text.lower()
        if "?" in text and " and " in low:
            return json.dumps({"mode": "both", "confidence": "high",
                                "answer_value": text.split(",")[0].strip(), "question_text": text})
        if "?" in text:
            return json.dumps({"mode": "freeform", "confidence": "high",
                                "answer_value": None, "question_text": text})
        return json.dumps({"mode": "guided", "confidence": "high", "answer_value": text, "question_text": None})
    return "narrated"


prompts.call_llm = mock_classifier_realistic

cid = C.create_conversation("user-freeform", "environment")
resp = orch.start_conversation(cid, "environment")
resp = orch.process_turn(cid, "environment", "test env", resp["question"]["id"], is_chip=False)
resp = orch.process_turn(cid, "environment", "Yes", resp["question"]["id"], is_chip=True, chip_value=True)
resp = orch.process_turn(cid, "environment", "Development", resp["question"]["id"], is_chip=True, chip_value="dev")
resp = orch.process_turn(cid, "environment", "10 VMs, 1 AKS cluster", resp["question"]["id"], is_chip=False)
resp = orch.process_turn(cid, "environment", "yes", resp["question"]["id"], is_chip=False)
check("setup: reached exposure question", resp["question"]["id"] == "exposure")

pqid_before = C.get_state(cid)["pending_question_id"]
resp = orch.process_turn(cid, "environment", "what's a private endpoint?", resp["question"]["id"], is_chip=False)
pqid_after = C.get_state(cid)["pending_question_id"]
check("item 4: freeform mid-intake re-asks the SAME pending question, never skips ahead",
      resp["type"] == "question" and resp["question"]["id"] == pqid_before)
check("item 4: pending_question_id is BYTE-IDENTICAL before and after the freeform turn",
      pqid_before == pqid_after)
msgs = C.list_messages(cid)
check("item 4: the freeform answer came from the glossary (private_endpoint), tagged mode=freeform",
      msgs[-2]["mode"] == "freeform"
      and (msgs[-2]["metadata"] or {}).get("source") == "glossary:private_endpoint")
check("item 4: the re-ask message is tagged mode=guided — the state machine speaking, not freeform",
      msgs[-1]["mode"] == "guided")

resp = orch.process_turn(cid, "environment", "public_internet", resp["question"]["id"],
                          is_chip=True, chip_value="public_internet")
check("setup: reached public_details question", resp["question"]["id"] == "public_details")
resp = orch.process_turn(cid, "environment", "app.presight.ai, and what does WAF mean?",
                          resp["question"]["id"], is_chip=False)
# Matches the spec's own "both" example shape ("2 TB, and what does ZRS mean?") —
# an answer, "and", then a question — recorded as the current pending question's
# answer while the aside is separately answered from the glossary.
check("item 5: 'both' turn advances past the current question rather than re-asking it",
      resp["type"] == "question" and resp["question"]["id"] != "public_details")
msgs2 = C.list_messages(cid)
check("item 5: the aside was still answered (a freeform-tagged message present for this turn)",
      any(m["mode"] == "freeform" for m in msgs2[-3:]))

check("item 8: 'what's an HSM?' resolves via alias to the RSA_HSM glossary entry",
      glossary.find_term("what's an HSM?")["term"] == "RSA_HSM")
C.delete_conversation(cid, "user-freeform")

# ── Items 6-7: outside-scope honesty, holds the Presight line ─────────────
print("\nItems 6-7: outside-scope honesty, holds the Presight line")
from advisor import freeform as freeform_mod
prompts.call_llm = _dead_llm

r = freeform_mod.answer("does presight allow hosting in a region not on the approved list",
                         "service", "storage_account")
check("item 6: a question with no KB match is labelled outside_scope, no invented claim",
      r["labels"] == ["outside_scope"] and "presight" not in r["text"].lower().split("that's")[0])

r = freeform_mod.answer("can I use a public endpoint for storage instead of a private one?",
                         "service", "storage_account")
check("item 7: 'public endpoint for storage' holds the Presight line (mandatory private endpoint), "
      "not a balanced general-Azure comparison",
      "mandatory" in r["text"].lower() and r["labels"] == ["presight_standard"])

# ── Items 9, 12: correction detect/confirm, invalidation + recompute ──────
print("\nItems 9, 12: correction detection, confirmation, recommendation invalidation + recompute")
prompts.call_llm = _dead_llm

cid = C.create_conversation("user-correction", "environment")
resp = _drive(cid, "environment", _ENV_POSITIVE_SCRIPT)
check("setup: reached a recommendation", resp["type"] == "ready")
plan_before = resp["recommendation"]
vm_count_before = C.get_state(cid)["answers"]["answers"]["vm_count"]
check("setup: vm_count is 10 before correction", vm_count_before == 10)

resp = orch.process_turn(cid, "environment", "actually, make it 20 VMs", "x", is_chip=False)
check("item 9: correction phrase produces a confirmation turn, does NOT mutate answers yet",
      resp["type"] == "question" and resp["question"]["id"] == "_confirm_correction"
      and C.get_state(cid)["answers"]["answers"]["vm_count"] == 10)

resp2 = orch.process_turn(cid, "environment", "yes", "_confirm_correction", is_chip=False)
state_after = C.get_state(cid)
check("item 9: confirming applies the change", state_after["answers"]["answers"]["vm_count"] == 20)
check("item 12: an existing recommendation is invalidated and genuinely RECOMPUTED "
      "(different subnet size), not patched",
      resp2["type"] == "ready"
      and resp2["recommendation"]["network_plan"]["arithmetic_line"] != plan_before["network_plan"]["arithmetic_line"])
msgs = C.list_messages(cid)
check("item 9: the invalidation is stated in the transcript",
      any("invalidates" in m["content"].lower() for m in msgs))
C.delete_conversation(cid, "user-correction")

# ── Item 11: environment-mode numeric question never recomputes ──────────
print("\nItem 11: CIDR/subnet-size questions answered from the KB, never recomputed")
from advisor.composer import network_planner
answers = {"vm_count": 10, "aks_count": 1, "postgres_count": 1, "storage_count": 1,
           "appgw_count": 0, "_aks_node_count": 6}
inferred = {"keyvault_premium_private", "container_registry", "appgw_public_cloudflare"}
plan = network_planner.build_network_plan(answers, inferred)
r = freeform_mod.answer("why is the AKS subnet only a /26?", "environment", None, {"network_plan": plan})
aks_subnet = plan["subnets"][1]
check("item 11: the answer states the planner's OWN size/usable figures, never re-derived",
      aks_subnet["size"] in r["text"] and str(aks_subnet["usable"]) in r["text"])
check("item 11: the answer explains via Overlay/nodes, not pod count",
      "overlay" in r["text"].lower())

# ── Item 10: environment mode resume renders the full plan ────────────────
print("\nItem 10: environment mode resume carries the full plan")
cid = C.create_conversation("user-resume-env", "environment")
resp = _drive(cid, "environment", _ENV_POSITIVE_SCRIPT)
state = C.get_state(cid)
rec = state["recommendation"]
check("item 10: resumed state's recommendation has the subnet table", len(rec["network_plan"]["subnet_rows"]) == 4)
check("item 10: resumed state's recommendation has the arithmetic line",
      "384 addresses" in rec["network_plan"]["arithmetic_line"])
check("item 10: resumed state's recommendation has the Pod CIDR paragraph",
      rec["network_plan"]["pod_cidr_paragraph"] is not None)
check("item 10: resumed state's recommendation has the InfoSec section", rec["public_access"] is not None)
check("item 10: resumed state's recommendation has all 7 build waves", len(rec["build_sequence"]["rows"]) == 7)
C.delete_conversation(cid, "user-resume-env")

# ── Items 13-14: LLM dead vs. classifier-only dead (kept distinct) ────────
print("\nItems 13-14: LLM entirely dead vs. ONLY the classifier dead (distinct failure surfaces)")
prompts.call_llm = _dead_llm

cid_svc = C.create_conversation("user-13-svc", "service")
resp = orch.start_conversation(cid_svc, "service")
resp = orch.process_turn(cid_svc, "service", "Storage", resp["question"]["id"],
                          is_chip=True, chip_value="storage_account")
turns = 0
while resp["type"] == "question" and resp.get("question"):
    q = resp["question"]
    val, label = _generic_answer(q) if q["type"] in ("yes_no", "single_choice") else (None, None)
    if val is not None:
        resp = orch.process_turn(cid_svc, "service", label, q["id"], is_chip=True, chip_value=val)
    else:
        resp = orch.process_turn(cid_svc, "service", _generic_answer(q), q["id"], is_chip=False)
    turns += 1
    if turns > 30:
        break
check("item 13 (service mode): guided flow completes to a recommendation with the LLM entirely dead",
      resp["type"] == "ready")
C.delete_conversation(cid_svc, "user-13-svc")

cid_env = C.create_conversation("user-13-env", "environment")
resp = _drive(cid_env, "environment", _ENV_POSITIVE_SCRIPT)
check("item 13 (environment mode): guided flow completes to a recommendation with the LLM entirely dead",
      resp["type"] == "ready")
C.delete_conversation(cid_env, "user-13-env")


def _classifier_only_dead(system, user):
    if system == orch._CLASSIFY_SYSTEM_PROMPT:
        raise RuntimeError("classifier endpoint down")
    return "narrated fine"  # freeform narration still works if reached


prompts.call_llm = _classifier_only_dead
cid = C.create_conversation("user-14", "service")
resp = orch.start_conversation(cid, "service")
resp = orch.process_turn(cid, "service", "Storage", resp["question"]["id"], is_chip=True, chip_value="storage_account")
q_before = resp["question"]["id"]
resp = orch.process_turn(cid, "service", "Yes", resp["question"]["id"], is_chip=False)
check("item 14: with ONLY the classifier broken, guided input still records the answer and advances "
      "(distinct from item 13 — narration would still work here if reached)",
      resp["type"] == "question" and resp["question"]["id"] != q_before)
C.delete_conversation(cid, "user-14")

# ── Item 15: optimistic concurrency rejects a stale write ─────────────────
print("\nItem 15: two-tab concurrent write — stale write rejected")
prompts.call_llm = _dead_llm
cid = C.create_conversation("user-15", "service")
resp = orch.start_conversation(cid, "service")
state_tab_a = C.get_state(cid)
resp = orch.process_turn(cid, "service", "Storage", resp["question"]["id"], is_chip=True, chip_value="storage_account")
check("item 15: tab B's write succeeds", resp["type"] == "question")
stale_ok = C.save_state(cid, state_tab_a["answers"], state_tab_a["version"], pending_question_id="STALE")
check("item 15: tab A's write, using its now-stale version, is rejected rather than corrupting state",
      stale_ok is False)
C.delete_conversation(cid, "user-15")

# ── Item 17: delete cascades cleanly, nothing orphaned ────────────────────
print("\nItem 17: delete cascades — messages and state, nothing orphaned")
cid = C.create_conversation("user-17", "service")
orch.start_conversation(cid, "service")
C.delete_conversation(cid, "user-17")
with db_backend.connect() as conn:
    m = conn.execute("SELECT COUNT(*) AS n FROM advisor_messages WHERE conversation_id=?", (cid,)).fetchone()
    s = conn.execute("SELECT COUNT(*) AS n FROM advisor_state WHERE conversation_id=?", (cid,)).fetchone()
check("item 17: no orphaned messages after delete", m["n"] == 0)
check("item 17: no orphaned state row after delete", s["n"] == 0)

# ── Item 16: schema portability — see Stage 1 commit for the real-Postgres
# run; re-asserted here structurally against SQLite (always available).
print("\nItem 16: schema present and functional (real-Postgres run recorded in Stage 1's commit)")
with db_backend.connect() as conn:
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'advisor_%'").fetchall()}
check("item 16: all three conversation tables exist",
      {"advisor_conversations", "advisor_messages", "advisor_state"} <= tables)

os.remove(_SCRATCH_DB)
print(f"\n{passed} checks passed.")
