# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Network Copilot** (repo name: Subnet-Finder-App) — a Flask portal for day-2
Azure network operations: subnet/VNET allocation, hub-spoke peering, firewall
rules, UDR/routing, ZPA routing, AKS cluster deployment, VM(s) deployment,
Storage Account deployment, DNS, cost dashboard, resource optimizer, and
network-issue diagnosis. Originally subnet-only; brand
and scope have broadened to general cloud operations (see `docs/BRANDING.md`
and recent `chore(brand)` commits) — don't assume "network" in an old
identifier means the feature is network-scoped only.

Read `docs/HOW_IT_WORKS.md` first for the actual request-lifecycle and Azure
execution model — it's short and answers most "why is this built this way"
questions better than re-deriving them from code.

## Session Memory Protocol

This repo maintains a `.memory/` directory as a persistent project-memory and
progress-tracking system, separate from this architecture guide. **Memory
maintenance is mandatory and part of the definition of done** — no feature
or fix is complete until the relevant `.memory/` files reflect it.

**At the start of every session**, read (in this order):
1. `.memory/project-overview.md`
2. `.memory/current-state.md`
3. `.memory/next-actions.md`
4. The latest file in `.memory/daily/` (most recent `YYYY-MM-DD.md`)

**At the end of every session** (or immediately after finishing a feature —
don't wait for session end if the feature is done sooner):
1. Update `.memory/current-state.md` (completed / in-progress / pending / blockers)
2. Update `.memory/next-actions.md` (remove completed items, add new ones, keep prioritized)
3. Update (or create) today's `.memory/daily/YYYY-MM-DD.md`
4. Add an entry to `.memory/architecture-decisions.md` if a significant
   decision was made (append-only — never delete or rewrite past entries)
5. Update `.memory/known-issues.md` / `.memory/feature-roadmap.md` if scope changed
6. Roll up into `.memory/weekly/YYYY-Www.md` / `.memory/monthly/YYYY-MM.md`
   when a week/month completes

Rules: keep entries concise and bulleted; never delete historical
daily/weekly/monthly records (archive, don't remove); another Claude session
must be able to resume work using only the `.memory/` directory plus this
file.

## Local dev vs. production — not the same stack

These two environments differ in backend **and** auth, not just config values,
so code that only gets exercised in one of them (SSO, group/manager claims,
Postgres-only migration paths) can look fine locally and still break prod:

| | Local dev / testing | Production (AKS) |
|---|---|---|
| Database | **SQLite** (`data/requests.db`), single writer | **PostgreSQL** (`DATABASE_URL` set, `db_backend.IS_POSTGRES` true) |
| Auth | **Local password** login (`ADMIN_PASSWORD`), `AUTH_PROVIDER=local`, **no Keycloak/SSO** | **Keycloak SSO**, `AUTH_PROVIDER=keycloak` — roles, groups, manager claim all live |
| Replicas | 1 (forced — SQLite is single-writer) | **3** (`helm/subnet-manager/intdev-aks-values.yaml` sets `replicaCount: 3`; the Helm chart refuses `replicaCount > 1` unless Postgres is configured, see `templates/deployment.yaml`) |

Practical implications when changing code:
- Anything SSO-gated (Keycloak team/group visibility, line-manager approval
  routing, audit actor from token) is **inert in local dev** — there's no
  Keycloak to exercise it against. Verify that logic by reasoning through it
  or with an isolated/mocked check, not by running it locally and seeing it
  "work."
- A new DB column on an **existing** table needs an explicit Postgres
  migration path — `db.create_all()` only creates new tables, and any
  `PRAGMA`-based schema introspection in the bootstrap must be guarded
  behind `db_backend.IS_POSTGRES` or it silently no-ops on Postgres (this bug
  class has recurred: see the `approval_state` backfill fix).
- With 3 replicas live in prod, don't assume in-process state (module-level
  caches, in-memory locks) is shared — anything that needs to be consistent
  across requests has to go through the DB, since any of the 3 pods can serve
  a given request.
- Prod ships via **rebuilt container image** (Helm upgrade), never by
  patching a running pod — there's no code sync between local and the AKS
  deployment other than through that image.

## Commands

There is no build step, linter, or test suite in this repo (pure Flask +
Jinja, no bundler; no pytest/ruff/flake8 config present). Development loop is
run-and-check-in-browser.

```bash
# Local run
pip install -r requirements.txt
python app.py                      # dev server (has an if __name__ == "__main__" block)
# or, closer to prod:
gunicorn --workers=1 --threads=8 --bind=0.0.0.0:8080 app:app

# Docker
docker compose up --build          # docker-compose.yml, mounts ./data
curl http://localhost:8080/health

# One-time data import (fresh DB) — reads subnets.xlsx from repo root
python migrate_excel_to_db.py

# SQLite -> Postgres migration (when moving to multi-replica)
# optional positional arg: path to the sqlite file (defaults to data/requests.db);
# target is read from DATABASE_URL
python scripts/sqlite_to_postgres.py [path/to/requests.db]
```

Deploying the *app itself* (as opposed to the Azure network changes it
makes) goes through Docker + the Helm chart in `helm/subnet-manager/` (or raw
manifests in `k8s/`) — see `docs/DEPLOYMENT.md` for the full checklist. Do not
confuse the two "deployment" meanings; `docs/HOW_IT_WORKS.md` §7 has the table.

## Architecture

### One big Flask app, feature modules lazy-imported

`app.py` (~200KB, 103 `@app.route` handlers) is the monolith: routes, view
logic, and wiring. It imports core pieces (`config`, `models`, `audit`,
`changes`, `db_backend`, `search`, `settings_store`, `auth_oidc`, `naming`,
`notifications`) at module level, but imports feature modules **lazily
inside the route functions that use them**: `azure_tools`, `agent_admin`,
`agent_requester`, `approvals`, `budgetalerts`, `costmgmt`, `netdiag`,
`optimize`, `reachability`, `resourcegraph`, `subinventory`, `chats`. This is deliberate — when
touching one of these features, the relevant code lives entirely in its own
module; `app.py` is just the route surface calling into it.

### Azure changes: imperative SDK calls, not IaC

No Terraform/Bicep/ARM templates. `azure_tools.py` calls the Azure SDK for
Python directly against the ARM REST API — one small idempotent call per
admin action (peer, firewall rule, route, etc.), gated by `AZURE_DRY_RUN`.
Azure itself is the source of truth; nothing is tracked in local state. The
app only *attaches* things to an existing hub (peerings, rules in an existing
firewall policy, routes in existing tables) — it never owns the hub's own
definition. Every mutating call is recorded to the audit trail (`audit.py`)
and the change ledger (`changes.py`), which is what drives revert/cancel.

### Config resolution: DB override → env var → default

`config.py`'s `SETTINGS_SPEC` is the single source of truth for `/admin/settings`
(categories become tabs). Every setting resolves live through that chain on
every access — editing Settings in the UI writes to the `app_settings` table
(`settings_store.py`) and takes effect with **no restart**. Only
security-critical bootstrap values (`FLASK_SECRET_KEY`, `ADMIN_PASSWORD`,
`DEBUG`, AI provider keys) are env-only, not UI-editable. Secrets are
Fernet-encrypted at rest using a key derived from `FLASK_SECRET_KEY` — rotating
that key invalidates stored secrets and sessions both.

### CIDR pool allocator (the app's namesake feature)

`POOLS` (`app.py`) is a fixed dict of named base CIDR blocks (e.g. `10.110.0.0/16`)
that every allocation is carved from. Given a pool, `compute_free_blocks()`
starts from the pool's base network and subtracts every subnet already marked
`used`/`reserved` in `subnet_records` (via `ipaddress.address_exclude`), producing
the disjoint free ranges. `candidates_from_free()` then enumerates concrete
CIDRs of the requester's chosen prefix length out of those free blocks (capped
at 1024 candidates) for the picker UI. `allocate_subnet()` re-validates
(inside the pool, no overlap with existing records, actually inside a
currently-free block) before writing through `db_utils.allocate_subnet_db()` —
the source of truth for "what's used" is always the `subnet_records` table,
never a cached free-list. `/subnets` (`subnet_allocator()`) is the pool
browsing/allocation UI; `/allocator`, `/allocate`, `/deallocate`,
`/available_base`, `/all_available`, `/free_summary`, `/pool_stats` are its
API routes. Adding a new pool means adding an entry to `POOLS`; there's no
separate pool-config table.

