# How Network Copilot Works — High-Level Architecture

This document explains how the tool performs deployments in Azure, why it
uses the mechanism it does (vs ARM templates / Bicep / Terraform), and how a
request flows through the system end to end.

---

## 1. The one-paragraph answer

Network Copilot makes Azure changes through the **Azure SDK for Python**
(`azure-mgmt-network`, `azure-mgmt-resource`), which calls the **ARM REST
API** directly — the same management plane that ARM templates, Bicep, the
CLI and Terraform all ultimately talk to. There are **no template files and
no Terraform state**: each admin action (peer, firewall rule, route table…)
is a small, imperative, idempotent API call built from the request's data at
the moment the admin clicks the button. Azure itself is the source of truth;
the app's audit trail records what was done, by whom, and why.

```
Requester ──► Request (form / AI chat)
                  │
Admin ──► clicks action button ──► app.py builds parameters from the request
                  │
            azure_tools.py  ── dry-run guard ──►  Azure SDK (Python)
                  │                                     │
            audit trail ◄───────── result ◄──── ARM REST API ──► Azure resources
```

## 2. Why not ARM templates / Bicep / Terraform?

| | Templates / Terraform | Network Copilot's approach |
|---|---|---|
| Unit of change | Whole stack / module | One action per click (peer, rule, route) |
| State | tfstate / deployment history to keep in sync | None — Azure is queried live |
| Drift | Must be detected & reconciled | Irrelevant — reads happen at action time |
| Approval flow | PR review on code | Human admin per request, in the UI |
| Undo | Re-apply old code | Built-in revert per action (cancel/reject) |

The tool's job is **day-2 operations on shared infrastructure**: adding one
spoke's peering, one firewall rule, one route — dozens of times, requested
by different teams, each needing review, audit and sometimes revert. That's
a workflow problem more than a provisioning problem, so imperative SDK
calls with per-action auditing fit better than stack-based IaC.

The two coexist cleanly: the **hub itself** (hub VNET, firewall, gateways)
can be — and typically is — provisioned by Terraform/Bicep. Network Copilot
only *attaches things to it*: peerings, rules inside an existing policy,
routes inside existing tables, spoke VNETs. It never owns or rewrites the
hub's own definition.

## 3. What each admin action does in Azure

| UI action | azure_tools function | ARM resource touched |
|---|---|---|
| Deploy VNET | `create_spoke_vnet` | Resource group (create if missing), VNET + N carved subnets |
| Peer Hub | `peer_hub_vnet` | `virtualNetworkPeerings` on both spoke and hub VNETs |
| Allow Internet / internet rule | `allow_internet_rule`, `add_firewall_*_rule` | Rule inside a Firewall Policy rule collection |
| Firewall request apply | `add/replace/remove_firewall_rule` | Same, targeted at the admin-selected RCG/collection |
| Gateway / ZPA route | `add_route_to_table` | Route in a hub route table (next hop: firewall IP) |
| Spoke route table | `create_route_table` + `add_route_to_table` + `assign_route_table_to_subnet` | New UDR with default routes, associated to chosen subnets |
| Decommission | `delete_hub_spoke_peerings`, `remove_routes_by_prefix`, `remove_firewall_rule`, `delete_spoke_vnet` | Deletes in dependency-safe order |
| Deploy VM(s) | `create_vm`, looped once per VM in the request | Resource group, one NIC (no public IP), one VM per loop iteration with named OS/data disks declared inline |
| VM revert | `delete_vm` | The VM — its NIC and disks were tagged `delete_option=Delete` at creation, so Azure cascades their removal |
| Deploy Storage Account | `create_storage_account` | Resource group, storage account (network rules/identity/encryption applied inline), blob/file service properties, then each requested container |
| Storage revert | `delete_storage_account` | The storage account — delete-only, no account-level restore exists in Azure |
| Cancel/Reject revert | the matching `delete_*`/`remove_*` functions | Undoes exactly what the audit trail says was deployed |

VM(s) is the one exception to "one click, one Azure call": deploying N VMs
loops `create_vm` N times, stopping at the first failure without rolling back
the VMs that already succeeded — so a single click can leave a partial,
resumable result, and the change ledger gets one independent, revertable
entry per VM rather than one entry for the click. See CLAUDE.md's "VM(s)
request" section for the full model (naming/collision resolution, the
`vm_plan` persistence, quota gating, password handling).

Storage Account deploy is a single click mapping to several sequential Azure
calls (account → blob/file service properties → each container), but unlike
VM(s) it's still one resource, not N independent ones: the "change" recorded
to the ledger covers the storage account itself the moment it's created, even
if a later sub-step (a container, blob properties) fails — otherwise a real,
billable Azure resource could exist with no revert path. A sub-step failure
is instead surfaced via `all_steps_ok` on the audit entry, which gates
`STORAGE_DEPLOYED → COMPLETED` (re-running Deploy retries only what's left —
containers already created are skipped). Object replication and a private
endpoint, if requested, are separate best-effort steps after the main deploy;
their failure never fails the deploy itself. See CLAUDE.md's "Storage
Account request" section for the full model.

All mutating functions share three behaviors:

1. **Dry-run guard** — when `AZURE_DRY_RUN` is on (the default), the call is
   simulated and returns a `[dry-run]` message; nothing reaches Azure.
2. **Idempotence** — creates use `begin_create_or_update`; deletes treat
   "not found" as success, so retries and reverts are safe.
