"""
Assert-based checks for the AI Architecture Advisor's pure logic — no Flask
app, no DB, no LLM needed. Run: python scripts/test_advisor_validation.py

Mirrors scripts/test_storage_validation.py / test_resourcegraph_validation.py's
style (assert-based, no pytest). Covers verification items #2, #3, #4, #5,
#6, #7, #8, #9, #10 from the AI Architecture Advisor build (see the session's
plan) directly against the real advisor_kb/ content — not synthetic mocks —
since the KB itself is the thing under test as much as the code that reads it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.condition_eval import evaluate, apply_set, AttrDict
from advisor.catalog_loader import get_catalog
from advisor.pattern_matcher import score
from advisor.rules_engine import evaluate_full, evaluate_blockers
from advisor.question_engine import next_question, record_answer, is_complete
from advisor.prefill import build_prefill
from advisor.diagram_builder import render as render_diagram
import re

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAILED: {name}"
    passed += 1
    print(f"  ok: {name}")


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
check("all 5 patterns load", set(catalog.keys()) == {
    "storage_blob_private_standard", "storage_files_private_standard",
    "storage_datalake_private", "storage_archive_retention", "storage_premium_temporary",
})

# ── #2: no subscription -> halt, HALO portal ──────────────────────────────
print("\n#2 no subscription blocks immediately")
r = evaluate_blockers({"subscription_available": False})
check("blocked", r["blocked"] is True)
check("HALO portal mentioned", "HALO" in r["message"])

# ── #8: sovereign classification -> blocked, platform + security ─────────
print("\n#8 sovereign data blocks, routes to platform+security")
r = evaluate_blockers({"subscription_available": True, "data_classification": "restricted_sovereign"})
check("blocked", r["blocked"] is True)
check("blocker id", r["blocker_id"] == "sovereign_data")
check("mentions platform and security teams",
      "platform" in r["message"].lower() and "security" in r["message"].lower())

# ── #3: application files + AKS + SDK -> storage_blob_private_standard ───
print("\n#3 application files + AKS + SDK -> blob")
r = score(catalog, {
    "purpose": "application_files", "data_shape": "unstructured_objects",
    "access_protocol": ["rest_sdk"], "consumer": ["aks"], "access_frequency": "frequent",
    "data_classification": "internal",
})
check("winner", r["winner"] == "storage_blob_private_standard")

# ── #4: shared drive + SMB -> storage_files_private_standard ─────────────
print("\n#4 shared drive + SMB -> files")
r = score(catalog, {
    "purpose": "shared_drive", "data_shape": "shared_file_system",
    "access_protocol": ["smb"], "consumer": ["vm"], "access_frequency": "frequent",
    "data_classification": "internal",
})
check("winner", r["winner"] == "storage_files_private_standard")

# ── #5: analytics + Databricks -> storage_datalake_private, HNS warning ──
print("\n#5 analytics + Databricks -> datalake, HNS warning present")
r = evaluate_full({
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
r = evaluate_full({
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
r = evaluate_full({
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
r = evaluate_full({
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
r_no_zpa = evaluate_full({**base, "consumer": ["application"]})
check("no ZPA when not selected", r_no_zpa["derived"]["zpa_routing_required"] is False)
r_zpa = evaluate_full({**base, "consumer": ["application", "end_user_zpa"]})
check("ZPA required when end_user_zpa selected", r_zpa["derived"]["zpa_routing_required"] is True)

# ── #1: question flow starts correctly, one question at a time ──────────
print("\n#1 question flow: one question at a time, skip_if + follow_up_if + default_if_unknown")
state = {"answers": {}, "pending_followups": []}
q = next_question(state)
check("first question is subscription_available", q["id"] == "subscription_available")
state = record_answer(state, "subscription_available", True)
q = next_question(state)
check("second question is purpose", q["id"] == "purpose")
state = record_answer(state, "purpose", "analytics_datalake")
q = next_question(state)
check("data_shape auto-set + skipped via skip_if", state["answers"].get("data_shape") == "analytics_datalake")
check("skipped question isn't re-asked", q["id"] != "data_shape")

state2 = {"answers": {"capacity_estimate": "2 TB"}, "pending_followups": []}
state2 = record_answer(state2, "performance_requirement", "premium_justified")
q = next_question(state2)
check("follow_up_if queues workload_duration", q["id"] == "workload_duration")
state2 = record_answer(state2, "workload_duration", "temporary_bounded")
q = next_question(state2)
check("follow_up_if queues performance_evidence next", q["id"] == "performance_evidence")

state3 = {"answers": {}, "pending_followups": []}
state3 = record_answer(state3, "data_classification", "unsure")
check("default_if_unknown applied", state3["answers"]["data_classification"] == "confidential")

# ── End-to-end: full question walk -> rules -> pattern_matcher pipeline ──
print("\nEnd-to-end: full conversation -> rules -> pattern selection")
state4 = {"answers": {}, "pending_followups": []}
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
result = evaluate_full(state4["answers"])
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
r9 = evaluate_full(answers9)
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
r10 = evaluate_full(answers10)
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
for pid, pattern in catalog.items():
    out = render_diagram(pattern, diag_answers, {"zpa_routing_required": False})
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

print(f"\n{passed} checks passed.")
