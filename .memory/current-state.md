# Current State

> Last updated: 2026-08-01. Update this file at the end of every session
> (see maintenance rules in `CLAUDE.md` → "Session Memory Protocol").

## Development Status

Active development. Working tree has an **uncommitted** Storage Account
feature on top of `main` — see "Features In Progress" below. `main` is the
only branch; commits go straight to it.

## Features Completed (committed, on `main`)

- Subnet/VNET CIDR pool allocation (`POOLS`, `compute_free_blocks`, etc.)
- Hub-spoke peering + hub integration
- Firewall policy rule management
- UDR/routing management
- ZPA routing requests (RND/other/NMO) + live reachability testing over SSH
- AKS cluster deployment, incl. node OS SKU list, CMK/disk-encryption toggle,
  resource tags (owner/env/criticality/creator)
- DNS / Private DNS zone linking
- VNET decommissioning
- Network issue diagnosis (read-only)
- Cost dashboard with real (not retail-estimate) resource costs
- Budget alerts — forecast-gated (run-rate aware), not raw-threshold
- Resource optimizer — idle/orphaned scan, usage-pattern underutilization (low-CPU VMs)
- Line-manager approval flow (Entra `manager` claim → Keycloak → app), with
  dependency preflight gate and fallback-approver routing
- Keycloak-driven team visibility (SSO groups), individual-fallback for
  zero-group users
- AI requester + admin chat agents, tool-calling only, persisted via `chats.py`
- Notifications: Teams Adaptive Card + SMTP, template-first with optional
  AI-drafted enhancement, leaked-reasoning/malformed-draft guard
- Rebrand: Network Copilot → **Presight AlMadar 360** (`chore(brand)` commits),
  scope broadened from "network" to general cloud ops
- SQLite ↔ Postgres portability layer (`db_backend.py`) + migration script
  covering all raw-SQL tables (subinventory, budget_alert_state, agent_chats)
- VM(s) deployment (`RequestType.VM_CREATE`) — committed 2026-08-01 (commit
  `65df6b9`); see `architecture-decisions.md`. Partial verification only
  (see "Verification Notes" below) — full live-Azure check still pending.

## Features In Progress (uncommitted — working tree only)

**Storage Account Request & Deploy (`RequestType.STORAGE_ACCOUNT_CREATE`)** —
implemented end-to-end 2026-08-01, matching the AKS/VM architecture exactly
(see `CLAUDE.md` → "Storage Account request" for the full design). Delivered
in 4 phases, each verified in-browser against the running local instance
before moving to the next:

- **Phase 1 (data model/config/form)**: `models.py` new `STORAGE_ACCOUNT_CREATE`
  type + `STORAGE_DEPLOYED` status; `config.py` new "Storage Defaults"
  settings tab; `app.py` `TYPE_REQUIRED_DETAILS` entry + `_validate_storage_request()`
  (offline checks — name/container/IP-rule rules, option membership, region
  justification, tag presence); `templates/requester.html` full form section
  (Basic Info, Storage Config, Access Tier, Networking, Identity, Encryption,
  Blob Protection, Containers, Object Replication, Tags); help copy in
  `help_admin.html`/`help_requester.html`.
- **Phase 2 (Azure discovery)**: `requirements.txt` +`azure-mgmt-storage`,
  +`azure-mgmt-msi` (net-new); `azure_tools.py` `list_storage_skus`,
  `list_user_assigned_identities`, `list_keyvaults`, `list_keyvault_keys`,
  `check_storage_name_availability`; 4 new `app.py` routes (`storage-skus`,
  `storage-identities`, `storage-keyvaults`, `storage-keys`) reusing
  `_vm_lookup_response`; VNet/subnet picker **reuses** the existing
  `/api/azure/vnets`/`/api/azure/subnets` routes (no duplicate
  storage-vnets/storage-subnets — a deliberate deviation from the literal
  spec, matching how AKS/VM already share those two routes); live-Azure
  checks added to `_validate_storage_request()`.
- **Phase 3 (deploy engine)**: `azure_tools.py` `create_storage_account`
  (RG → account → blob/file service properties → containers, resumable),
  `delete_storage_account` (revert), `create_object_replication_policy` +
  `create_storage_private_endpoint` (best-effort, non-blocking optional
  steps); `changes.py` `delete_storage_account` revert-op registered;
  `app.py` `storage_check`/`storage_deploy` actions in the shared
  `admin_azure_action()` dispatcher, `_storage_preview()` +
  `/api/admin/storage/preview/<id>`, bespoke `_auto_advance()` branch (reads
  `all_steps_ok` off the latest audit entry, same reasoning as VM_CREATE's
  bespoke branch); `templates/request_detail.html` admin deploy panel.
  Revert UI needed **no template changes** — it turned out the per-resource
  revert (Change Ledger / `/admin/changes`) is already fully generic across
  request types, driven by `changes.py`'s `revert_op` dispatch.