### Two DB backends, one abstraction

(See the local-vs-prod table above for which backend each environment
actually runs.)

- **SQLite** (default, `data/requests.db`) — single writer, so exactly one
  replica.
- **PostgreSQL** (`DATABASE_URL` set) — app is otherwise stateless (cookie
  sessions), so it scales to N replicas — prod runs 3.

The ORM (`models.py` via Flask-SQLAlchemy) is portable by default. The
raw-SQL modules (`db_utils.py`, `audit.py`, `settings_store.py`, `changes.py`,
`search.py`, `subinventory.py`, `budgetalerts.py`, `chats.py`) go through
`db_backend.py`, which translates `?` → `%s` placeholders, no-ops SQLite
PRAGMAs on Postgres, and normalizes rows to plain dicts on both backends.
`db_backend.py` is intentionally dependency-free (no app imports) since
`config.py` → `settings_store.py` → `db_backend.py` is itself an import chain.
When adding a new raw-SQL table/column, it needs to work on **both** backends
and existing Postgres DBs need a migration path (see recent
`fix(approvals): add approval_state on existing Postgres DBs` commit) —
schema changes aren't automatically applied to already-deployed Postgres data.

Note that `models.py`'s 7 ORM tables are not the whole schema: tables like
`subscription_inventory` (`subinventory.py`), `budget_alert_state`
(`budgetalerts.py`), and `agent_chats` (`chats.py`) are created by their own
modules' `ensure_table()` via raw SQL and never appear in `models.py` — which
is also why they each needed explicit coverage added to
`scripts/sqlite_to_postgres.py` separately (don't assume a new raw-SQL table
is automatically picked up by the migration script).

### Auth: local password or Keycloak SSO, switched live

`AUTH_PROVIDER` setting toggles `local` vs `keycloak` with no restart.
`auth_oidc.py` (Authlib) populates just two session keys the rest of the app
already depends on: `session["is_admin"]` and `session["admin_name"]`
(read by `current_actor()` for audit/change-ledger attribution). Roles map to
`subnet-admin` / `subnet-requester` Keycloak client roles.

