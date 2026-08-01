# Project Overview

> Read this first. For deep architectural detail see `CLAUDE.md` (repo root)
> and `docs/HOW_IT_WORKS.md` — this file is the compressed pointer to both.

## Purpose

**Presight AlMadar 360** (repo name: Subnet-Finder-App) is an internal
self-service Flask portal for day-2 Azure network + cloud operations.
Originally subnet-allocation-only, scope broadened to general cloud ops —
don't assume "network"/"subnet" in an old identifier means network-scoped
only (see `docs/BRANDING.md`).

## Key Capabilities

- Subnet/VNET allocation from fixed CIDR pools (the original namesake feature)
- Hub-spoke VNET peering + hub integration
- Firewall policy rule management
- UDR / routing management
- ZPA (Zscaler Private Access) routing requests + live reachability testing
- AKS cluster deployment (with CMK/disk-encryption options, node OS SKU choice)
- **VM(s) deployment** (1–N VMs per request — newest, most complex request type)
- DNS / Private DNS zone linking
- VNET decommissioning (reverse of allocation)
- Network issue diagnosis (read-only path tracing)
- Cost dashboard + budget alerts (forecast-gated, not raw-threshold)
- Resource optimizer (idle/orphaned/underutilized resource findings, advisory only)
- Line-manager approval flow (Entra/Keycloak-dependent, auto-disables if unwired)
- Two AI chat agents (requester + admin), tool-calling only — never raw SQL/Azure

## Main Modules

| Module | Responsibility |
|---|---|
| `app.py` | Monolith: ~110 `@app.route` handlers, route/view logic, wiring. Core imports at module level; feature modules lazy-imported inside routes. |
| `azure_tools.py` | All imperative Azure SDK calls (ARM REST API), one idempotent call per admin action, gated by `AZURE_DRY_RUN`. |
| `models.py` | Flask-SQLAlchemy ORM — 7 tables incl. `RequestType`/`RequestStatus` enums, `SpokeRequest`. |
| `db_utils.py` | Raw-SQL helpers for `SpokeRequest`-adjacent queries via `db_backend`. |
| `db_backend.py` | Dependency-free SQLite/Postgres translation layer (`?`→`%s`, PRAGMA no-ops, row normalization). |
| `config.py` | `SETTINGS_SPEC` — single source of truth for `/admin/settings`; DB override → env var → default resolution chain. |
| `settings_store.py` | Persists UI-edited settings to `app_settings` table (Fernet-encrypted secrets). |
| `audit.py` / `changes.py` | Audit trail + change ledger (drives revert/cancel). |
| `approvals.py` | Line-manager approval gate with dependency preflight check. |
| `auth_oidc.py` | Keycloak SSO (Authlib) — populates `session["is_admin"]`/`session["admin_name"]`. |
| `agent_requester.py` / `agent_admin.py` | Chat agents, tool-calling only. |
| `chats.py` | Persists agent conversations across sessions/devices. |
| `netdiag.py` / `reachability.py` | Read-only path diagnosis; live SSH-based reachability via ZPA connector VM. |
| `costmgmt.py` / `budgetalerts.py` / `optimize.py` | Cost dashboard, forecast-gated budget alerts, resource optimizer. |
| `notifications.py` | Teams (Adaptive Card) + SMTP email, template-first with optional LLM-drafted enhancement. |
| `subinventory.py` | Subscription inventory (own raw-SQL table, own `ensure_table()`). |
| `naming.py` / `search.py` | Naming conventions; search helpers. |

## Important Workflows

1. **Request lifecycle**: requester submits one of 13 `RequestType`s →
   admin reviews/deploys via Azure actions → status auto-advances from
   completed actions → optional manual-completion escape hatch (mandatory
   note) → cancel/reject reverts every deployed change in dependency-safe
   order via the change ledger.
2. **CIDR allocation**: `POOLS` dict of base CIDRs → `compute_free_blocks()`
   subtracts used/reserved subnets → `candidates_from_free()` enumerates
   concrete CIDRs → `allocate_subnet()` re-validates and writes through
   `db_utils.allocate_subnet_db()` (source of truth: `subnet_records` table).
3. **VM(s) deploy** (see `CLAUDE.md` "VM(s) request" section for full detail):
   preview resolves+persists a per-VM plan once (`build_vm_plan`), deploy loop
   is resumable/per-VM-revertable, quota checked live before every deploy.
4. **Approval flow**: routes to the specific requester's line manager (Entra
   `manager` claim via Keycloak), inert until every dependency-chain check
   passes; falls back to a configured approver or any super-admin.

## Important Technologies

- **Backend**: Flask + Flask-SQLAlchemy, gunicorn in prod
- **DB**: SQLite (local dev, single writer) or PostgreSQL (`DATABASE_URL` set, prod, N replicas)
- **Auth**: local password (dev) or Keycloak SSO/Authlib (prod)
- **Azure**: Azure SDK for Python, direct ARM REST calls — no Terraform/Bicep/IaC
- **Frontend**: server-rendered Jinja templates, vanilla CSS/JS — no build step, no React
- **AI**: OpenAI-compatible or Anthropic LLM, configurable per `Settings → AI Agent`
- **Deploy**: Docker + Helm chart (`helm/subnet-manager/`) or raw `k8s/` manifests, AKS target
- **Knowledge graph**: `graphify-out/` — use `graphify query/path/explain` for codebase navigation before grepping raw source

## High-Level Architecture

```
Requester/Admin browser
        │
        ▼
   Flask app.py (~110 routes)
   ├── models.py (ORM: SpokeRequest, RequestType/Status, 7 tables)
   ├── db_backend.py ── SQLite (dev) | PostgreSQL (prod, 3 replicas)
   ├── config.py + settings_store.py (DB override → env → default)
   ├── audit.py + changes.py (audit trail + revertable change ledger)
   ├── auth_oidc.py (local password | Keycloak SSO, switched live)
   ├── approvals.py (line-manager gate, dependency-checked)
   ├── azure_tools.py ──► Azure ARM REST API (imperative, dry-run gated)
   ├── agent_requester.py / agent_admin.py ──► LLM (tool-calling only)
   └── notifications.py ──► Teams webhook + SMTP
```

No IaC: Azure itself is the source of truth. The app only *attaches* to an
existing hub — it never owns the hub's own definition.

## See Also

- `CLAUDE.md` (repo root) — the canonical, detailed architecture guide. Read
  it whenever this overview isn't enough.
- `docs/HOW_IT_WORKS.md` — request lifecycle + Azure execution model
- `docs/DEPLOYMENT.md`, `docs/KEYCLOAK.md`, `docs/BRANDING.md`,
  `docs/ZPA_CONNECTOR_USER.md`, `docs/AZURE_PERMISSIONS.md`,
  `docs/GPU_UTILIZATION.md` (design-only, not built)
