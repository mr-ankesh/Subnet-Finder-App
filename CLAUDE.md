# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Network Copilot** (repo name: Subnet-Finder-App) — a Flask portal for day-2
Azure network operations: subnet/VNET allocation, hub-spoke peering, firewall
rules, UDR/routing, ZPA routing, AKS cluster deployment, DNS, cost dashboard,
resource optimizer, and network-issue diagnosis. Originally subnet-only; brand
and scope have broadened to general cloud operations (see `docs/BRANDING.md`
and recent `chore(brand)` commits) — don't assume "network" in an old
identifier means the feature is network-scoped only.

Read `docs/HOW_IT_WORKS.md` first for the actual request-lifecycle and Azure
execution model — it's short and answers most "why is this built this way"
questions better than re-deriving them from code.

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
`changes`, `db_backend`, `search`, `settings_store`, `auth_oidc`, `naming`)
at module level, but imports feature modules **lazily inside the route
functions that use them**: `azure_tools`, `agent_admin`, `agent_requester`,
`approvals`, `budgetalerts`, `costmgmt`, `netdiag`, `optimize`, `reachability`,
`subinventory`, `chats`. This is deliberate — when touching one of these
features, the relevant code lives entirely in its own module; `app.py` is
just the route surface calling into it.

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
`subnet_additional`, `vnet_decommission`, `dns`, `aks_cluster`,
`network_issue`, `other`) and `RequestStatus` (VNET-specific states like
`CIDR_ASSIGNED`/`HUB_INTEGRATED` plus generic per-type states like
`IN_REVIEW`/`RULE_IMPLEMENTED`/`FW_RULES_UPDATED`). Status advances only from
completed portal actions (never manually), except a manual-completion escape
hatch for out-of-band work, which requires a mandatory note. Cancel/Reject
walks the audit trail and reverts every deployed change in dependency-safe
order.

### Budget alerts: forecast-gated, not raw-threshold

`budgetalerts.py` deliberately doesn't alert on raw `% of budget` — it
projects month-end spend from the current pace (`projected_pct = raw_pct /
elapsed_month_fraction`) and only fires 70/80/90% thresholds when the
forecast *also* says you'll land over budget (unless already ≥100% now, which
always fires). This suppresses false alarms near month-end; it never invents
new ones. Read the module docstring before changing thresholds — the logic
is intentionally more subtle than "percent crossed a line."

### Network diagnosis is read-only, and says so where it can't see

`netdiag.py` traces a source→destination path using only the Azure control
plane (subnet's associated UDR, DNS, hub firewall rules) — it does **not**
see real effective routes (BGP/peering) without a live NIC call, and says so.
`reachability.py` optionally runs live ping/TCP/curl tests via SSH to a ZPA
connector VM as a proxy for the real path. The connector VM's `networkuser`
account is intentionally unprivileged/read-only (see
`docs/ZPA_CONNECTOR_USER.md` for the exact allow-listed commands and the
optional forced-command SSH wrapper in `scripts/zpa-networkuser-wrapper.sh`).

### Separate credentials per concern

Three independent service-principal configs, intentionally isolated so a
credential leak or misconfiguration in one can't touch another:
- **Network automation** (`Settings → Azure Credentials`) — Network
  Contributor, used by `azure_tools.py`.
- **Cost dashboard** (`COST_*` settings, `costmgmt.py`) — Cost Management
  Reader + Reader, read-only.
- **Resource Optimizer** (`optimize.py`) — Reader only, read-only, advisory
  findings only (never deletes anything).

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