Team membership under SSO comes from the Keycloak `groups` claim
(`auth_oidc.groups_from_token()`), captured into `session["sso_groups"]` at
login (full group paths reduced to leaf name). `app.py`'s `_available_teams()`
is the single enforcement point for team lists everywhere (request form,
My/Team Requests, `/api/requester/teams`, `/api/requester/team-requests`) —
in SSO mode it's exactly the user's own groups (server rejects querying a team
you're not in); in local/open mode it's the admin-configured `Settings → Teams`
list (selection-based, not identity-enforced). SSO users in zero groups fall
back to individual-only visibility. See `docs/KEYCLOAK.md` for the full
behavior table and the client-mapper prerequisites.

### Approval flow: relationship-based routing with a dependency gate

`approvals.py` implements an optional per-request-type approval gate. Routing
is always to *that specific requester's* line manager — never a single global
approver — sourced from a `manager` OIDC token claim that Keycloak maps from
the Entra ID `manager` attribute. The feature runs a **preflight dependency
check** (Settings → Approvals) and auto-disables with a specific missing-
prerequisite message if the Entra→Keycloak→token claim chain isn't fully
wired; it's inert until every check passes, so an unconfigured portal behaves
exactly as if the module didn't exist. Fallback: no manager on file (or
non-SSO) routes to a configured fallback approver email, or any super-admin,
flagged as fallback-routed. Self-approval is blocked.

### AI agents are tool-callers, not free-form SQL/Azure access

`agent_requester.py` and `agent_admin.py` are the two chat agents (requester
vs admin-only, e.g. CIDR assignment / hub integration are admin-only). Both
call the configured LLM (`AGENT_PROVIDER`: openai-compatible or anthropic,
`Settings → AI Agent`) but act **only** through the same validated tool
functions the HTML forms use — never raw SQL or raw Azure SDK calls directly
from agent code. `chats.py` persists conversations across sessions/devices.

### Request lifecycle & types

`models.py` defines `RequestType` (`vnet_new`, `firewall_policy`,
`hub_integration`, `zpa_rnd_routing`, `zpa_other_routing`, `zpa_nmo_routing`,
`subnet_additional`, `vnet_decommission`, `dns`, `aks_cluster`, `vm_create`,
`storage_account_create`, `network_issue`, `other`) and `RequestStatus` (VNET-specific states like
`CIDR_ASSIGNED`/`HUB_INTEGRATED` plus generic per-type states like
`IN_REVIEW`/`RULE_IMPLEMENTED`/`FW_RULES_UPDATED`). Status advances only from
completed portal actions (never manually), except a manual-completion escape
hatch for out-of-band work, which requires a mandatory note. Cancel/Reject
walks the audit trail and reverts every deployed change in dependency-safe
order. `vm_create` is the one exception to "status advances from a single
completed action" — see below, its completion is per-VM-plan-item, not
per-action.

### VM(s) request: the one type with N Azure mutations per action

`vm_create` deploys 1–N VMs per request. Every other type maps one portal
action to one Azure mutation; this one maps a single "Deploy" click to N
independent VM creations, so several pieces work differently from the rest of
the app:

- **Live Azure lookups** (`azure_tools.list_vm_skus` / `list_vm_images` /
  `list_disk_skus` / `list_vm_zones` / `check_vm_quota`, exposed as
  `/api/azure/vm-skus|vm-images|disk-skus|vm-zones|vm-quota`) are
  requester-accessible (`@require_login`, not admin-only — the requester needs
  them before submitting) and return `{ok, data, error}`, never a 500: an
  Azure failure is a normal "form falls back to free text" outcome, not a
  server error. Subscription/region come only from the request body — these
  routes never fall back to a configured hub/spoke subscription, so a
  requester can't enumerate resources without typing one in themselves.
- **Multi-VM naming**: the requester types a base name; deploy always appends
  `-001`, `-002`… zero-padded to `VM_NAME_SUFFIX_DIGITS` (Settings → VM
  Defaults), even for a single VM, so a later request against the same base is
  never ambiguous. `azure_tools.list_existing_vm_indexes()` scans the target
  RG's VM **and** NIC **and** disk names (not just VMs — an orphaned NIC/disk
  from a previously failed deploy collides just as hard) to skip past every
  taken index before assigning new ones.
- **`details["vm_plan"]`** — `[{name, computer_name, zone, nic, os_disk,
  data_disks, status, resource_ids, error}, …]` — is resolved and persisted
  **once**, at first preview (`azure_tools.build_vm_plan()`, called from
  `app._vm_preview()`); re-opening the preview never reshuffles names or
  zones already committed. Windows `computer_name` is derived separately from
  the VM's resource name (`derive_windows_computer_name()`) — Windows' 15-char
  limit is independent of, and much tighter than, the 64-char Azure
  resource-name limit the base name itself is validated against at submit.
