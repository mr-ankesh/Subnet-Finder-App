# Current State

> Last updated: 2026-08-02. Update this file at the end of every session
> (see maintenance rules in `CLAUDE.md` → "Session Memory Protocol").

## Development Status

Active development. Storage Account feature committed (`8ef4ef2`,
2026-08-01). Resource Relationship Graph (new module, read-only) built and
real-Azure verified 2026-08-02, **not yet committed** — see "Features In
Progress" below. Remaining uncommitted changes also include the unrelated
cosmetic/infra items noted below. `main` is the only branch; commits go
straight to it.

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
  `65df6b9`); see `architecture-decisions.md`. **Fully verified against real
  Azure 2026-08-01** — see "Verification Notes" below.

## Features In Progress

### Resource Relationship Graph — base feature committed (`66824be`), UI polish pass uncommitted

New read-only (V1, no Azure mutations) module: visual node/edge map of
Azure resource dependencies (Network/Compute/Platform/Storage/Security) for
troubleshooting and impact analysis. Does **not** use the `RequestType`
lifecycle — see `CLAUDE.md` → "Resource Relationship Graph" for the full
design (ARG-reverse-index discovery, isolated 4th Reader-only SP,
deterministic hop-bounded truncation, Cytoscape.js frontend).

- `resourcegraph.py` (new), `config.py` (`RESGRAPH_*` settings + category),
  `app.py` (`/resource-graph`, `/api/resource-graph/subscriptions`,
  `/api/resource-graph/query`, `/api/admin/settings/test-resourcegraph`),
  `templates/resource_graph.html` (new), `templates/base.html` (Governance
  nav entry), `scripts/test_resourcegraph_validation.py` (new, 32
  assert-based checks, no Azure needed).
- **Verified against real Azure** (subscription `845e564b-31a3-44b0-b030-226798b31574`,
  "Sandbox Connectivity"): whole-subscription mode; VNet/Subnet/NSG/
  RouteTable chains; Private DNS Zone rooted directly at the zone, showing
  its `virtualNetworkLinks` → linked VNet → subnets (the exact reverse-index
  case this design exists for); a temporary real VM deployed via the
  existing VM_CREATE feature specifically for this test, showing the full
  VM→NIC→Disk→Subnet→VNet/RouteTable chain, then reverted and confirmed
  cleaned up (`az resource list` empty, matches `rg-claude-e2e-qa-test`'s
  state before this test).
- **Two real bugs found and fixed via this real-Azure testing** (passed a
  32-check mocked test suite cleanly first, because the mocks shared the
  same wrong assumptions) — see `architecture-decisions.md` 2026-08-02 entry
  for both: (1) edge-map key casing mismatch, (2) ARM sub-resource
  `properties` nesting not accounted for in `REFERENCE_PATHS`. Also fixed
  before real-Azure testing: an offline-test-caught bug where "Resource Type
  selected with no Resource Name" (a form-allowed combination) silently fell
  back to whole-subscription mode instead of respecting the type filter.