- **Phase 4 (tests/docs/memory)**: `scripts/test_storage_validation.py`
  (assert-based, `unittest.mock`-stubbed `azure_tools`, 31 checks, no new
  dependency); `docs/HOW_IT_WORKS.md` + `CLAUDE.md` updated; this file.

Key decisions locked in with the user before implementation (see
`architecture-decisions.md` for full rationale):
- Tag schema: `_deploy_tags()` extended with a new 9-tag governance schema
  (`ApplicationName`, `BusinessUnit`, `Criticality`, `DataClassification`,
  `Owner`, `Approver`, `Environment`, `ServiceClass`, `Sovereignty`) —
  coexists with the old lowercase set (Azure tags are case-preserving, so
  both spellings can't collapse into one).
- Tests: assert-based script, not pytest — matches the documented no-test-
  framework convention.
- Status tracking maps onto the existing `SUBMITTED → X_DEPLOYED →
  COMPLETED` convention, not a bespoke Pending/Deploying/Failed/Reverted
  enum (Deploying/Failed are transient, derived from action responses +
  audit; Reverted = the existing generic CANCELLED/REJECTED flow).
- Rollback is delete-only (Azure has no account-level restore); blob
  soft-delete/versioning (on by default) is the only recovery path for
  what's inside the account.

**No database migration needed** — Storage details live in
`SpokeRequest.details` JSON like every other type; the only new persisted
values are string constants (`RequestType`/`RequestStatus`), which need no
schema change on SQLite or Postgres.

Unrelated uncommitted changes in the same working tree (predate this
feature, cosmetic/infra, not part of Storage or VM):
- `static/css/style.css`, `static/page-bg.jpg` replaced (smaller file) —
  `static/page-bg-original.jpg` sits alongside as an untracked backup
- `helm/subnet-manager/values.yaml` — `existingSecretName` set to
  `"almadar-db"` (was empty) — a real prod-config change, not a placeholder

## Pending Work

- Commit the Storage Account feature (currently only in working tree)
- Confirm the `helm/subnet-manager/values.yaml` secret-name change
  (`almadar-db`) is intentional and matches the actual K8s secret in the
  target cluster before this ships
- Decide whether `static/page-bg-original.jpg` (untracked) should be removed,
  kept as a backup, or was left over accidentally
- Run a full live-Azure verification of both VM_CREATE and
  STORAGE_ACCOUNT_CREATE against a real subscription before either reaches
  prod — both currently only partially verified locally (see below)

## Verification Notes (2026-08-01, both VM and Storage)

This sandbox has no real Azure Service Principal credentials configured
(`.env`'s `AZURE_CLIENT_ID`/`SECRET`/`TENANT_ID` are blank — a standing,
sandbox-wide gap, not specific to either feature). Both features were
verified in-browser against the running local instance by driving the
actual routes with `curl` (login, submit, admin preview/deploy):
- Submission gates, offline validation, and error handling all confirmed
  correct — no 500s/tracebacks anywhere, including when live-Azure calls
  fail (they degrade to clean `{"ok": false, "error": "..."}` /
  `{"error": "..."}` responses, confirmed against real Azure endpoints
  returning real errors like `SubscriptionNotFound`/`InvalidSubscriptionId`).
- Storage's dry-run deploy (`AZURE_DRY_RUN=true`, the local default) was
  verified end-to-end: preview renders correctly, deploy short-circuits via
  `_guard` with a clean simulated response, status advances
  `SUBMITTED → STORAGE_DEPLOYED` (correctly **not** `COMPLETED`, since
  `all_steps_ok` is never set for a dry-run — the function body never runs).
- **Not verified for either feature**: a real (non-dry-run) deploy against
  live Azure, since that needs real reachable credentials this sandbox
  doesn't have. Live-Azure-dependent discovery-route *data accuracy* is
  likewise unverified beyond "it degrades gracefully."

## Active Priorities

1. Commit the Storage Account feature
2. Full live-Azure verification of VM_CREATE and STORAGE_ACCOUNT_CREATE
   (needs real credentials — see `next-actions.md`)

## Current Blockers

- None known. SSO/Keycloak-gated logic (approvals, team routing) remains
  unverifiable in local dev by design — see `CLAUDE.md` local-vs-prod table —
  this is a standing constraint, not a blocker for current work.