- **Admin preview → deploy**: `vm_check`/`vm_deploy` are actions in the shared
  `/api/admin/azure-action` dispatcher — the same one every other type uses,
  no parallel endpoint. `vm_check` resolves/refreshes the plan without
  creating anything; `vm_deploy` loops the plan's still-pending VMs through
  `azure_tools.create_vm()`: RG → NIC (no public IP, ever; no NSG created —
  the subnet's own applies) → **one** VM-creation call with named, inline
  OS/data disks. A separately pre-created disk later `Attach`-ed to the VM
  would never go through Azure's guest-OS provisioning agent, so a custom
  disk name and correct computer-name/SSH-key/password provisioning can only
  coexist by declaring the disks inside the VM's own creation call — see
  `create_vm()`'s docstring.
- **Resumable deploy**: a VM already `status: created` is skipped on
  re-deploy; a failure stops the loop (no rollback of VMs that already
  succeeded) and persists progress after *every* VM, not just at the end, so
  a mid-loop crash still reflects what actually happened. `_auto_advance()`
  has a bespoke `VM_CREATE` branch reading `vm_plan` directly — unlike every
  other type's generic audit-derived done/required set comparison — and
  completes only once every plan item is `created`.
- **Per-VM change ledger**: each successful `create_vm()` gets its own
  `changes.record()` entry with `revert_op="delete_vm"` — reverting one VM
  never touches the others. `delete_vm()` is a single VM-delete call: the NIC
  and every disk were tagged `delete_option="Delete"` at creation, so Azure
  cascades the cleanup itself, never touching the subnet, its NSG, or
  anything else this portal didn't create.
- **Quota**: checked at submit (informational — a warning shown in the form,
  never a submit gate) and again immediately before the deploy loop (a
  **hard** block there, scoped to only the pending/failed VMs, not the
  original full count). No caching — `check_vm_quota()` hits Azure's live
  `usages.list()` every time, since headroom moves while a request sits.
- **`zones: null` is a deliberate, preserved signal, not a bug.** The generic
  `details = {k: v for k, v in … if v not in (None, "")}` line at the top of
  `_create_service_request()` (shared by every non-VNET type) strips `None`
  values, which would otherwise turn "this SKU has no zone support" into the
  same thing as "the requester left zones blank." `_create_service_request`
  captures whether `zones` was explicitly `None` *before* that filter runs and
  re-injects it after, so the distinction survives into the stored `details`
  JSON as an auditable record.
- **`app._cached_vm_skus()`** memoizes `list_vm_skus`'s 1000+-entry scan using
  Flask's `g` — thread-local, cleared after every request. This is
  deliberately **not** a module-level cache: prod runs 3 replicas, and
  anything that outlives one request could go stale or diverge between pods
  (see the local-vs-prod table above). It only avoids re-scanning when the
  *same* incoming request needs SKU data more than once.
- **Password handling**: `VM_REQUIRE_SSH_KEY` (default on) hides password
  auth from the form entirely; when it's off, the requester still never types
  a password — only `auth_mode: "password"` is recorded. The admin enters the
  actual password in the deploy panel, and it travels exactly one hop (the
  `vm_deploy` request body → `create_vm()`'s one API call) — never written to
  `details`, `audit_log`, or `change_log`, and never appears in a `create_vm()`
  return message. `azure_tools._guard` (the shared dry-run decorator every
  mutating function uses) excludes any kwarg whose name contains
  `password`/`secret`/`key`/`token`/`credential` from its dry-run log line and
  simulated response — a real leak path caught before it shipped: without
  this, a dry-run deploy would have echoed the admin's password straight into
  the audit trail's `data` column via `_audit_azure()`.

### Storage Account request: one resource, several sequential sub-steps

`storage_account_create` deploys a single Azure Storage Account, but unlike
every other single-resource type (AKS, VNET) its deploy is a sequence of
several Azure calls against that one resource rather than one call — closer
in spirit to VM(s)' multi-step nature, but for one resource instead of N.

- **Architecture reused wholesale from AKS/VM**: same `RequestType`/
  `RequestStatus` pattern (`STORAGE_ACCOUNT_CREATE` → `STORAGE_DEPLOYED` →
  `COMPLETED`), same `admin_azure_action()` dispatcher (`storage_check`/
  `storage_deploy` actions, no parallel endpoint), same `_vm_lookup_response`
  `{ok,data,error}` shape for requester-facing discovery routes, same
  `_deploy_tags()`-derived tagging, same `changes.py` revert-op registration
  (`delete_storage_account`), same approval-flow integration (just adding the
  type to `RequestType.ALL` gives it a Settings → Approvals row for free —
  `approvals.py` iterates `RequestType.ALL` generically).
- **Reused discovery routes, not duplicated ones**: the VNet/subnet picker
  reuses the existing generic `/api/azure/vnets` and `/api/azure/subnets`
  routes — the same ones AKS and VM already share — rather than adding
  `storage-vnets`/`storage-subnets`. New routes exist only for what didn't
  already have a generic equivalent: `storage-skus`, `storage-identities`,
  `storage-keyvaults`, `storage-keys` (the last one returns each key's
  version list inline, covering both the "Key Picker" and "Key Version"
  picker in one call — no separate versions route).
- **`azure_tools.create_storage_account()`** runs sequentially: resource
  group → the account itself (network rules, identity, encryption all
  applied in that single create call) → blob service properties (versioning,
  change feed, soft delete) → file service properties (share soft delete) →
  each requested container, skipping any that already exist (the same
  resumability spirit as VM's per-item loop, but within one account rather
  than across N). Object replication and a Private Endpoint, if requested,
  are separate, best-effort calls (`create_object_replication_policy`,
  `create_storage_private_endpoint`) run after the main deploy succeeds —
  their failure surfaces as a warning but never fails the deploy, the same
  relationship `aks_link_dns` has to `create_aks_cluster` (a distinct,
  optional post-deploy step, not baked into the main call). A Private
  Endpoint here does **not** link a private DNS zone — that's a separate
  `RequestType.DNS` request, reusing the existing DNS request type rather
  than reimplementing zone-linking inside the storage deploy.
- **Security defaults are hardcoded in `create_storage_account`, not
  settings-editable**: TLS 1.2, HTTPS-only, shared-key access disabled, blob
  public access disabled, infrastructure encryption enabled, network default
  action Deny. `public_network_access` and the allow-lists only ever narrow
  access from that baseline — Settings → Storage Defaults controls SKU/kind/
  region/container-cap guard rails, never these security floors (contrast
  with AKS's `AKS_CMK_ENCRYPTION` etc., which genuinely are
  settings-adjustable guard rails, not security floors).
- **"success" for the change ledger means "the account was created", not
  "every sub-step succeeded"** — a container or blob-properties failure after
  the account exists must not suppress the change-ledger entry (`_guard`-
  wrapped functions only get their `changes.record()` fired when
  `res["success"]` is true), or a real, billable Azure resource would exist
  with no revert path. Sub-step results travel separately via `res["steps"]`
  and `res["all_steps_ok"]`, plumbed into the `azure_action` audit entry's
  `data.all_steps_ok` (a field that's `None` for every other action). Rollback
  is delete-only — Azure has no account-level "restore previous config";
  blob soft-delete/versioning (on by default) is the only recovery path for
  what was inside it, and only within their configured retention window.
- **Completion is a bespoke `_auto_advance()` branch**, not the generic
  done/required-set comparison, for the same reason VM_CREATE has one:
  `"storage_deploy" in done` alone can't distinguish "account created, one
  sub-step still failing" from "everything succeeded" — it reads the latest
  `storage_deploy` audit entry's `data.all_steps_ok` directly to decide
  `STORAGE_DEPLOYED` vs. `COMPLETED`. A dry-run deploy correctly stops at
  `STORAGE_DEPLOYED` (never `COMPLETED`) since `_guard` short-circuits before
  the function body runs, so `all_steps_ok` is never set.
- **No dedicated plan-resolution stage** the way VM has (`_vm_preview`/
  `build_vm_plan`): the account name and full config are already fixed by the
  requester at submission (no auto-numbering/collision-avoidance need), so
  `_storage_preview()` just reshapes `details` for the admin panel — nothing
  is computed or persisted at preview time. "Dry Run" for the real deploy
  needs no bespoke flag either: `AZURE_DRY_RUN` + `create_storage_account`'s
  `@_guard` decorator already provide it, same as every other mutating call.
- **Tag schema extended, not replaced**: `_deploy_tags()` gained a second set
  of governance tags (`ApplicationName`, `BusinessUnit`, `Criticality`,
  `DataClassification`, `Owner`, `Approver`, `Environment`, `ServiceClass`,
  `Sovereignty`) alongside its original lowercase set (`owner`, `env`,
  `criticality`, `project`, `requester`, `creator`). Both coexist because
  Azure tags are case-preserving on write — the two casings are genuinely
  different tag keys, not a rename. Existing request types keep tagging
  exactly as before (the new fields just come back empty/omitted for them);
  Storage (and any future type) can adopt the fuller schema without touching
  what AKS/VM/VNET already write to Azure.
- **Validation is layered like VM's**: offline checks (name/container/IP-rule
  format, option membership, region justification, required-tags presence
  via `TYPE_REQUIRED_DETAILS`) run first in `_validate_storage_request()`,
  then live-Azure checks (real name-availability via
  `check_name_availability`, VNet/subnet existence, Key Vault/key/version
  reachability, replication destination existence) — same staging as VM's
  stage-1 field validation followed by stage-3 live SKU/VNet/image checks.
  `scripts/test_storage_validation.py` covers the offline + live-check logic
  with `azure_tools` stubbed via `unittest.mock`, consistent with this
  repo's no-test-framework convention (see
  `scripts/preview_notification_email.py` for the same import-a-module-
  directly pattern).

### Budget alerts: forecast-gated, not raw-threshold

`budgetalerts.py` deliberately doesn't alert on raw `% of budget` — it
projects month-end spend from the current pace (`projected_pct = raw_pct /
elapsed_month_fraction`) and only fires 70/80/90% thresholds when the
forecast *also* says you'll land over budget (unless already ≥100% now, which
always fires). This suppresses false alarms near month-end; it never invents
new ones. Read the module docstring before changing thresholds — the logic
is intentionally more subtle than "percent crossed a line."