- **Not verified against real Azure**: AKS/Storage-Account/Key-Vault-rooted
  graphs specifically (none currently exist in the sandbox — the earlier
  session's test resources were reverted) — only via mocked offline tests
  for those code paths (`_expand_aks_node_rg`, `_expand_storage_containers`,
  `_expand_pe_dns_zone_group` are all try/except-wrapped best-effort SDK
  calls, untested against live data). Also not visually confirmed in an
  actual browser — no headless-browser tool was available this session;
  verified structurally instead (page renders with no traceback, JS syntax
  valid, manual code review caught and fixed one Cytoscape/CSS-var
  incompatibility). See `next-actions.md`.
- **Base feature committed** `66824be` (2026-08-02).

**UI polish pass (2026-08-02, on top of `66824be`, uncommitted)**: the
initial version rendered every node identically with always-on edge labels
and no hierarchy. Added per-exact-type color/icon/size styling (hand-drawn
inline SVG icons, not Microsoft's actual Azure icon assets), a `concentric`
layout centered on the hub VNET (importance-based ring placement, no new
layout library) with `localStorage`-persisted manual positions, a custom
tooltip + animated dashed-line neighborhood highlight replacing always-on
edge labels, a client-side Relationship Analysis panel (direct/upstream/
downstream/"impact if deleted" — all computed from already-fetched edges,
explicitly caveated to the current graph's scope), a provisioningState-
derived health ring, a stats bar, exact-type filtering, and a
`cytoscape-navigator` minimap. Two small additive backend fields power
this: `hub_id` (built from `HUB_*` settings, no extra Azure call) and
`tags`/`subscriptionId` (added to the ARG projection). See
`architecture-decisions.md` 2026-08-02 entries.
- Verified: `scripts/test_resourcegraph_validation.py` now has 36 checks
  (was 32) — the `hub_id` tests initially used env-var overrides and
  **failed against this sandbox's real DB-configured hub settings** (DB
  overrides win over env vars in `config.py`'s resolution order), which is
  exactly the kind of "the mock's assumption was wrong" issue this repo has
  hit twice before on this same feature — fixed by monkeypatching `cfg`
  attributes directly instead. Real-Azure re-check confirmed `tags` (a
  genuinely-tagged Route Table, cross-verified via `az resource list`),
  `subscriptionId`, and `hub_id` (resolved to the real configured hub VNET)
  all come back correctly shaped.
- **Not verified**: visual rendering in an actual browser (still no
  headless-browser tool available this session — same limitation as the
  base feature); the `cytoscape-navigator` CSS class name used for the
  minimap's viewport-indicator styling (`cytoscape-navigatorView`) is from
  memory of the library, not confirmed against its actual source — if wrong
  the minimap still functions, just without the theme-matched border color.
- **Not yet done**: commit this polish pass.

### Storage Account Request & Deploy — committed 2026-08-01 (`8ef4ef2`)

Implemented end-to-end, matching the AKS/VM architecture exactly (see
`CLAUDE.md` → "Storage Account request" for the full design). Delivered
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

- Confirm the `helm/subnet-manager/values.yaml` secret-name change
  (`almadar-db`) is intentional and matches the actual K8s secret in the
  target cluster before this ships
- Decide whether `static/page-bg-original.jpg` (untracked) should be removed,
  kept as a backup, or was left over accidentally
- Delete the leftover empty `rg-claude-e2e-qa-test` resource group in the
  `845e564b-31a3-44b0-b030-226798b31574` ("Sandbox Connectivity") sandbox
  subscription — created for the 2026-08-01 real E2E test, now empty
  (storage account + VM successfully reverted); resource-group deletion
  itself was blocked by the auto-mode permission classifier, so it was left
  for the user to remove.

## Verification Notes

**2026-08-01, full real Azure E2E pass (both VM and Storage) — both
features fully confirmed working, independently of the app's own
reporting.** Real SP credentials were configured (`AZURE_AUTH_MODE=service_principal`)
and `AZURE_DRY_RUN` was flipped off (both done by the user, not this
session) against subscription `845e564b-31a3-44b0-b030-226798b31574`
("Sandbox Connectivity"). Ran the full lifecycle for both request types:

- **Preview**: Storage's `_storage_preview` (no Azure call needed) and VM's
  `build_vm_plan`/quota check (genuinely live) both resolved correctly
  against real Azure — first time VM's live plan resolution succeeded in
  this repo's history of local testing (previously blocked by having no
  real subscription).
- **Deploy**: both created real resources successfully
  (`rg-claude-e2e-qa-test`, region `uaenorth`) — a `StorageV2`/`Standard_LRS`
  account with a container, and a `Standard_B1s` VM attached to an existing
  sandbox VNet/subnet (`vnet-spoke1-sand-conn-prd-prs-aen-001` /
  `snet-spoke1-sand-conn-prd-prs-aen-001`, in a different resource group,
  confirming cross-RG VNet attach works).
- **Verify Azure resources**: independently confirmed via `az` CLI (a
  separate identity/tool from the app, not just trusting the app's success
  response) — `az storage account show` confirmed every security default
  landed exactly as coded (TLS1_2, HTTPS-only, shared-key access disabled,
  blob public access disabled, infra encryption on, network default Deny,
  public network access Disabled, SystemAssigned identity); the container
  was confirmed via the ARM control-plane API (data-plane `az storage
  container list` was itself blocked by the account's own network rules —
  good independent proof the Deny-by-default posture is real, not just a
  reported property); `az vm show` confirmed the VM (`provisioningState:
  Succeeded`, correct size/image/OS disk) and `az network nic show`
  confirmed **no public IP** or the correct cross-RG subnet attachment.
  Azure also auto-created a `Microsoft.EventGrid/systemTopics` resource
  alongside the storage account — a normal Azure platform behavior tied to
  the account's lifecycle, not something this app's code requested.
- **Revert**: both reverted cleanly via the Change Ledger
  (`/api/admin/changes/<id>/revert`) — `delete_storage_account` and
  `delete_vm` both succeeded, ledger entries flipped from `active` to
  `reverted`, and a revert note was correctly appended to each request.
- **Verify cleanup**: `az resource list` on the resource group came back
  **empty** — independently confirmed the storage account (and its
  EventGrid system topic) and the VM (with its NIC/OS disk, cascade-deleted
  via `delete_option=Delete`) were all genuinely gone.

This closes out the one gap noted after initial implementation (real-Azure
verification) — both `VM_CREATE` and `STORAGE_ACCOUNT_CREATE` are now
verified end-to-end for the happy path against a real subscription. Not
covered by this pass: failure/partial-failure paths (e.g. a mid-loop VM
failure, a container-creation failure after the account exists), replication/
private-endpoint best-effort steps, and CMK/user-assigned-identity paths —
none of those were exercised against real Azure, only their offline logic
and dry-run behavior (see the Phase 1–4 implementation notes in this
session's daily log).

## Active Priorities

1. Delete the leftover sandbox resource group (`rg-claude-e2e-qa-test`) —
   needs the user, blocked for this session by the permission classifier.
2. No other blockers — both recent features are now real-Azure verified for
   their primary flow.

## Current Blockers

- None known. SSO/Keycloak-gated logic (approvals, team routing) remains
  unverifiable in local dev by design — see `CLAUDE.md` local-vs-prod table —
  this is a standing constraint, not a blocker for current work.
