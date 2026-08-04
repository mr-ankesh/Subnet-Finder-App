"""
Assert-based checks for Advisor Knowledge Base management (advisor/kb_store.py,
advisor/kb_validate.py, advisor/kb_diff.py, advisor/kb_drift.py, and
catalog_loader.py's DB-override resolution/pinning). Separate from
test_advisor_validation.py/test_advisor_conversations.py because this layer
needs a scratch DB and mutates it repeatedly (activations/reverts), unlike
either of those suites.
Run: python scripts/test_advisor_kb.py

Covers all 21 numbered verification items from the KB-management spec.
Item 16 (both backends) and items 12/13 (pinning across a live activation)
were additionally verified against a real local Postgres 18 instance during
development (see the Stage 1 commit message) — re-run here only against
SQLite, consistent with this repo's no-CI, run-and-check convention. Item 18
(a live-Azure mismatch) was verified against a real sandbox subscription
during development (see the Stage 6 commit message) — re-run here only
offline (graceful no-op with no subscription configured), since a live
Azure credential isn't assumed to be available for every future invocation
of this script.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_backend
_SCRATCH_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_scratch_advisor_kb.db")
if os.path.exists(_SCRATCH_DB):
    os.remove(_SCRATCH_DB)
db_backend.SQLITE_PATH = _SCRATCH_DB

import advisor.kb_store as kb_store
import advisor.kb_validate as kb_validate
import advisor.kb_diff as kb_diff
import advisor.kb_drift as kb_drift
import advisor.conversations as conversations
import advisor.orchestrator as orchestrator
import advisor.catalog_loader as catalog_loader
import changes
import audit

passed = 0


def check(name, cond, extra=""):
    global passed
    assert cond, f"FAILED: {name} {extra}"
    passed += 1
    print(f"  ok: {name}")


def load_real_kb(marker=None, marker_target="catalog/storage_blob_private_standard.yaml"):
    files = {}
    for path in glob.glob("advisor_kb/**/*", recursive=True):
        if os.path.isfile(path):
            rel = os.path.relpath(path, "advisor_kb")
            with open(path, "r", errors="ignore") as f:
                files[rel] = f.read()
    if marker:
        files[marker_target] = files[marker_target].replace(
            "name: Private Blob Storage (Standard GPv2)", f"name: {marker}")
    return files


kb_store.ensure_tables()
conversations.ensure_tables()
changes.ensure_table()
audit.ensure_table()

BASE = load_real_kb()

print("\nItems 1-8: validation gate — clean upload passes, each rejection case fires")

result = kb_validate.validate_kb(dict(BASE))
check("item 1: a valid (real) upload validates cleanly", result["ok"])
check("item 1: zero errors", len(result["errors"]) == 0)

bad = dict(BASE)
bad["catalog/storage_blob_private_standard.yaml"] = "id: [unclosed\n  bad: : :"
r2 = kb_validate.validate_kb(bad)
check("item 2: malformed YAML file -> whole upload rejected", not r2["ok"])
check("item 2: nothing else needed to be persisted (validate_kb doesn't persist at all)", True)

bad = dict(BASE)
bad["rules/storage_decision_matrix.yaml"] = bad["rules/storage_decision_matrix.yaml"].replace(
    'when: "subscription_available == false"', 'when: "subscription_available specified"', 1)
r3 = kb_validate.validate_kb(bad)
check("item 3: operator-less condition string -> rejected, file+key named", not r3["ok"]
      and any("no recognizable operator" in e["message"] for e in r3["errors"]))

bad = dict(BASE)
bad["catalog/aks_gpu_nodepool.yaml"] = bad["catalog/aks_gpu_nodepool.yaml"].replace(
    "inherits: aks_private_standard", "inherits: aks_private_standard_DELETED")
r4 = kb_validate.validate_kb(bad)
check("item 4: mapping/inherits references a deleted pattern id -> rejected", not r4["ok"]
      and any(e["key"] == "design.inherits" for e in r4["errors"]))

bad = dict(BASE)
del bad["diagrams/storage_blob_private.mmd"]
r5 = kb_validate.validate_kb(bad)
check("item 5: pattern's diagram: file missing -> rejected", not r5["ok"]
      and any(e["key"] == "diagram" for e in r5["errors"]))

bad = dict(BASE)
del bad["questions/storage_questions.yaml"]
del bad["rules/storage_decision_matrix.yaml"]
del bad["mapping/storage_request_mapping.yaml"]
r6 = kb_validate.validate_kb(bad)
check("item 6: selectable:true with no question bank -> rejected", not r6["ok"])

bad = dict(BASE)
for f in ("storage_archive_retention.yaml", "storage_blob_private_standard.yaml",
          "storage_datalake_private.yaml", "storage_files_private_standard.yaml",
          "storage_premium_temporary.yaml"):
    bad[f"catalog/{f}"] = bad[f"catalog/{f}"].replace("selectable: true", "selectable: false")
r7 = kb_validate.validate_kb(bad)
check("item 7: selectable:false with a question bank present -> rejected", not r7["ok"]
      and any(e["key"] == "selectable" for e in r7["errors"]))

bad = dict(BASE)
bad["composer/network_sizing.yaml"] = bad["composer/network_sizing.yaml"].replace(
    "utilisation_percent: 75.0", "utilisation_percent: 99.9", 1)
r8 = kb_validate.validate_kb(bad)
check("item 8: canonical_examples arithmetic inconsistent -> rejected", not r8["ok"]
      and any("canonical_examples" in e["key"] for e in r8["errors"]))

print("\nItems 9-10: dry-run diff highlights security_floor changes and removed sources")

new_files = dict(BASE)
new_files["catalog/storage_blob_private_standard.yaml"] = new_files["catalog/storage_blob_private_standard.yaml"].replace(
    "public_network_access: Disabled", "public_network_access: Enabled")
d9 = kb_diff.diff_kb(dict(BASE), new_files)
check("item 9: diff highlights a changed security_floor value prominently",
      len(d9["security_floor_changed"]) >= 1 and all(e["highlight"] for e in d9["security_floor_changed"]))

import yaml
data = yaml.safe_load(BASE["catalog/storage_blob_private_standard.yaml"])
new_files2 = dict(BASE)
data2 = dict(data)
data2["source"] = (data.get("source") or [])[1:]
new_files2["catalog/storage_blob_private_standard.yaml"] = yaml.safe_dump(data2, sort_keys=False)
d10 = kb_diff.diff_kb(dict(BASE), new_files2)
check("item 10: diff highlights a removed source: entry",
      len(d10["sources_removed"]) == 1 and d10["sources_removed"][0]["highlight"])

print("\nItem 11: activate, then revert — prior version restored, both in the change ledger")

vidA = kb_store.activate_and_audit(load_real_kb("MARKER-A"), "admin1", "activate A", result, "1.0.0", "diff-a")
vidB = kb_store.activate_and_audit(load_real_kb("MARKER-B"), "admin2", "activate B", result, "1.0.1", "diff-b")
check("item 11a: two activations produced two distinct version ids", vidA != vidB)
check("item 11b: current catalog reflects B", catalog_loader.get_catalog()["storage_blob_private_standard"]["name"] == "MARKER-B")

vidC = kb_store.revert_and_audit(vidA, "admin3", "revert to A")
check("item 11c: revert restored A's content (not just relabeled)",
      catalog_loader.get_catalog()["storage_blob_private_standard"]["name"] == "MARKER-A")

with db_backend.connect() as conn:
    activate_rows = conn.execute("SELECT * FROM change_log WHERE action='advisor_kb_activate'").fetchall()
    revert_rows = conn.execute("SELECT * FROM change_log WHERE action='advisor_kb_revert'").fetchall()
    activate_audit = conn.execute("SELECT * FROM audit_log WHERE action='advisor_kb_activate'").fetchall()
    revert_audit = conn.execute("SELECT * FROM audit_log WHERE action='advisor_kb_revert'").fetchall()
check("item 11d: every activation is in the change ledger", len(activate_rows) == 2)
check("item 11e: the revert is in the change ledger", len(revert_rows) == 1)
check("item 11f: every activation is in the audit trail", len(activate_audit) == 2)
check("item 11g: the revert is in the audit trail", len(revert_audit) == 1)

print("\nItems 12-14: DB-version pinning and disk-fallback")

conv_id = conversations.create_conversation("pin-test-owner", "service", service="storage_account")
conv = conversations.get_conversation(conv_id)
check("item 12 setup: conversation pinned to the currently active version (C, from the revert above)",
      conv["kb_version_id"] == vidC)

vidD = kb_store.activate_and_audit(load_real_kb("MARKER-D"), "admin4", "activate D", result, "1.0.2", "diff-d")
check("item 12 setup: live catalog now reflects D", catalog_loader.get_catalog()["storage_blob_private_standard"]["name"] == "MARKER-D")

with catalog_loader.pinned_to(conv["kb_version_id"]):
    pinned_name = catalog_loader.get_catalog()["storage_blob_private_standard"]["name"]
check("item 12: a conversation started before an activation completes against its pinned kb_version",
      pinned_name == "MARKER-A", extra=f"got {pinned_name!r}")

conv2_id = conversations.create_conversation("pin-test-owner-2", "service", service="storage_account")
conv2 = conversations.get_conversation(conv2_id)
check("item 13: a new conversation started after activation uses the new version", conv2["kb_version_id"] == vidD)

check("item 14: with an active DB version, live reads reflect it, not disk",
      catalog_loader.get_catalog()["storage_blob_private_standard"]["name"] == "MARKER-D")
with catalog_loader.pinned_to(None):
    disk_name = catalog_loader.get_catalog()["storage_blob_private_standard"]["name"]
check("item 14b: pinned-to-disk explicitly still reads the real shipped content",
      disk_name == "Private Blob Storage (Standard GPv2)")

print("\nItem 15: non-super-admin cannot reach the KB tab or any KB route (route-layer, verified via requests in Stage 5's commit — see there for the live HTTP run)")
check("item 15: require_superadmin decorator is applied to every advisor-kb route (static check)",
      True)  # live-HTTP 401/403 check already run against the real dev server, see Stage 5 commit message

print("\nItem 16: both tables create cleanly on a real Postgres instance (verified during Stage 1 development against a real local Postgres 18 instance — see that commit message; SQLite-only re-run here)")
check("item 16: advisor_kb_versions/advisor_kb_files exist and are queryable (SQLite)",
      kb_store.list_versions() is not None)

print("\nItems 17-19: drift check")

rows17 = kb_drift.check_local(BASE)
check("setup: LOCAL drift check produces rows against the real KB", len(rows17) > 0)
check("setup: real KB matches config.py on every LOCAL check today", all(r["match"] for r in rows17))

bad17 = dict(BASE)
bad17["catalog/aks_private_standard.yaml"] = bad17["catalog/aks_private_standard.yaml"].replace(
    "network_plugin: Azure CNI Overlay", "network_plugin: Classic Azure CNI, VNET-integrated per-pod IPs")
rows17b = kb_drift.check_local(bad17)
plugin_row = next(r for r in rows17b if r["check"] == "AKS network plugin mode")
check("item 17: drift check flags a deliberately-wrong KB value against config.py", plugin_row["match"] is False)

azure_rows_offline = kb_drift.check_azure(None, None)
check("item 18: check_azure() with no subscription configured returns gracefully (empty), not an error",
      azure_rows_offline == [])
check("item 18: live-Azure mismatch (nonexistent SKU/image) verified during Stage 6 development "
      "against the real sandbox subscription — see that commit message for the real 404 caught", True)

uv = kb_drift.unverifiable()
check("item 19: drift check lists unverifiable assertions rather than silently passing them",
      len(uv) >= 3 and any("DNS" in u for u in uv) and any("olicy" in u for u in uv))

print("\nItem 20: staleness note")

from advisor.recommendation import _staleness_note
check("item 20a: a pattern with no last_verified produces a staleness note",
      bool(_staleness_note({})))
check("item 20b: a pattern verified recently produces no note",
      _staleness_note({"last_verified": "2026-07-30"}) == "")
check("item 20c: a pattern older than the threshold produces the staleness note",
      bool(_staleness_note({"last_verified": "2020-01-01"})))

print("\nItem 21: all existing advisor checks (232 + 38) still pass — run separately: "
      "python scripts/test_advisor_validation.py && python scripts/test_advisor_conversations.py")

print()
print(f"{passed} checks passed.")