### Notifications: template-first, LLM-drafted as an enhancement

`notifications.py` sends Teams (Power Automate webhook, Adaptive Card format)
and SMTP email notifications for request lifecycle events (submitted, status
changed, CIDR assigned, hub integrated, approval requested/decided, etc.) and
budget-alert emails. Every event has a hardcoded fallback subject/body; when
`NOTIFY_AI_DRAFT` is on, it asks the LLM (reusing `netdiag`'s admin-agent
client) to draft a nicer version, but `_parse_draft()` requires the response
to strictly start with `Subject:` and rejects anything that looks like leaked
reasoning/chain-of-thought — any parse failure or exception silently falls
back to the template, so a misbehaving LLM never blocks or corrupts a
notification. Recipients/webhook/SMTP config live under `Settings →
Notifications`; nothing here is Azure-scoped, so it works identically in
local dev and prod as long as `SMTP_HOST`/`TEAMS_WEBHOOK_URL` are set.

### Network diagnosis is read-only, and says so where it can't see

`netdiag.py` traces a source→destination path using only the Azure control
plane (subnet's associated UDR, DNS, hub firewall rules) — it does **not**
see real effective routes (BGP/peering) without a live NIC call, and says so.
`reachability.py` optionally runs live ping/TCP/curl tests via SSH to a ZPA
connector VM as a proxy for the real path. The connector VM's `networkuser`
account is intentionally unprivileged/read-only (see
`docs/ZPA_CONNECTOR_USER.md` for the exact allow-listed commands and the
optional forced-command SSH wrapper in `scripts/zpa-networkuser-wrapper.sh`).

### Resource Relationship Graph: read-only dependency map, not a request type

`resourcegraph.py` + `/resource-graph` builds an interactive node/edge graph
of Azure resource dependencies (VNET/Subnet/NSG/Route Table/Firewall Policy/
Private Endpoint/Private DNS Zone, VM/NIC/Disk/Availability Set, AKS/Node RG/
Load Balancer/Public IP/Managed Identity, Storage Account/Containers, Key
Vault/CMK) for troubleshooting and impact analysis. Unlike AKS/VM/Storage,
this does **not** fit the `RequestType` lifecycle — there's no approval/
deploy/revert cycle, since nothing here ever mutates Azure. Architecturally
it's modeled on `optimize.py` instead: a standalone module, `@require_superadmin`-
gated, with its own isolated Reader-only SP, a "not configured" gate, and no
`@_guard`/dry-run wrapper anywhere (every call is a `list`/`get`/Resource
Graph query, so `AZURE_DRY_RUN` doesn't apply).

- **Discovery is ARG-reverse-index-backed, not pure forward SDK traversal.**
  A pure "follow only the reference IDs this resource's own properties point
  at" walk can't answer entry points like "start at this Route Table" or
  "start at this Private DNS Zone" — those types don't carry a property
  pointing back at whatever references them. So `build_graph()` runs **one**
  Azure Resource Graph (ARG) query per request (scoped to the subscription,
  and resource group if given), builds a forward adjacency map from the
  declarative `REFERENCE_PATHS` dict (per-type property-path → edge-label
  rules), inverts it into a reverse map, and BFS walks **both** directions —
  so rooting the graph at either end of a relationship works. Typed SDK
  clients (own credential, never `azure_tools._get_credential()`) are used
  only to enrich nodes actually shown and to fetch sub-resources ARG doesn't
  return as rows (blob containers, a private endpoint's DNS zone group, an
  AKS node resource group's LB/Public IP) — see `_expand_subnets`,
  `_expand_aks_node_rg`, `_expand_storage_containers`,
  `_expand_pe_dns_zone_group`. Any nested ARM child resource (type has 2+
  slashes, e.g. `privateDnsZones/virtualNetworkLinks`) also gets an implicit
  `child_of` edge to its parent by ID-segment stripping — otherwise a row ARG
  returns as its own resource has no way back to the parent that "contains"
  it in the UI's sense.
- **Two bugs real-Azure testing caught that mocked/offline testing alone
  wouldn't have**: (1) `forward`'s edge-map keys used the resource ID's
  original casing while `id_map`/`included`/`reverse` all used lowercase —
  Azure doesn't return resource IDs with consistent casing across ARG vs.
  SDK vs. a resource's own canonical `id`, so this silently broke the
  primary forward-direction lookup in `neighbors()` (worked around this
  session by a real deployed VM whose NIC/disk edges came back empty until
  fixed). Fixed by funneling every edge insertion through one `_add_edge()`
  helper that lowercases both ends — never append to `forward[...]`
  directly. (2) ARM sub-resources embedded in an array (a NIC's
  `ipConfigurations[]`, and by the same convention an LB's
  `frontendIPConfigurations[]`, a firewall's `ipConfigurations[]`) wrap their
  own fields in a **nested `properties`** the same way the top-level resource
  does — `ipConfigurations[].subnet.id` doesn't exist on real Azure data; the
  real path is `ipConfigurations[].properties.subnet.id`. Rather than fixing
  every affected `REFERENCE_PATHS` entry by hand (and re-breaking on the next
  one discovered), `_walk()`'s array-expansion step transparently falls
  through into an item's nested `properties` when the key isn't found
  directly. `scripts/test_resourcegraph_validation.py` has regression checks
  for both.
- **Deterministic, hop-level-bounded truncation.** `RESGRAPH_MAX_NODES`
  (default 300) and `RESGRAPH_MAX_HOPS` (default 3) cap graph size. The BFS
  completes an entire hop level before checking the node cap, and stops
  *before* admitting a hop level that would exceed it — so which nodes
  survive is a function of hop distance from the root, never dict/set
  iteration order, and the response's `truncated`/`truncated_at_hop` fields
  say exactly where it stopped.
- **Seed resolution has three modes**, matching the form: a specific
  resource (name, optionally narrowed by type); every resource of a given
  type with no name (e.g. "graph every Route Table in this RG"); or the
  whole scope. The middle case is easy to silently drop — an earlier version
  fell through to "whole scope" whenever no name was given, discarding the
  type filter entirely; fixed before it shipped.
- **Frontend**: `templates/resource_graph.html`, Cytoscape.js + the
  `cytoscape-navigator` minimap extension (both CDN, loaded only in this
  template's `{% block scripts %}`, never globally) — click a node for a
  detail panel, search/highlight, category **and** exact-type filters, PNG
  export (`cy.png()`, native) and raw JSON export. SVG export is out of
  scope for V1 (needs the separate `cytoscape-svg` plugin). Cytoscape
  renders to `<canvas>`, not the DOM — its style values can't be CSS
  `var(--...)` references (caught and fixed before shipping: an edge-label
  color was originally a CSS variable, which Cytoscape simply can't resolve).
- **Enterprise UI polish pass** (2026-08-02): per-exact-type color/icon/size
  styling (`TYPE_STYLE`, hand-authored inline SVG icons — not Microsoft's
  actual Azure icon assets, which aren't freely redistributable), a
  `concentric` layout keyed off a computed importance `level` per node (hub
  VNET centered and sized largest, down to leaf resources on the outer
  ring) — Cytoscape core, no force-directed layout library added — with
  manually-dragged positions persisted to `localStorage` per query scope. A
  custom lightweight tooltip (no `cytoscape-popper`/`tippy.js`) and a
  hover/select neighborhood highlight with an animated dashed-line active
  path (`line-dash-offset`, natively animatable — no extra library) replace
  always-on edge labels. The side panel gained Subscription/Tags (both new
  backend fields, see below) and a client-side-only **Relationship
  Analysis** block: since every edge in this graph consistently means
  "source depends on target," direct/upstream/downstream are just outgoing/
  incoming edge traversal over `GRAPH.edges` already fetched — "impact if
  deleted" is the transitive downstream closure, explicitly scoped-caveated
  to the currently-rendered graph (hop/node caps mean the true
  environment-wide impact could be larger). A health **ring** (node
  `border-color`) is derived from `properties.provisioningState` — real
  signal already returned, not a faked placeholder; genuinely unknown for
  resource types that don't expose it. Selected-node glow uses Cytoscape's
  native `overlay-color`/`overlay-opacity` (canvas nodes have no
  `box-shadow`). Two small, additive `build_graph()` fields power this:
  `hub_id` (the hub VNET's resource ID, string-built once from `HUB_*`
  settings — no extra Azure call) and `tags`/`subscriptionId` added to the
  ARG projection and `_trim_properties()`.

### AI Architecture Advisor: rules decide, LLM explains — not a `RequestType`

`advisor/` (new package) + `/advisor` + `templates/advisor.html` is a guided
intake chat that turns plain-English answers into a Presight-approved
Storage Account request, **prefilling** it (never submitting). Like the
Resource Relationship Graph, this isn't a `RequestType` — there's no deploy/
revert cycle, and unlike every AI feature elsewhere in this app it makes
**zero** Azure SDK calls at all (not even read-only ones — see the
[Resource Relationship Graph](#resource-relationship-graph-read-only-dependency-map-not-a-request-type)
above for the read-only-but-still-calls-Azure case; the advisor doesn't even
do that).

The whole feature is driven by a checked-in knowledge base at `advisor_kb/`
(provenance-tracked to two Microsoft/Kyndryl design documents — see its own
`README.md`), which is the **only** source of architecture defaults, policy
and pattern choices. The non-negotiable, stated as literally as possible so
it doesn't drift:

```
Rules decide.  LLM explains.  Forms validate.  Azure deploys.
```

- **`rules_engine.py`** runs `advisor_kb/rules/storage_decision_matrix.yaml`'s
  seven phases (blockers → escalations → constants → derivations → pattern
  selection → deviations → warnings) in that literal order — deterministic,
  no LLM involved, and its output is authoritative. **Blockers/escalations
  are re-evaluated after every single answer**, not just once at the end of
  the questionnaire — a rule referencing an unanswered field simply doesn't
  fire yet, so this is safe to run incrementally, and it's what makes
  "answering 'no subscription' halts immediately" work rather than only
  after the whole intake finishes.
- **`pattern_matcher.py`** scores `advisor_kb/catalog/*.yaml`'s five
  patterns per the schema's own rules (exclude on disqualify/unmet-required,
  score by preferred-hits, tie-break approved-over-conditional then catalog
  order); if nothing scores above zero, it escalates rather than guessing.
- The LLM (`advisor/prompts.py`, a single-turn `call_llm()` — no tool-calling
  loop, unlike `agent_requester.py`/`agent_admin.py`, since the advisor's LLM
  usage is always "explain this fixed data," never "go look something up")
  is used in exactly three narrow places, all downstream of the rules:
  classifying free-text answers onto the fixed question bank's options,
  filling only the "Why this pattern" prose sentence in the recommendation
  (every other section — the settings table, requests list, deviations,
  warnings — is assembled deterministically in `advisor/recommendation.py`
  from rule output, with nothing left for the LLM to decide), and picking
  between tied patterns only when the KB's own `tiebreak_questions` doesn't
  resolve it. If the LLM is unavailable or fails, every one of these has a
  deterministic fallback — the feature works with **no LLM configured at
  all** (verified: an expired-license 403 from the provider was hit live
  during this build and the recommendation still rendered correctly).
- **Condition language**: the KB's `when`/`skip_if`/`include_if` strings
  (`"purpose in [analytics_datalake]"`, `"performance_evidence is empty"`,
  `"pattern.design.change_feed is defined"`) are evaluated by
  `advisor/condition_eval.py` — a small restricted `eval()` (builtins
  stripped, only the answers/derived namespace exposed) after rewriting the
  KB's few non-Python phrases (`is empty`, `is defined`, `contains`,
  `always`) and quoting bare enum identifiers into string literals. Safe
  because these strings are static content in this repo's own `advisor_kb/`
  files, never user input — not a general-purpose expression language.
- **Session state** (`advisor/session_store.py`) is a new `advisor_sessions`
  table, not `chats.py`'s `agent_chats` — a conversation here is structured
  state (answers-so-far, derived values, selected pattern, escalation flags)
  in one JSON column, not an append-only message transcript, so it doesn't
  fit `chats.py`'s shape. Added to `scripts/sqlite_to_postgres.py`'s
  `TABLES` list like every other raw-SQL table.
- **Prefill handoff**: `/api/advisor/prefill` persists the payload
  server-side and hands back `/requester?advisor_session=<id>` — only the
  session id ever travels in the URL. `requester_page()` (the existing
  route) was extended to look up that session and pass the payload into the
  template; `requester.html`'s prefill JS calls the existing `selectType()`
  then populates matching `[data-detail]` fields generically — no
  hand-written per-field JS mapping, since the field-name translation
  already happened server-side in `advisor/prefill.py`. `_validate_storage_request()`
  is never called by the advisor — prefill only ever populates form inputs
  that remain fully editable; the existing form → `_create_service_request`
  → `_validate_storage_request` path is completely untouched.
- **KB-vs-real-form mismatches found and fixed while wiring this up** (the
  KB was written somewhat independently of this app's actual form markup):
  `identity_type`/`encryption_type` needed translating from the KB's
  semantic values (`UserAssigned`/`CMK`) to the form's actual `<option>`
  values (`user`/`customer_managed`); `storage_premium_temporary`'s
  `Premium_ZRS` SKU had no matching form option at all (fixed by adding it
  to both `config.py`'s `STORAGE_SKUS` and the form's dropdown — a genuine
  pre-existing gap, not new scope); the KB's `ServiceClass` tag mapping
  (Bronze/Silver/Gold/Platinum) doesn't match the form's actual
  `service_class` options (Standard/Business Critical/Mission Critical) —
  left for the user to pick rather than force either vocabulary, since the
  KB itself flags that mapping as "inferred, not quoted from the design
  documents."
- **Mermaid is vendored as the single-file UMD build**
  (`static/vendor/mermaid.min.js`), not the ESM build the KB's own
  `diagrams/README.md` snippet shows — the ESM entry file turned out to be a
  76-byte re-export pointing at 121 separate hash-named chunk files, too
  fragile to vendor reliably. The UMD bundle is one self-contained 3.3 MB
  file with the identical practical outcome (no CDN, no supply-chain
  surface) and is loaded via a plain `<script>` tag instead of
  `type="module"`. `diagram_builder.py` only does placeholder substitution
  (escaping `< > " ` `` in every substituted value) plus two named,
  KB-mandated block-removal rules — the ZPA subgraph when
  `zpa_routing_required` is false, and the datalake diagram's second (blob)
  private-endpoint block unless the analytics engine is confirmed to use it
  too — never generating Mermaid syntax from scratch.
- **`scripts/test_advisor_validation.py`** (assert-based, no pytest, no
  Flask/LLM needed) exercises `condition_eval`/`catalog_loader`/
  `pattern_matcher`/`rules_engine`/`question_engine`/`prefill`/
  `diagram_builder` directly against the real `advisor_kb/` content — not
  synthetic mocks — since the KB itself is as much the thing under test as
  the code that reads it.

### Separate credentials per concern

Four independent service-principal configs, intentionally isolated so a
credential leak or misconfiguration in one can't touch another:
- **Network automation** (`Settings → Azure Credentials`) — Network
  Contributor, used by `azure_tools.py`.
- **Cost dashboard** (`COST_*` settings, `costmgmt.py`) — Cost Management
  Reader + Reader, read-only.
- **Resource Optimizer** (`optimize.py`) — Reader only, read-only, advisory
  findings only (never deletes anything).
- **Resource Relationship Graph** (`RESGRAPH_*` settings, `resourcegraph.py`)
  — Reader only, read-only, no mutations possible.

### Frontend: server-rendered, no build step

Flask + Jinja templates (`templates/`), vanilla CSS/JS (`static/`) — no
React/build pipeline despite what any design brief might reference (see
`docs/BRANDING.md`, which explicitly maps React/Framer-Motion/Lottie asks
onto GSAP/CSS equivalents). `static/css/tokens.css` is the single source of
brand truth; never hard-code colors/fonts in templates. Performance rules in
`BRANDING.md` (no per-card `backdrop-filter`, no scroll-linked JS animation,
particle/video budgets) exist because the animated version originally tanked
scroll FPS on AKS — don't reintroduce those patterns.

## Key docs (read before touching these areas)

- `docs/HOW_IT_WORKS.md` — request lifecycle, Azure execution model, identity.
- `docs/DEPLOYMENT.md` — Helm/k8s deploy, secrets, backup/upgrade/scaling.
- `docs/KEYCLOAK.md` — SSO setup, team/group visibility rules, approval-flow
  dependency chain (Entra `manager` attribute → Keycloak claim → app).
- `docs/BRANDING.md` — design tokens, animation/perf rules, do's/don'ts.
- `docs/ZPA_CONNECTOR_USER.md` — connector VM SSH access model and hardening.
- `docs/AZURE_PERMISSIONS.md` — as needed.
- `docs/GPU_UTILIZATION.md` — **not yet built.** A prerequisites/design doc for
  a future GPU-utilization dashboard + optimizer finding (DCGM exporter +
  Managed Prometheus pipeline). Nothing in the current codebase implements
  this; treat it as a spec to build toward, not existing behavior.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
