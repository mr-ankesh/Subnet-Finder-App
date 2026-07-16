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
| Cancel/Reject revert | the matching `delete_*`/`remove_*` functions | Undoes exactly what the audit trail says was deployed |

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
   outside the portal, with a mandatory note.
5. **Close or revert** — completion notifies the requester (Teams + email).
   Cancel/Reject shows every deployed change and automatically reverts them
   in dependency order.

## 6. Data & configuration

- **SQLite** (`data/requests.db`, WAL mode) holds requests, subnet
  inventory, VNET info, settings overrides, firewall collection
  definitions and the audit log. Single writer → single replica.
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
