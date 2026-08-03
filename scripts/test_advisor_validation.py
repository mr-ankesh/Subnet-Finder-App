"""
Assert-based checks for the AI Architecture Advisor's pure logic — no Flask
app, no DB, no LLM needed. Run: python scripts/test_advisor_validation.py

Mirrors scripts/test_storage_validation.py / test_resourcegraph_validation.py's
style (assert-based, no pytest). The original 63 checks (storage-only build)
cover verification items #2-#10, #12-#13; the six-service expansion adds 15
more (numbered #1-#15 in the expansion's own plan) — both sets run against
the real advisor_kb/ content, not synthetic mocks, since the KB itself is as
much the thing under test as the code that reads it.

rules_engine.evaluate_blockers/evaluate_full now take a `service` as their
first argument (the six-service delta made the engine service-aware) — every
pre-existing storage check below was updated to pass STORAGE explicitly;
none of their actual assertions changed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.condition_eval import evaluate, evaluate_safe, apply_set, AttrDict
from advisor.catalog_loader import (get_catalog, get_rules, get_mapping, get_platform_constants,
                                     AdvisorKBError, _validate_pattern)
from advisor.pattern_matcher import score
from advisor.rules_engine import evaluate_full, evaluate_blockers
from advisor.question_engine import (next_question, record_answer, is_complete,
                                      _normalize_options, _normalize_skip_if, _service_questions)
from advisor.prefill import (build_prefill, build_prefill_aks, build_prefill_vm,
                             build_prefill_postgres, build_prefill_appgw)
from advisor import recommendation as advisor_recommendation
from advisor.recommendation import build_recommendation_generic, build_redirect_response
from advisor.diagram_builder import render as render_diagram
from advisor import services as advisor_services
from advisor.composer import inventory_parser
from advisor.composer import network_planner
from advisor.composer import composition_engine
from advisor.composer import sequencer
from advisor.composer import infosec as composer_infosec
from advisor.composer import render as composer_render
from advisor.diagram_builder import render_environment as render_env_diagram
from advisor.catalog_loader import get_composer_file
from pathlib import Path

# This suite's own contract (see module docstring) is "no LLM needed" — the
# "Why this pattern" prose is the one deliberately LLM-authored piece, with a
# documented deterministic fallback if the provider is unavailable. Whatever
# AGENT_PROVIDER this DB happens to have configured live must never make this
# offline suite depend on real network reachability (a live-but-slow endpoint
# hung this suite for 60s+ with zero timeout), so call_llm is forced to raise
# immediately — exercising exactly the fallback path this suite should cover.
advisor_recommendation.prompts.call_llm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no LLM in tests"))
import re

STORAGE = "storage_account"

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAILED: {name}"
    passed += 1
    print(f"  ok: {name}")


def _storage_catalog(full_catalog):
    return {pid: p for pid, p in full_catalog.items() if p.get("service") == STORAGE}


# ── condition_eval: every distinct condition shape used across the KB ────
print("condition_eval")
check("== false", evaluate("subscription_available == false", {"subscription_available": False}))
check("== true", evaluate("immutability_required == true", {"immutability_required": True}))
check("in list (quoted membership)", evaluate("'external_system' in consumer", {"consumer": ["external_system"]}))
check("and", evaluate("performance_requirement == premium_justified and workload_duration == permanent",
                       {"performance_requirement": "premium_justified", "workload_duration": "permanent"}))
check("is empty", evaluate("performance_evidence is empty", {"performance_evidence": ""}))
check("is empty (falsy on missing)", evaluate("performance_evidence is empty", {"performance_evidence": None}))
check("is not empty", not evaluate("performance_evidence is empty", {"performance_evidence": "5000 IOPS"}))
check("contains", evaluate("capacity_estimate contains 'not sure'", {"capacity_estimate": "not sure yet"}))
check("always", evaluate("always", {}))
check("bare enum in list", evaluate("purpose in [analytics_datalake]", {"purpose": "analytics_datalake"}))
check("not in", evaluate("access_frequency not in [rare_archive, infrequent]", {"access_frequency": "frequent"}))
check("dotted attr access", evaluate("derived.lifecycle_to_archive == true",
                                      {"derived": AttrDict({"lifecycle_to_archive": True})}))
check("is defined (present)", evaluate("pattern.design.change_feed is defined",
      {"pattern": AttrDict({"design": {"change_feed": "enabled"}})}))
check("is defined (missing)", not evaluate("pattern.design.change_feed is defined",
      {"pattern": AttrDict({"design": {}})}))

ctx = {}
apply_set(ctx, "access_tier = Hot; lifecycle_policy = recommended")
check("apply_set compound", ctx == {"access_tier": "Hot", "lifecycle_policy": "recommended"})
ctx2 = {}
apply_set(ctx2, "zpa_routing_required = true")
check("apply_set bool coercion", ctx2["zpa_routing_required"] is True)

# ── catalog_loader: real KB loads cleanly ─────────────────────────────────
print("\ncatalog_loader")
catalog = get_catalog()
storage_catalog = _storage_catalog(catalog)
check("all 5 storage patterns load", set(storage_catalog.keys()) == {
    "storage_blob_private_standard", "storage_files_private_standard",
    "storage_datalake_private", "storage_archive_retention", "storage_premium_temporary",
})

# ── #2: no subscription -> halt, HALO portal ──────────────────────────────
print("\n#2 no subscription blocks immediately")
r = evaluate_blockers(STORAGE, {"subscription_available": False})
check("blocked", r["blocked"] is True)
check("HALO portal mentioned", "HALO" in r["message"])

# ── #8: sovereign classification -> blocked, platform + security ─────────
print("\n#8 sovereign data blocks, routes to platform+security")
r = evaluate_blockers(STORAGE, {"subscription_available": True, "data_classification": "restricted_sovereign"})
check("blocked", r["blocked"] is True)
check("blocker id", r["blocker_id"] == "sovereign_data")
check("mentions platform and security teams",
      "platform" in r["message"].lower() and "security" in r["message"].lower())

# ── #3: application files + AKS + SDK -> storage_blob_private_standard ───
print("\n#3 application files + AKS + SDK -> blob")
r = score(storage_catalog, {
    "purpose": "application_files", "data_shape": "unstructured_objects",
    "access_protocol": ["rest_sdk"], "consumer": ["aks"], "access_frequency": "frequent",
    "data_classification": "internal",
})
check("winner", r["winner"] == "storage_blob_private_standard")

# ── #4: shared drive + SMB -> storage_files_private_standard ─────────────
print("\n#4 shared drive + SMB -> files")
r = score(storage_catalog, {
    "purpose": "shared_drive", "data_shape": "shared_file_system",
    "access_protocol": ["smb"], "consumer": ["vm"], "access_frequency": "frequent",
    "data_classification": "internal",
})
check("winner", r["winner"] == "storage_files_private_standard")

# ── #5: analytics + Databricks -> storage_datalake_private, HNS warning ──
print("\n#5 analytics + Databricks -> datalake, HNS warning present")
r = evaluate_full(STORAGE, {
    "subscription_available": True, "purpose": "analytics_datalake",
    "data_shape": "analytics_datalake", "access_protocol": ["abfs"],
    "consumer": ["analytics_engine"], "data_classification": "internal",
    "access_frequency": "frequent", "performance_requirement": "standard_ok",
    "region": "uaenorth", "network_details_known": True,
})
check("winner", r["selection"]["winner"] == "storage_datalake_private")
check("HNS warning present", any("Hierarchical namespace" in w for w in r["warnings"]))

# ── #6: compliance retention + rarely read -> archive, LRS/GRS deviation ─
print("\n#6 compliance retention + rare access -> archive, ZRS deviation stated")
r = evaluate_full(STORAGE, {
    "subscription_available": True, "purpose": "compliance_retention",
    "data_shape": "unstructured_objects", "access_protocol": ["rest_sdk"],
    "consumer": ["application"], "data_classification": "internal",
    "access_frequency": "rare_archive", "retention_period": "7 years",
    "immutability_required": False, "performance_requirement": "standard_ok",
    "region": "uaenorth", "network_details_known": True,
})
check("winner (via override)", r["selection"]["winner"] == "storage_archive_retention")
check("deviation explicitly states ZRS/LRS/GRS",
      any("ZRS" in d and ("LRS" in d or "GRS" in d) for d in r["deviations"]))

# ── #7: premium requested, unmeasured -> downgraded to Standard, flagged ─
print("\n#7 premium unmeasured -> downgraded to Standard, flagged")
r = evaluate_full(STORAGE, {
    "subscription_available": True, "purpose": "application_files",
    "data_shape": "unstructured_objects", "access_protocol": ["rest_sdk"],
    "consumer": ["application"], "data_classification": "internal",
    "access_frequency": "frequent", "performance_requirement": "premium_justified",
    "performance_evidence": "", "workload_duration": "temporary_bounded",
    "region": "uaenorth", "network_details_known": True,
})
check("escalation flagged", any(e["id"] == "premium_unmeasured" for e in r["escalations"]))
check("downgraded to standard_ok", r["derived"]["performance_requirement"] == "standard_ok")
check("winner is blob (not premium)", r["selection"]["winner"] == "storage_blob_private_standard")

# ── #9: DNS link always required ──────────────────────────────────────────
print("\n#9 DNS link always required")
r = evaluate_full(STORAGE, {
    "subscription_available": True, "purpose": "application_files",
    "data_shape": "unstructured_objects", "access_protocol": ["rest_sdk"],
    "consumer": ["application"], "data_classification": "internal",
    "access_frequency": "frequent", "performance_requirement": "standard_ok",
    "region": "uaenorth", "network_details_known": True,
})
check("DNS mandatory warning present", any("DNS link is not optional" in w for w in r["warnings"]))

# ── #10: ZPA appears only when end-user access selected ───────────────────
print("\n#10 ZPA routing conditional on end_user_zpa consumer")
base = {
    "subscription_available": True, "purpose": "application_files",
    "data_shape": "unstructured_objects", "access_protocol": ["rest_sdk"],
    "data_classification": "internal", "access_frequency": "frequent",
    "performance_requirement": "standard_ok", "region": "uaenorth",
    "network_details_known": True,
}
r_no_zpa = evaluate_full(STORAGE, {**base, "consumer": ["application"]})
check("no ZPA when not selected", r_no_zpa["derived"]["zpa_routing_required"] is False)
r_zpa = evaluate_full(STORAGE, {**base, "consumer": ["application", "end_user_zpa"]})
check("ZPA required when end_user_zpa selected", r_zpa["derived"]["zpa_routing_required"] is True)

# ── #1: question flow starts correctly, one question at a time ──────────
print("\n#1 question flow: one question at a time, skip_if + follow_up_if + default_if_unknown")
state = {"service": STORAGE, "answers": {}, "pending_followups": []}
q = next_question(state)
check("first question is subscription_available", q["id"] == "subscription_available")
state = record_answer(state, "subscription_available", True)
q = next_question(state)
check("second question is purpose", q["id"] == "purpose")
state = record_answer(state, "purpose", "analytics_datalake")
q = next_question(state)
check("data_shape auto-set + skipped via skip_if", state["answers"].get("data_shape") == "analytics_datalake")
check("skipped question isn't re-asked", q["id"] != "data_shape")

state2 = {"service": STORAGE, "answers": {"capacity_estimate": "2 TB"}, "pending_followups": []}
state2 = record_answer(state2, "performance_requirement", "premium_justified")
q = next_question(state2)
check("follow_up_if queues workload_duration", q["id"] == "workload_duration")
state2 = record_answer(state2, "workload_duration", "temporary_bounded")
q = next_question(state2)
check("follow_up_if queues performance_evidence next", q["id"] == "performance_evidence")

state3 = {"service": STORAGE, "answers": {}, "pending_followups": []}
state3 = record_answer(state3, "data_classification", "unsure")
check("default_if_unknown applied", state3["answers"]["data_classification"] == "confidential")

# ── End-to-end: full question walk -> rules -> pattern_matcher pipeline ──
print("\nEnd-to-end: full conversation -> rules -> pattern selection")
state4 = {"service": STORAGE, "answers": {}, "pending_followups": []}
full_answers = {
    "subscription_available": True, "purpose": "application_files", "data_shape": "unstructured_objects",
    "access_protocol": ["rest_sdk"], "consumer": ["aks"], "data_classification": "internal",
    "access_frequency": "frequent", "capacity_estimate": "500 GB",
    "performance_requirement": "standard_ok", "environment": "prd", "business_unit": "Platform",
    "application_name": "MyApp", "criticality": "high", "owner_email": "a@b.com",
    "region": "uaenorth", "network_details_known": True,
}
steps = 0
while not is_complete(state4) and steps < 50:
    q = next_question(state4)
    state4 = record_answer(state4, q["id"], full_answers[q["id"]])
    steps += 1
check("flow completes", is_complete(state4))
result = evaluate_full(STORAGE, state4["answers"])
check("end-to-end pattern selection matches", result["selection"]["winner"] == "storage_blob_private_standard")
check("not blocked", not result["blocked"])

# ── prefill.py: KB-semantic -> real-form-field translation ───────────────
print("\nprefill.py: form-field translation and DNS/ZPA follow-on requests")
answers9 = {
    "subscription_available": True, "purpose": "application_files", "data_shape": "unstructured_objects",
    "access_protocol": ["rest_sdk"], "consumer": ["aks"], "data_classification": "internal",
    "access_frequency": "frequent", "capacity_estimate": "500 GB",
    "performance_requirement": "standard_ok", "environment": "prd", "business_unit": "Platform",
    "application_name": "MyApp", "criticality": "high", "owner_email": "a@b.com",
    "region": "uaenorth", "network_details_known": True, "vnet_name": "vnet-x", "subnet_name": "snet-x",
}
r9 = evaluate_full(STORAGE, answers9)
pattern9 = catalog[r9["selection"]["winner"]]
p9 = build_prefill(pattern9, answers9, r9)
check("#9 DNS always included", "dns" in [f["request_type"] for f in p9["follow_on_requests"]])
check("#10a no ZPA when not selected",
      "zpa_rnd_routing" not in [f["request_type"] for f in p9["follow_on_requests"]])
check("identity_type translated to form value 'user'", p9["fields"]["identity_type"] == "user")
check("encryption_type translated to form value 'customer_managed'",
      p9["fields"]["encryption_type"] == "customer_managed")
check("sku matches ZRS baseline", p9["fields"]["sku"] == "Standard_ZRS")

answers10 = dict(answers9, consumer=["aks", "end_user_zpa"])
r10 = evaluate_full(STORAGE, answers10)
pattern10 = catalog[r10["selection"]["winner"]]
p10 = build_prefill(pattern10, answers10, r10)
check("#10b ZPA included when end_user_zpa selected",
      "zpa_rnd_routing" in [f["request_type"] for f in p10["follow_on_requests"]])

premium = catalog["storage_premium_temporary"]
p_premium = build_prefill(premium, {"region": "uaenorth"}, {"derived": {}, "escalations": []})
check("premium sku is Premium_ZRS (matches the fixed form <option>)", p_premium["fields"]["sku"] == "Premium_ZRS")
sc_item = next(i for i in p_premium["user_must_provide"] if i["field"] == "service_class")
check("service_class vocabulary mismatch explicitly flagged, not silently guessed", "Bronze" in sc_item["why"])

archive = catalog["storage_archive_retention"]
p_archive = build_prefill(archive, {"region": "uaenorth"}, {"derived": {}, "escalations": []})
check("archive sku falls back to Standard_LRS (no single sku in its design)",
      p_archive["fields"]["sku"] == "Standard_LRS")

# ── #12: diagram_builder — no raw {PLACEHOLDER} left, block-removal rules ─
print("\n#12 diagram rendering: placeholders, ZPA + datalake block-removal")
diag_answers = {"application_name": "MyApp", "vnet_name": "vnet-x", "subnet_name": "snet-x",
                 "storage_account_name": "stgmyapp001", "environment": "prd"}
full_diag_answers = {**diag_answers, "backend": "aks", "public_hostname": "app.presight.ai", "gpu_required": False}
for pid, pattern in catalog.items():
    out = render_diagram(pattern, full_diag_answers, {"zpa_routing_required": False})
    check(f"{pid}: no raw placeholders", not re.findall(r"\{[A-Z_]+\}", out))

blob = catalog["storage_blob_private_standard"]
no_zpa = render_diagram(blob, diag_answers, {"zpa_routing_required": False})
check("ZPA subgraph stripped when not required", "ZPA" not in no_zpa and "subgraph USER" not in no_zpa)
with_zpa = render_diagram(blob, diag_answers, {"zpa_routing_required": True})
check("ZPA subgraph present when required", "ZPA" in with_zpa and "subgraph USER" in with_zpa)

datalake = catalog["storage_datalake_private"]
dfs_only = render_diagram(datalake, {**diag_answers, "access_protocol": ["abfs"]}, {})
check("datalake: blob endpoint dropped when engine only uses dfs", "PE2" not in dfs_only and "DNS2" not in dfs_only)
dfs_and_blob = render_diagram(datalake, {**diag_answers, "access_protocol": ["abfs", "rest_sdk"]}, {})
check("datalake: blob endpoint kept when engine confirmed to use both",
      "PE2" in dfs_and_blob and "DNS2" in dfs_and_blob)

malicious = render_diagram(blob, {**diag_answers, "application_name": "<script>alert(1)</script>"},
                            {"zpa_routing_required": False})
check("HTML-unsafe characters escaped in diagram output", "<script>" not in malicious)

# ═══════════════════════════════════════════════════════════════════════
# Six-service expansion — new checks (numbered per the expansion's own plan)
# ═══════════════════════════════════════════════════════════════════════

AKS, VM, POSTGRES, APPGW = "aks_cluster", "vm_create", "postgres_create", "app_gateway"

# ── schema normalization: both options/skip_if shapes -> one uniform shape ─
print("\nquestion_engine: options/skip_if schema normalization")
check("plain string list options normalize to {value,label} dicts",
      _normalize_options(["dev", "tst"]) == [{"value": "dev", "label": "Dev"},
                                              {"value": "tst", "label": "Tst"}])
check("dict-shaped options pass through unchanged",
      _normalize_options([{"value": "a", "label": "A label"}]) == [{"value": "a", "label": "A label"}])
check("bare skip_if string normalizes to {condition,set}",
      _normalize_skip_if("exposure != public_internet") == {"condition": "exposure != public_internet", "set": None})
check("dict-shaped skip_if passes through unchanged",
      _normalize_skip_if({"condition": "x == y", "set": "z = 1"}) == {"condition": "x == y", "set": "z = 1"})

# real appgw_questions.yaml exercises both shapes for real (public_hostname's
# skip_if is a bare string; environment's options are a bare list) — confirm
# question_engine loads it without crashing and the shapes come out normalized.
appgw_qs = {q["id"]: q for q in _service_questions(APPGW)}
check("appgw public_hostname skip_if normalized from bare string",
      appgw_qs["public_hostname"]["skip_if"] == {"condition": "exposure != public_internet", "set": None})
check("appgw environment options normalized from bare string list",
      {o["value"] for o in appgw_qs["environment"]["options"]} == {"dev", "tst", "uat", "prd", "snd"})

# ── #1: service selection is the literal first question, both menu + free text
print("\n#1 service selection: menu routing and free-text keyword fallback")
svc_state = {"answers": {}, "pending_followups": []}
first_q = next_question(svc_state)
check("first question (no service chosen yet) is the service-selector",
      first_q["id"] == advisor_services.SERVICE_QUESTION_ID)
check("five services offered, Key Vault and 'whole environment' excluded",
      {o["value"] for o in first_q["options"]} == {STORAGE, AKS, VM, POSTGRES, APPGW})
svc_state = record_answer(svc_state, advisor_services.SERVICE_QUESTION_ID, AKS)
check("menu selection sets state['service']", svc_state["service"] == AKS)
next_after_service = next_question(svc_state)
check("next question after service choice is that service's own first question",
      next_after_service["id"] == "subscription_available")

check("free text 'I need a cluster' routes to aks_cluster (keyword fallback, no LLM)",
      advisor_services.classify_free_text("I need a cluster") == AKS)
check("free text routes VM/database/gateway/storage too",
      advisor_services.classify_free_text("spin up a few VMs") == VM and
      advisor_services.classify_free_text("need a postgres database") == POSTGRES and
      advisor_services.classify_free_text("expose this behind a gateway") == APPGW and
      advisor_services.classify_free_text("need some blob storage") == STORAGE)
check("unrelated free text doesn't force a match", advisor_services.classify_free_text("hello there") is None)

# ── #13: malformed KB YAML fails loudly at load time, not at request time ──
print("\n#13 malformed KB file fails loudly at load, not at request time")
try:
    _validate_pattern(Path("broken.yaml"), {"id": "broken", "name": "Broken"})  # missing required keys
    raised = False
except AdvisorKBError as exc:
    raised = True
    check("error names the offending file", "broken.yaml" in str(exc.path))
    check("error names the offending/missing key", exc.key not in (None, ""))
check("malformed pattern raises AdvisorKBError, not a bare traceback", raised)
try:
    get_rules("not_a_real_service")
    unknown_raised = False
except AdvisorKBError:
    unknown_raised = True
check("get_rules() on an unknown service id raises AdvisorKBError", unknown_raised)

# ── platform_constants: loads once, shared, not restated per service ──────
print("\nplatform_constants: loads once, applies across all six services")
pc1 = get_platform_constants()
pc2 = get_platform_constants()
check("platform_constants loads (scope: all_services)", pc1.get("scope") == "all_services")
check("cached — same object on repeated calls", pc1 is pc2)
check("naming pattern present (shared by every service's real form fields)",
      "naming" in pc1 and "pattern" in pc1["naming"])
for svc in (STORAGE, AKS, VM, POSTGRES, APPGW):
    rules = get_rules(svc)
    check(f"{svc}'s own matrix doesn't restate platform_constants' naming/DNS keys",
          "naming" not in rules and "private_dns_zones" not in rules)

# ── generic execution_order: 4 new services' 6-phase matrices run cleanly ──
print("\nrules_engine: generic execution_order across all five services")
for svc in (AKS, VM, POSTGRES, APPGW):
    order = get_rules(svc)["execution_order"]
    check(f"{svc} matrix has no 'constants' phase (only storage's does)", "constants" not in order)
    r = evaluate_blockers(svc, {"subscription_available": False})
    check(f"{svc}: no-subscription blocks immediately, HALO portal", r["blocked"] and "HALO" in r["message"])
    r = evaluate_blockers(svc, {"subscription_available": True, "data_classification": "restricted_sovereign"})
    check(f"{svc}: sovereign data blocks, routes to platform+security",
          r["blocked"] and "platform" in r["message"].lower() and "security" in r["message"].lower())

# ── VM/Postgres: no real match.required signal is ever set -> always falls
# through catalog scoring to pattern_selection.default (confirmed by reading
# the KB: vm_workload_standard/postgres_flexible_private require
# workload_shape/data_shape, which no question or derivation in these two
# services' own KBs ever sets) ───────────────────────────────────────────
print("\npattern_selection: default fallback for single-pattern services")
r = evaluate_full(VM, {
    "subscription_available": True, "vm_purpose": "batch jobs", "vm_count": 2,
    "os_family": "linux", "sizing": "small", "data_disks": False,
    "access_need": ["application_only"], "availability": False, "environment": "dev",
    "data_classification": "internal", "business_unit": "Platform", "application_name": "App",
    "owner_email": "a@b.com", "criticality": "low",
})
check("VM: falls through to its only pattern (vm_workload_standard) via default",
      r["selection"]["winner"] == "vm_workload_standard")
check("VM: vm_count coerced to int by the integer question type", r["derived"]["vm_count"] == 2)

r = evaluate_full(POSTGRES, {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "managed", "size_estimate": "100 GB", "ha_requirement": "unsure",
    "rpo_rto": "1 hour", "environment": "prd", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "App", "owner_email": "a@b.com",
    "criticality": "high",
})
check("Postgres: falls through to its only pattern via default", r["selection"]["winner"] == "postgres_flexible_private")
check("#7 (postgres) prd + ha unsure -> zone_redundant", r["derived"]["ha_requirement"] == "zone_redundant")

r_dev = evaluate_full(POSTGRES, {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "managed", "size_estimate": "10 GB", "ha_requirement": "unsure",
    "rpo_rto": "1 day", "environment": "dev", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "App", "owner_email": "a@b.com",
    "criticality": "low",
})
check("#7 (postgres) dev + ha unsure -> single", r_dev["derived"]["ha_requirement"] == "single")

r_prd_single = evaluate_full(POSTGRES, {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "managed", "size_estimate": "10 GB", "ha_requirement": "single",
    "rpo_rto": "1 day", "environment": "prd", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "App", "owner_email": "a@b.com",
    "criticality": "high",
})
check("#8 (postgres) prd + single HA -> deviation surfaced",
      any("zone-redundant" in d.lower() or "production" in d.lower() for d in r_prd_single["deviations"]))

r_self_managed = evaluate_full(POSTGRES, {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "self_managed", "size_estimate": "10 GB", "ha_requirement": "single",
    "rpo_rto": "1 day", "environment": "dev", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "App", "owner_email": "a@b.com",
    "criticality": "low",
})
check("#9 (postgres) self_managed -> escalation carries a redirect to vm_workload_standard",
      any(e.get("redirect") == "vm_workload_standard" for e in r_self_managed["escalations"]))

# ── AKS: GPU vs non-GPU pattern selection, CNI warning, escalation flag ────
print("\nAKS: GPU/non-GPU pattern selection + warnings")
aks_base = {
    "subscription_available": True, "workload_description": "internal API", "node_count": "6",
    "exposure": "internal_only", "persistent_storage": False, "database_needed": False,
    "environment": "prd", "data_classification": "internal", "business_unit": "Platform",
    "application_name": "App", "owner_email": "a@b.com", "criticality": "high",
}
r_gpu = evaluate_full(AKS, {**aks_base, "gpu_required": True})
check("#3 AKS + GPU -> aks_gpu_nodepool", r_gpu["selection"]["winner"] == "aks_gpu_nodepool")
check("#3 GPU quota escalation flagged as longest lead time",
      any(e["flag"] == "GPU_QUOTA_REQUIRED" and "longest lead" in e["message"].lower()
          for e in r_gpu["escalations"]))
r_nogpu = evaluate_full(AKS, {**aks_base, "gpu_required": False})
check("#4 AKS without GPU -> aks_private_standard", r_nogpu["selection"]["winner"] == "aks_private_standard")
check("#5 AKS CNI Overlay / Pod CIDR sizing warning present",
      any("overlay" in w.lower() and "pod cidr" in w.lower() for w in r_nogpu["warnings"]))
check("aks_gpu_nodepool inherited aks_private_standard's design (design.inherits resolved)",
      "inherits" not in catalog["aks_gpu_nodepool"]["design"] and
      catalog["aks_gpu_nodepool"]["design"].get("tier") == catalog["aks_private_standard"]["design"].get("tier"))
check("aks_private_standard's design recommends Azure CNI Overlay, not classic CNI",
      catalog["aks_private_standard"]["design"]["network_plugin"] == "Azure CNI Overlay")
check("aks_gpu_nodepool inherits the Overlay recommendation too (design.inherits, not restated)",
      catalog["aks_gpu_nodepool"]["design"]["network_plugin"] == "Azure CNI Overlay")
check("AKS mapping locks network_plugin_mode=overlay",
      get_mapping(AKS)["locked_fields"]["network_plugin_mode"]["value"] == "overlay")

# ── add_service accumulation: multiple derivations must not overwrite ─────
print("\nrules_engine: add_service accumulates across escalations + derivations")
r_multi = evaluate_full(AKS, {**aks_base, "gpu_required": False, "persistent_storage": True,
                              "database_needed": True, "exposure": "public_internet"})
check("add_services accumulates storage_account + postgres_create + container_registry + app_gateway",
      set(r_multi["add_services"]) >= {"storage_account", "postgres_create", "container_registry", "app_gateway"})

# ── VM: fleet-size escalation + single-VM-HA warning ──────────────────────
print("\nVM: fleet-size escalation + single-VM availability warning")
vm_base = {
    "subscription_available": True, "vm_purpose": "batch jobs", "os_family": "linux",
    "sizing": "small", "data_disks": False, "access_need": ["application_only"],
    "environment": "dev", "data_classification": "internal", "business_unit": "Platform",
    "application_name": "App", "owner_email": "a@b.com", "criticality": "low",
}
r_fleet = evaluate_full(VM, {**vm_base, "vm_count": 25, "availability": False})
check("#6 VM count > 20 -> FLEET_SIZE_REVIEW", any(e["flag"] == "FLEET_SIZE_REVIEW" for e in r_fleet["escalations"]))
r_single_ha = evaluate_full(VM, {**vm_base, "vm_count": 1, "availability": True})
check("#7 VM single VM + availability=true -> single-point-of-failure warning",
      any("single point of failure" in w.lower() for w in r_single_ha["warnings"]))

# ── AppGW: public/internal InfoSec gate, direct-public-IP hard blocker ────
print("\nAppGW: InfoSec gate presence/absence, direct-public-IP blocker")
appgw_base = {
    "subscription_available": True, "backend": "aks", "routing_need": "single_backend",
    "environment": "prd", "data_classification": "internal", "business_unit": "Platform",
    "application_name": "App", "owner_email": "a@b.com", "criticality": "high",
}
r_public = evaluate_full(APPGW, {**appgw_base, "exposure": "public_internet"})
check("#10 AppGW public -> appgw_public_cloudflare", r_public["selection"]["winner"] == "appgw_public_cloudflare")
check("#10 AppGW public -> exactly 1 public IP", str(r_public["derived"]["public_ip_count"]) == "1")
check("#10 AppGW public -> InfoSec gate escalation present, rendered verbatim (message_ref)",
      any(e["id"] == "infosec_onboarding" and e.get("message_ref") for e in r_public["escalations"]))
infosec_msg = next(e["message_ref"] for e in r_public["escalations"] if e["id"] == "infosec_onboarding")
check("InfoSec gate message has heading/body/next_step (verbatim composer content)",
      {"heading", "body", "next_step"} <= set(infosec_msg.keys()))

r_internal = evaluate_full(APPGW, {**appgw_base, "exposure": "internal_only"})
check("#11 AppGW internal -> appgw_internal", r_internal["selection"]["winner"] == "appgw_internal")
check("#11 AppGW internal -> 0 public IPs", str(r_internal["derived"]["public_ip_count"]) == "0")
check("#11 AppGW internal -> NO InfoSec gate escalation",
      not any(e["id"] == "infosec_onboarding" for e in r_internal["escalations"]))

r_direct_ip = evaluate_blockers(APPGW, {**appgw_base, "exposure": "public_internet",
                                        "user_requests_direct_public_ip": True})
check("#12 AppGW public + direct-public-IP request -> hard blocker",
      r_direct_ip["blocked"] and r_direct_ip["blocker_id"] == "public_without_cloudflare")

r_unsure = evaluate_full(APPGW, {**appgw_base, "exposure": "unsure"})
check("AppGW exposure=unsure -> tiebreak question asked, not guessed",
      r_unsure["selection"]["outcome"] == "ask_tiebreak")

# ── #14: every private-endpoint recommendation lists the DNS link as required
print("\n#14 DNS link listed as required wherever a private endpoint is recommended")
pg_mapping = get_mapping(POSTGRES)
check("postgres mapping's DNS follow-on is always_required (private endpoint always used)",
      any(f.get("request_type") == "dns" and f.get("always_required")
          for f in pg_mapping["follow_on_requests"]))
appgw_mapping = get_mapping(APPGW)
check("appgw mapping's DNS follow-on (private backend A record) is always_required",
      any(f.get("request_type") == "dns" and f.get("always_required")
          for f in appgw_mapping["follow_on_requests"]))

# ── evaluate_safe: unparseable KB condition strings fail closed, not loudly ─
print("\ncondition_eval.evaluate_safe: malformed include_if strings fail closed")
check("malformed condition (real mapping-file example) treated as False, not raised",
      evaluate_safe("egress_destinations specified", {}) is False)
check("malformed condition referencing a never-set field also fails closed",
      evaluate_safe("engineer_access_needed == true", {}) is False)
check("well-formed conditions still evaluate normally through evaluate_safe",
      evaluate_safe("exposure == public_internet", {"exposure": "public_internet"}) is True)

# ── prefill.py: AKS/VM real form-field translations (semantic-vocab check) ─
print("\nprefill.py: AKS field translations against real form markup")
aks_pattern = catalog["aks_private_standard"]
aks_answers = {
    "subscription_available": True, "workload_description": "internal API", "node_count": "6 nodes",
    "gpu_required": False, "exposure": "internal_only", "persistent_storage": False,
    "database_needed": False, "environment": "prd", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "MyApp", "owner_email": "a@b.com", "criticality": "high",
}
aks_result = evaluate_full(AKS, aks_answers)
p_aks = build_prefill_aks(catalog[aks_result["selection"]["winner"]], aks_answers, aks_result)
check("AKS: tag/name source uses real form field 'project', not nonexistent 'application_name'",
      p_aks["fields"].get("project") == "MyApp" and "application_name" not in p_aks["fields"])
check("AKS: node_count parsed to a leading integer from free text", p_aks["fields"]["node_count"] == "6")
check("AKS: cmk_encryption checkbox set (real field, locked_fields: encryption CMK)",
      p_aks["fields"]["cmk_encryption"] is True)
check("AKS: node_pool_name flagged (required by form, missing from KB's own user_must_provide)",
      any(i["field"] == "node_pool_name" for i in p_aks["user_must_provide"]))
check("AKS: zpa_rnd_access flagged (same gap)",
      any(i["field"] == "zpa_rnd_access" for i in p_aks["user_must_provide"]))
check("AKS: no gpu_node_pool field ever written (doesn't exist on the form)",
      "gpu_node_pool" not in p_aks["fields"])

aks_gpu_answers = {**aks_answers, "gpu_required": True}
aks_gpu_result = evaluate_full(AKS, aks_gpu_answers)
p_aks_gpu = build_prefill_aks(catalog[aks_gpu_result["selection"]["winner"]], aks_gpu_answers, aks_gpu_result)
check("AKS+GPU: gpu_node_pool surfaced as an informational checklist note, not a real field",
      any(i["field"] == "gpu_node_pool" and i["blocking"] is False for i in p_aks_gpu["user_must_provide"]))

print("\nprefill.py: VM field translations against real form markup")
vm_pattern_id = "vm_workload_standard"
vm_answers = {
    "subscription_available": True, "vm_purpose": "batch jobs", "vm_count": 3,
    "os_family": "windows", "sizing": "small", "data_disks": False,
    "access_need": ["application_only"], "availability": True, "environment": "prd",
    "data_classification": "internal", "business_unit": "Platform", "application_name": "MyApp",
    "owner_email": "a@b.com", "criticality": "high",
}
vm_result = evaluate_full(VM, vm_answers)
p_vm = build_prefill_vm(catalog[vm_result["selection"]["winner"]], vm_answers, vm_result)
check("VM: Windows auth_mode translated to real form option 'password' (not admin_password_at_deploy)",
      p_vm["fields"]["auth_mode"] == "password")
check("VM: zones prefilled '1,2,3' from availability=true", p_vm["fields"]["zones"] == "1,2,3")
check("VM: project field used (not application_name)",
      p_vm["fields"].get("project") == "MyApp" and "application_name" not in p_vm["fields"])
check("VM: os_image flagged as user-must-provide (no curated image list exists)",
      any(i["field"] == "os_image" for i in p_vm["user_must_provide"]))
check("VM: vm_base_name never invented (KB's own user_must_provide already covers it, unchanged)",
      "vm_base_name" not in p_vm["fields"])

vm_linux_answers = {**vm_answers, "os_family": "linux", "availability": False}
vm_linux_result = evaluate_full(VM, vm_linux_answers)
p_vm_linux = build_prefill_vm(catalog[vm_linux_result["selection"]["winner"]], vm_linux_answers, vm_linux_result)
check("VM: Linux auth_mode translates to 'ssh_key'", p_vm_linux["fields"]["auth_mode"] == "ssh_key")
check("VM: zones left unset when availability isn't true", "zones" not in p_vm_linux["fields"])

# ── diagram_builder: new {BASE}/{BACKEND}/{HOSTNAME} placeholders ─────────
print("\ndiagram_builder: BASE/BACKEND/HOSTNAME placeholders, GPU node strip")
vm_diag = render_diagram(catalog["vm_workload_standard"], {"application_name": "MyApp", "vnet_name": "vnet-x"}, {})
check("vm diagram: {BASE} substituted illustratively (not the real vm_base_name field)", "myapp" in vm_diag.lower())
appgw_pub_diag = render_diagram(catalog["appgw_public_cloudflare"],
                                 {"backend": "aks", "public_hostname": "app.presight.ai"}, {})
check("appgw public diagram: {HOSTNAME} substituted", "app.presight.ai" in appgw_pub_diag)
check("appgw public diagram: {BACKEND} substituted with backend label", "AKS cluster" in appgw_pub_diag)

aks_diag_gpu = render_diagram(catalog["aks_private_standard"], {"vnet_name": "vnet-x", "gpu_required": True}, {})
check("AKS diagram: GPU node kept when gpu_required=true", 'GPU["GPU node pool' in aks_diag_gpu)
aks_diag_nogpu = render_diagram(catalog["aks_private_standard"], {"vnet_name": "vnet-x", "gpu_required": False}, {})
check("AKS diagram: GPU node stripped when gpu_required=false",
      'GPU["GPU node pool' not in aks_diag_nogpu and "SN --- GPU" not in aks_diag_nogpu
      and "class GPU warn" not in aks_diag_nogpu)

# ── prefill.py: Postgres/AppGW -> RequestType.OTHER (no dedicated type yet) ─
print("\nprefill.py: Postgres/AppGW compose into RequestType.OTHER's description")
pg_answers = {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "managed", "size_estimate": "100 GB, ~200 connections", "ha_requirement": "unsure",
    "rpo_rto": "1 hour RPO, 4 hour RTO", "environment": "prd", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "MyApp", "owner_email": "a@b.com", "criticality": "high",
}
pg_result = evaluate_full(POSTGRES, pg_answers)
p_pg = build_prefill_postgres(catalog[pg_result["selection"]["winner"]], pg_answers, pg_result)
check("Postgres: request_type is 'other' (no dedicated RequestType yet)", p_pg["request_type"] == "other")
check("Postgres: description composed (no per-field prefill possible on this form)",
      "description" in p_pg["fields"] and len(p_pg["fields"]["description"]) > 100)
check("Postgres: description states the dedicated-type-coming note",
      "doesn't have a dedicated request type" in p_pg["fields"]["description"])
check("Postgres: description surfaces the derived HA decision (prd+unsure->zone_redundant)",
      "zone_redundant" in p_pg["fields"]["description"])
check("Postgres: description includes the full user_must_provide checklist inline",
      "server_name" in p_pg["fields"]["description"] and "key_vault_name" in p_pg["fields"]["description"])
check("Postgres: priority derived high for prd", p_pg["fields"]["priority"] == "high")
check("Postgres: no fields invented beyond description/priority (RequestType.OTHER's only two fields)",
      set(p_pg["fields"].keys()) == {"description", "priority"})

appgw_answers = {
    "subscription_available": True, "backend": "aks", "routing_need": "single_backend",
    "public_hostname": "app.presight.ai", "audience": "internal staff, low volume",
    "exposure": "public_internet", "environment": "prd", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "MyApp", "owner_email": "a@b.com", "criticality": "high",
}
appgw_result = evaluate_full(APPGW, appgw_answers)
p_appgw = build_prefill_appgw(catalog[appgw_result["selection"]["winner"]], appgw_answers, appgw_result)
check("AppGW: request_type is 'other' (no dedicated RequestType yet)", p_appgw["request_type"] == "other")
check("AppGW: description surfaces InfoSec onboarding requirement", "InfoSec" in p_appgw["fields"]["description"])
check("AppGW: description surfaces the public hostname", "app.presight.ai" in p_appgw["fields"]["description"])
check("AppGW: priority derived high for prd", p_appgw["fields"]["priority"] == "high")

# ── recommendation.py: generic builder + redirect response ────────────────
print("\nrecommendation.py: generic builder (AKS) + redirect response (Postgres self_managed)")
rec_aks = build_recommendation_generic(catalog[aks_result["selection"]["winner"]], aks_answers,
                                        aks_result, p_aks)
check("generic recommendation: security_floor becomes the settings table",
      any(r["setting"] == "Private Api Server" for r in rec_aks["table_rows"]))
check("generic recommendation: raw design dict passed through, not flattened away",
      "node_pools" in rec_aks["design"])
check("generic recommendation: required_requests always shown, condition surfaced as a caveat",
      any(r.get("condition_note", "").startswith("Applies when:") for r in rec_aks["requests"]))
check("generic recommendation: add_services carried through", "add_services" in rec_aks)

redirect_result = evaluate_full(POSTGRES, {
    "subscription_available": True, "purpose": "app database", "consumer": ["aks"],
    "managed_preference": "self_managed", "size_estimate": "10 GB", "ha_requirement": "single",
    "rpo_rto": "1 day", "environment": "dev", "data_classification": "internal",
    "business_unit": "Platform", "application_name": "App", "owner_email": "a@b.com", "criticality": "low",
})
redirect_answers = {"managed_preference": "self_managed", "purpose": "app database"}
redirect_resp = build_redirect_response(redirect_result, catalog, redirect_answers)
check("redirect response targets vm_workload_standard", redirect_resp["target_pattern_id"] == "vm_workload_standard")
check("redirect response never fabricates VM-shaped fields — only summary + restart hint",
      set(redirect_resp.keys()) == {"redirect", "target_pattern_id", "target_pattern_name",
                                     "target_pattern_summary", "message", "restart_hint", "captured_so_far"})

# ── Phase 3 environment composer: inventory_parser + network_planner ──────
# Arithmetic-focused checks only (verification items #1, #3-5, #9, #15-16,
# #18, #23-25 of the composer's 27-item spec). Reproduces both
# advisor_kb/composer/network_sizing.yaml canonical_examples exactly, plus
# the strictly-greater-than-75% boundary and the two distinct AKS sizing
# formulas. Full cross-service/InfoSec/sequencer checks land in a later
# stage once composition_engine.py/sequencer.py/infosec.py exist.
print("\ncomposer/inventory_parser.py + composer/network_planner.py")

parsed_canonical = inventory_parser.parse_inventory(
    "10 VMs, 1 AKS cluster, 1 managed PostgreSQL. The application will be publicly hosted.")
check("inventory_parser: canonical positive text parses to 10/1/1/0/0",
      (parsed_canonical["vm_count"], parsed_canonical["aks_count"],
       parsed_canonical["postgres_count"], parsed_canonical["storage_count"],
       parsed_canonical["appgw_count"]) == (10, 1, 1, 0, 0))

_positive_answers = {"vm_count": 10, "aks_count": 1, "postgres_count": 1,
                      "storage_count": 1, "appgw_count": 0, "_aks_node_count": 6}
_positive_inferred = {"keyvault_premium_private", "container_registry", "appgw_public_cloudflare"}
positive_plan = network_planner.build_network_plan(_positive_answers, _positive_inferred)

check("network_planner positive: subnet sizes match worked_example.md exactly",
      [(s["id"], s["size"], s["total"]) for s in positive_plan["subnets"]] ==
      [("snet_appgw", "/24", 256), ("snet_aks", "/26", 64),
       ("snet_workload", "/27", 32), ("snet_pe", "/27", 32)])
check("network_planner positive: arithmetic is 256+64+32+32=384",
      positive_plan["arithmetic_terms"] == [256, 64, 32, 32] and positive_plan["arithmetic_sum"] == 384)
check("network_planner positive: VNET is /23 (512), 75.0% allocated, 128 spare",
      (positive_plan["vnet_size"], positive_plan["capacity"], positive_plan["utilisation_pct"],
       positive_plan["spare"]) == ("/23", 512, 75.0, 128))
check("network_planner positive: 75.0% does NOT trip the flag (strictly_greater, not >=)",
      positive_plan["flag_tripped"] is False)
check("network_planner positive: Pod CIDR is a separate field, never a subnet row",
      positive_plan["pod_cidr"]["cidr"] == "10.244.0.0/16"
      and positive_plan["pod_cidr"]["is_subnet"] is False
      and all(s["id"] != "pod_cidr" for s in positive_plan["subnets"])
      and "10.244" not in str(positive_plan["arithmetic_terms"]))
check("network_planner positive: 4 private endpoints with correct DNS zones",
      [(e["service"], e["dns_zone"]) for e in positive_plan["private_endpoints"]] == [
          ("PostgreSQL Flexible Server", "privatelink.postgres.database.azure.com"),
          ("Key Vault", "privatelink.vaultcore.azure.net"),
          ("Container Registry", "privatelink.azurecr.io"),
          ("Storage Account", "privatelink.blob.core.windows.net"),
      ])

_negative_answers = dict(_positive_answers)
_negative_inferred = {"keyvault_premium_private", "container_registry"}  # no appgw: internal_only
negative_plan = network_planner.build_network_plan(_negative_answers, _negative_inferred)

check("network_planner negative (internal_only): snet-appgw absent",
      all(s["id"] != "snet_appgw" for s in negative_plan["subnets"]))
check("network_planner negative: arithmetic is 64+32+32=128",
      negative_plan["arithmetic_terms"] == [64, 32, 32] and negative_plan["arithmetic_sum"] == 128)
check("network_planner negative: VNET is /24 (256), not /23 not /22",
      negative_plan["vnet_size"] == "/24" and negative_plan["capacity"] == 256)
check("network_planner negative: Pod CIDR still stated (exposure doesn't affect it)",
      negative_plan["pod_cidr"] is not None
      and negative_plan["pod_cidr"]["cidr"] == "10.244.0.0/16")

aks_snet = next(s for s in positive_plan["subnets"] if s["id"] == "snet_aks")
check("AKS sizing: bucket lookup for 6 nodes gives /26 (not the actual-count prose formula)",
      aks_snet["size"] == "/26" and aks_snet["total"] == 64)
check("AKS sizing: prose headroom uses the DIFFERENT actual-count formula (6+2+5=13, matches worked_example)",
      aks_snet["actual_surge"] == 2 and aks_snet["actual_min_addresses"] == 13)
check("AKS sizing basis text never explains the subnet in terms of pods",
      "pod" not in aks_snet["basis"].lower())
_bucket_row_10 = network_planner._bucket_lookup(
    network_planner._subnets_by_id()["snet_aks"]["sizing_table"], 10)
check("AKS sizing: bucket table's OWN min_addresses (18 for 'up to 10') is a separate figure "
      "from the actual-count prose (13) — not the same computation",
      _bucket_row_10["min_addresses"] == 18 and aks_snet["actual_min_addresses"] != 18)

pe_snet = next(s for s in positive_plan["subnets"] if s["id"] == "snet_pe")
check("snet_pe sizing: recommended_default /27 overrides the naive 'up to 10 -> /28' bucket lookup",
      pe_snet["size"] == "/27" and pe_snet["total"] == 32)

# 385/512 = 75.195...% -> strictly > 75, must trip (one address more than the
# canonical 384/512 = 75.0% case above, which must NOT trip).
_just_over = network_planner.compute_vnet_plan([{"total": 257}, {"total": 64},
                                                  {"total": 32}, {"total": 32}])
check("Utilisation boundary: 75.195% (one address over the canonical 75.0% case) DOES trip the flag",
      _just_over["utilisation_pct"] > 75 and _just_over["flag_tripped"] is True)

# ── Phase 3: composition_engine.py + sequencer.py + infosec.py ────────────
# Cross-service rule evaluation, wave labelling/RequestType.OTHER mapping,
# exposure analysis, InfoSec brief drafting. Covers verification items #6-8,
# #10-14, #17, #19-22, #26 of the composer's 27-item spec.
print("\ncomposer/composition_engine.py + sequencer.py + infosec.py")

_env_positive = {
    "subscription_available": True, "environment": "dev", "data_classification": "confidential",
    "vm_count": 10, "aks_count": 1, "postgres_count": 1, "storage_count": 1, "appgw_count": 0,
    "aks_scale": "6", "gpu_required": False, "database_criticality": "single",
    "exposure": "public_internet", "public_details": "app.presight.ai, public customers",
    "business_unit": "Platform", "application_name": "MyApp", "owner_email": "a@b.com",
    "criticality": "high", "target_date": "Q3", "_resolved_asks": ["storage_for_aks"],
}
_env_negative = dict(_env_positive)
_env_negative["exposure"] = "internal_only"

pos_result = composition_engine.evaluate_full(_env_positive)
neg_result = composition_engine.evaluate_full(_env_negative)

check("composition_engine positive: exactly 3 inferred components, in the KB's own order",
      [c["id"] for c in pos_result["components"]["inferred"]] ==
      ["keyvault_premium_private", "container_registry", "appgw_public_cloudflare"])
check("composition_engine: snet_pe/pe_subnet is never listed as an 'inferred component' "
      "(worked_example.md's Components table has exactly 3 rows, not 4 — a subnet isn't a "
      "service the user recognizes as new work the way a Key Vault is)",
      all(c["id"] != "snet_pe" for c in pos_result["components"]["inferred"]))
check("composition_engine: storage_for_aks pending_ask resolved once _resolved_asks includes it",
      pos_result["components"]["pending_ask"] is None)

check("composition_engine: storage_for_aks surfaces as pending_ask when aks>0, storage==0, unresolved",
      composition_engine.infer_missing_components(
          {"aks_count": 1, "storage_count": 0, "_resolved_asks": []})["pending_ask"]["id"] == "storage_for_aks")
check("composition_engine: same ask does NOT re-fire once resolved, even though storage_count is still 0 "
      "(a 'no' answer leaves the count at 0 — only the resolved-marker distinguishes it from 'never asked')",
      composition_engine.infer_missing_components(
          {"aks_count": 1, "storage_count": 0, "_resolved_asks": ["storage_for_aks"]})["pending_ask"] is None)

check("exposure analysis positive: public_application, with infosec_gate.yaml's message_ref attached",
      pos_result["exposure"]["id"] == "public_application" and "user_message" in pos_result["exposure"]["message_ref"])
check("exposure analysis negative: fully_private, no message_ref",
      neg_result["exposure"]["id"] == "fully_private" and "message_ref" not in neg_result["exposure"])

check("environment_warnings positive: both new Overlay warnings plus surge-headroom present",
      {"aks_overlay_sizing", "aks_surge_headroom", "pod_cidr_non_overlap"} <= {w["id"] for w in pos_result["warnings"]})
check("environment_warnings positive: cert_ownership fires for an INFERRED AppGW "
      "(appgw_count stayed 0 in the raw answers — only inference makes it present)",
      "cert_ownership" in {w["id"] for w in pos_result["warnings"]})
check("environment_warnings negative: no cloudflare/cert warnings when exposure is internal_only",
      {"cert_ownership", "cloudflare_range_drift"}.isdisjoint({w["id"] for w in neg_result["warnings"]}))

check("environment_deviations: oversized_request only fires when computed VNET is larger than /21 "
      "(neither canonical example needs a split — both are /23 or /24)",
      pos_result["deviations"] == [] and neg_result["deviations"] == [])
_huge_plan = {"vnet_size": "/20"}
check("environment_deviations: oversized_request DOES fire for a /20 (larger than /21)",
      any(d["id"] == "oversized_request" for d in
          composition_engine.environment_deviations({}, _huge_plan)))
check("environment_deviations: oversized_request does NOT fire for exactly /21 (the guard, not '/21 or larger')",
      not any(d["id"] == "oversized_request" for d in
              composition_engine.environment_deviations({}, {"vnet_size": "/21"})))

pos_waves = sequencer.build_waves(_env_positive)
neg_waves = sequencer.build_waves(_env_negative)
check("sequencer positive: InfoSec onboarding sits in wave 0",
      any(r["label"] == "InfoSec public exposure onboarding" for w in pos_waves if w["wave"] == 0
          for r in w["requests"]))
check("sequencer positive: wave table shows real service names, never bare 'Other'",
      all(r["label"] != "Other" for w in pos_waves for r in w["requests"]))
check("sequencer: postgres_create/app_gateway map to RequestType.OTHER with a secondary "
      "'no dedicated request type yet' note, label stays the semantic name",
      all(r["submittable_request_type"] == "other" and r["secondary_note"]
          for w in pos_waves for r in w["requests"]
          if r["type"] in ("postgres_create", "app_gateway")))
check("sequencer: private_endpoint rows note they're part of their parent resource's own deploy",
      all("parent resource" in r["secondary_note"]
          for w in pos_waves for r in w["requests"] if r["type"] == "private_endpoint"))
check("sequencer negative (internal_only): wave 6 (public front door) entirely absent",
      all(w["wave"] != 6 for w in neg_waves))
check("sequencer negative: no InfoSec onboarding request anywhere",
      all(r["label"] != "InfoSec public exposure onboarding" for w in neg_waves for r in w["requests"]))
check("sequencer positive: critical path names InfoSec -> Cloudflare DNS",
      "InfoSec" in sequencer.critical_path(_env_positive) and "Cloudflare" in sequencer.critical_path(_env_positive))
check("sequencer negative: critical path has no InfoSec dependency",
      "InfoSec" not in sequencer.critical_path(_env_negative))

check("infosec.gate_fires: true only for public_internet exposure",
      composer_infosec.gate_fires(_env_positive) is True
      and composer_infosec.gate_fires(_env_negative) is False)
brief = composer_infosec.draft_brief(_env_positive)
exposure_section = next(s for s in brief["sections"] if s["heading"] == "Requested exposure")
_hostname_field = next(f for f in exposure_section["fields"] if f["label"] == "Public hostname(s)")
_audience_field = next(f for f in exposure_section["fields"] if f["label"] == "Expected audience")
check("infosec.draft_brief: public_details free text is split into hostname + audience "
      "(the KB's brief template wants them separate; the question bank asks one combined field)",
      _hostname_field["value"] == "app.presight.ai" and _audience_field["value"] == "public customers")
check("infosec.draft_brief: a field the intake never asks (pii_present) renders an explicit "
      "'not yet provided' placeholder, never invented and never silently dropped",
      any(f["value"] == "(not yet provided — confirm with the requester)"
          for s in brief["sections"] for f in s.get("fields", [])))

# ── Phase 3: full render + diagram integration, arithmetic integrity, ─────
# ── and regression checks (remaining items of the composer's 27-item spec) ─
# Covers #9-10, #14-15, #17, #21, #23-27. Items #23 is scoped honestly in
# its own check below: this offline suite (prompts.call_llm forced to raise,
# see the module-level monkeypatch above) can only exercise the
# DETERMINISTIC renderer's output against network_planner's own structured
# numbers — that comparison is the one that must never drift, and it's the
# only one reachable without a real LLM in an offline suite.
print("\ncomposer: full render + diagram integration, arithmetic integrity, regressions")

pos_waves = sequencer.build_waves(_env_positive)
pos_cp = sequencer.critical_path(_env_positive)
pos_pm = sequencer.parallelism_message()
pos_brief = composer_infosec.draft_brief(_env_positive)
pos_full = composer_render.render_full(_env_positive, pos_result, pos_waves, pos_cp, pos_pm, pos_brief)

neg_waves = sequencer.build_waves(_env_negative)
neg_full = composer_render.render_full(
    _env_negative, neg_result, neg_waves, sequencer.critical_path(_env_negative), pos_pm, None)

check("render positive: exactly one public IP stated explicitly, on the Application Gateway",
      pos_full["public_access"]["public_ip_count"] == 1
      and pos_full["public_access"]["public_ip_location"] == "Application Gateway")
check("render positive: 75.0% utilisation shows NO next-size-up caveat",
      pos_full["network_plan"]["tight_fit_caveat"] is None)
_pos_body_flat = " ".join((pos_full["public_access"] or {"body": ""})["body"].split())
check("render positive: InfoSec section present, explains the architecture (Cloudflare/WAF) "
      "before the process (InfoSec onboarding), citing the prior origin-bypass finding exactly once",
      pos_full["public_access"] is not None
      and _pos_body_flat.index("Cloudflare") < _pos_body_flat.index("InfoSec onboarding")
      and _pos_body_flat.count("bypassing the WAF") == 1)
check("render negative: no InfoSec/public-access section at all", neg_full["public_access"] is None)
check("render negative: zero public IPs — no public_access block to state a count from",
      neg_full["public_access"] is None)

diagram_pos = render_env_diagram(_env_positive, pos_result)
diagram_neg = render_env_diagram(_env_negative, neg_result)
check("diagram positive: Cloudflare/NET subgraph present", "Cloudflare" in diagram_pos and "subgraph NET" in diagram_pos)
check("diagram positive: AKS node label says Overlay, not bare 'Azure CNI'",
      "Azure CNI Overlay" in diagram_pos)
check("diagram negative: NET/Cloudflare subgraph omitted entirely, no orphan CF/AGW node refs",
      "subgraph NET" not in diagram_neg and "Cloudflare" not in diagram_neg
      and "CF ==>" not in diagram_neg and "AGW -->" not in diagram_neg)
check("diagram negative: no unsubstituted {TOKEN} placeholders leaked through",
      "{ENV}" not in diagram_neg and "{VNET_CIDR}" not in diagram_neg)

# Arithmetic integrity: the deterministic renderer's numbers must match
# network_planner's own structured output byte-for-byte — never re-derived.
_plan = pos_result["network_plan"]
check("arithmetic integrity: rendered arithmetic line reproduces the planner's own terms/sum/size/pct/spare exactly",
      pos_full["network_plan"]["arithmetic_line"] ==
      f"{' + '.join(str(t) for t in _plan['arithmetic_terms'])} = {_plan['arithmetic_sum']} addresses, "
      f"which fits a {_plan['vnet_size']} ({_plan['capacity']}) at {_plan['utilisation_pct']}% allocated, "
      f"leaving {_plan['spare']} spare.")
check("arithmetic integrity: rendered subnet sizes match the planner's subnet list exactly, in order",
      [s["size"] for s in pos_full["network_plan"]["subnet_rows"]] == [s["size"] for s in _plan["subnets"]])

_sizing_yaml = get_composer_file("network_sizing.yaml")
_pub_example = _sizing_yaml["canonical_examples"]["public_environment"]
_int_example = _sizing_yaml["canonical_examples"]["internal_only_environment"]
check("both canonical_examples in network_sizing.yaml are reproduced exactly (public)",
      _plan["arithmetic_sum"] == int(_pub_example["arithmetic"].split("=")[1].strip())
      and _plan["vnet_size"] == _pub_example["vnet"]
      and _plan["utilisation_pct"] == _pub_example["utilisation_percent"]
      and _plan["spare"] == _pub_example["spare"])
_neg_plan = neg_result["network_plan"]
check("both canonical_examples in network_sizing.yaml are reproduced exactly (internal_only)",
      _neg_plan["arithmetic_sum"] == int(_int_example["arithmetic"].split("=")[1].strip())
      and _neg_plan["vnet_size"] == _int_example["vnet"]
      and _neg_plan["utilisation_pct"] == _int_example["utilisation_percent"]
      and _neg_plan["spare"] == _int_example["spare"])

# advisor_recommendation.prompts.call_llm is already forced to raise at the
# top of this suite — confirming the environment renderer's summary still
# renders (from the deterministic fallback) rather than propagating the
# LLM failure is exactly the "never gates" guarantee this forced failure
# is meant to prove.
try:
    _llm_failure_summary = composer_render.render_summary(
        _env_positive, pos_result["network_plan"], pos_result["exposure"])
    _llm_fallback_ok = bool(_llm_failure_summary)
except Exception:
    _llm_fallback_ok = False
check("forced LLM failure: the environment summary still renders from the deterministic path",
      _llm_fallback_ok)

_rendered_text = " ".join(str(v) for v in [
    pos_full["summary"], pos_full["network_plan"]["arithmetic_line"],
    pos_full["network_plan"]["pod_cidr_paragraph"] or "",
    *[s["basis"] for s in pos_full["network_plan"]["subnet_rows"]],
    *[w["message"] for w in pos_result["warnings"]],
]).lower()
check("regression: rendered output never says 'per pod', 'one ip per pod', or explains the AKS "
      "subnet as 'larger than the node count' (the classic-CNI framing this KB was corrected away from)",
      "per pod" not in _rendered_text and "one ip per pod" not in _rendered_text
      and "larger than the node count" not in _rendered_text)

import subprocess
_repo_root = Path(__file__).resolve().parent.parent
_grep = subprocess.run(["grep", "-rn", "aks_cni_sizing", str(_repo_root / "advisor_kb"),
                        str(_repo_root / "advisor")], capture_output=True, text=True)
_grep_lines = [l for l in _grep.stdout.splitlines() if l.strip()]
# Only acceptable surviving references are inside a `change:`/changelog
# narrative sentence (past tense, describing what it used to be) — never a
# live `id:`/`when:`/`warning:` key, and never in any .py file at all. A
# silently-renamed warning that never fires again is the failure mode this
# check exists to catch.
_live_reference_patterns = ("id: aks_cni_sizing", "id:aks_cni_sizing", "'aks_cni_sizing'", '"aks_cni_sizing"')
_bad_hits = [l for l in _grep_lines
             if l.rsplit(":", 1)[0].endswith(".py") or any(p in l for p in _live_reference_patterns)]
check("regression: every repo reference to the retired 'aks_cni_sizing' warning id is gone "
      "except its own changelog narrative mention (a silently-renamed warning that never "
      "fires is the failure mode this check exists to catch)",
      len(_grep_lines) > 0 and len(_bad_hits) == 0)

print(f"\n{passed} checks passed.")