3. **Audit** — every call is recorded (`azure_action` / `azure_revert`)
   with actor, request, parameters and outcome. The audit trail also drives
   the UI (done-state buttons) and auto-status transitions.

## 4. Identity & permissions

The app authenticates to ARM with either:

- **Service Principal** (`ClientSecretCredential`) — tenant/client/secret
  from Settings (secret encrypted at rest), or
- **Managed Identity** (`ManagedIdentityCredential`) — when running on AKS,
  no secret needed; select it in Settings → Azure Credentials.

Grant the identity **Network Contributor** on the hub resource group and on
the spoke scopes it manages. "Test Connection" in Settings verifies access
by reading the hub VNET (read-only, works even in dry-run).

## 5. Request lifecycle (end to end)

0. **(Optional) Advise** — a requester unsure what to ask for can start at
   `/advisor` instead of the form, for Storage, Kubernetes, VMs, Database or
   Application Gateway (a menu, asked first). It's a separate guided-intake
   step *before* Submit, not a new lifecycle stage of its own: a fixed
   rules engine (`advisor/`, driven by the checked-in `advisor_kb/`
   knowledge base) picks a Presight-approved pattern for the chosen service
   and prefills the matching form — the LLM only narrates that decision,
   never makes it. Postgres and App Gateway don't have a dedicated request
   type yet, so those two prefill into "Other" instead, with the
   recommendation composed into its description field. It hands off to step
   1 exactly like a human filling the form themselves; every prefilled field
   stays editable and nothing is submitted until the requester does so from
   the normal form. See `CLAUDE.md` → "AI Architecture Advisor" for the full
   design. `/advisor`'s mode picker offers a second path alongside the
   single-service one above: describing a whole environment ("10 VMs, a
   cluster, a database") instead of one resource. That path COMPUTES a
   network plan (real subnet arithmetic, an InfoSec gate when exposure is
   public, an ordered build-wave sequence) rather than selecting a catalog
   pattern — still hands off the same way, into the same per-request-type
   forms, just several of them in sequence instead of one. See `CLAUDE.md`
   → "Environment composer" for the full design. With `ADVISOR_CHAT_HISTORY_ENABLED`
   on (Settings → Advisor), `/advisor` is a persistent, resumable conversation
   rather than a single-shot flow — history, free-form "what does X mean?"
   mid-intake, and correcting an earlier answer, for both the single-service
   and environment paths above. Off, it's the original single-shot flow,
   unchanged. See `CLAUDE.md` → "Persistent, conversational chat" for the
   full design.
1. **Submit** — requester uses the form or the AI chat agent. Both paths hit
   the same validated creation code (subnet fit, FQDN-only application
   rules, ports/protocol parsing — bad input is rejected at submission, not
   at deployment).
2. **Review** — admin opens the request; type-specific action panel shows
   exactly the steps that request needs (e.g. the internet action matches
   what the requester chose: full / network rule / application rule / none).
3. **Execute** — each button = one Azure call (see table above). Firewall
   changes first run a **coverage check**: existing rules are analyzed by
   source/destination/ports (including `*` and CIDR/wildcard supersets,
   priority-aware deny resolution) so already-allowed traffic never gets a
   duplicate rule.
4. **Status moves itself** — statuses advance from completed portal actions
   (never manually); a manual-completion escape hatch exists for work done
   outside the portal, with a mandatory note. VM(s) requests are the one
   exception: completion is read off the per-VM `vm_plan` (every VM must be
   `created`), not off a single completed action, since one request can need
   several deploy attempts to finish.
5. **Close or revert** — completion notifies the requester (Teams + email).
   Cancel/Reject shows every deployed change and automatically reverts them
   in dependency order.

## 6. Data & configuration

- **Database** holds requests, subnet inventory, VNET info, settings
  overrides, firewall collection definitions, the audit trail and the change
  ledger. Two backends, chosen by `DATABASE_URL`:
  - **SQLite** (default, `data/requests.db` on the PVC) — single writer, so
    one replica.
  - **PostgreSQL** (set `DATABASE_URL`) — the app is otherwise stateless
    (cookie sessions, shared secret), so it scales to N replicas with rolling
    upgrades. The ORM and the raw-SQL modules both go through a backend
    abstraction (`db_backend.py`); migrate existing data with
    `scripts/sqlite_to_postgres.py`.
- **Config resolution**: every setting resolves live as
  **DB override → environment variable → default**. The Settings UI writes
  DB overrides; env vars (ConfigMap/Secret) are only bootstrap defaults.
  Secrets are Fernet-encrypted with a key derived from `FLASK_SECRET_KEY`.
- **AI agents** (requester + admin chat) call the configured LLM
  (Anthropic / OpenAI-compatible, Settings → AI Agent / LLM) and act through
  the same validated tool functions as the forms — never raw SQL or raw
  Azure calls.

## 7. Deploying the app itself

The *application* ships as a container (gunicorn, non-root) deployed to
Kubernetes via the **Helm chart** in `helm/subnet-manager/` (or raw
manifests in `k8s/`) — see `docs/DEPLOYMENT.md`. A fresh deployment starts
with an **empty database**: no requests or allocations carry over from any
other environment. The first post-deploy step is importing the real subnet
inventory at `/admin/inventory` (the home page prompts for this).

So, to be precise about the two meanings of "deployment":

| "Deployment" of… | Mechanism |
|---|---|
| The app itself | Docker image + Helm chart on AKS |
| Network changes in Azure | Python Azure SDK → ARM REST API, per-action, dry-run-guarded, audited |
