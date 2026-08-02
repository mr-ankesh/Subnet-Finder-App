# Architecture Decisions

> Append-only log. Never delete an entry — if a decision is later reversed,
> add a new entry linking back to the one it supersedes. Full rationale for
> most of these lives in `CLAUDE.md`; this file is the dated index of *why*.

---

## 2026-07-18 — PostgreSQL as an optional backend, SQLite stays default

**Decision:** Add `db_backend.py` as a dependency-free translation layer
(`?`→`%s` placeholders, PRAGMA no-ops, row normalization) so the same
raw-SQL modules work on both SQLite and Postgres. SQLite remains the default
for local dev; Postgres is opt-in via `DATABASE_URL`.

**Why:** Horizontal scaling in prod (AKS, multiple pods) requires a
multi-writer DB — SQLite is single-writer. Rather than fork the raw-SQL
modules per backend, one translation layer keeps them portable.

**Tradeoff:** Every new raw-SQL table/column must be written to work on both
backends, and existing Postgres deployments need an explicit migration path
— schema changes are not automatically applied to already-deployed Postgres
data (`db.create_all()` only creates new tables). This bug class has
recurred (see the `approval_state` backfill fix, 2026-07-31).

---

## 2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent

**Decision:** `azure_tools.py` calls the Azure SDK directly against ARM REST,
one small idempotent call per admin action, gated by `AZURE_DRY_RUN`. No
Terraform/Bicep/ARM templates anywhere in the repo.

**Why:** The app only *attaches* to pre-existing hub infrastructure
(peerings, firewall policy rules, routes) — it never owns the hub's
definition. Azure itself stays the single source of truth; nothing is
tracked in local state that could drift.

**Tradeoff:** No plan/diff step before a mutation (unlike Terraform) — the
change ledger (`changes.py`) + audit trail (`audit.py`) are the substitute
safety net, driving revert/cancel instead of a rollback-by-reapplying-state
model.

---

## 2026-07-30 — Line-manager approval routing, dependency-gated

**Decision:** `approvals.py` routes approval to *that specific requester's*
line manager (sourced from an Entra ID `manager` attribute, mapped through a
Keycloak claim) — never a single global approver. The feature runs a
preflight dependency check and auto-disables with a specific missing-
prerequisite message if the Entra→Keycloak→token claim chain isn't fully
wired.

**Why:** A generic global-approver model doesn't reflect real org approval
hierarchy, but making the feature *look* enabled when the underlying SSO
plumbing isn't configured would silently misroute or block requests. Fail
inert, not silently wrong.

**Tradeoff:** The feature is fully unverifiable in local dev (no Keycloak
there) — must be reasoned through or checked with an isolated/mocked test,
never trusted just because it "worked" locally. Fallback path (no manager on
file, or non-SSO) routes to a configured fallback approver or any
super-admin, flagged as fallback-routed; self-approval is blocked.

---

## 2026-07-26 — Budget alerts are forecast-gated, not raw-threshold

**Decision:** `budgetalerts.py` projects month-end spend from current pace
(`projected_pct = raw_pct / elapsed_month_fraction`) and only fires 70/80/90%
thresholds when the forecast *also* says you'll land over budget (unless
already ≥100% now, which always fires).

**Why:** Raw `% of budget crossed` alarms falsely near month-end (e.g. 70% of
budget spent by day 3 of a 30-day month is fine; 70% spent by day 25 is
not). Forecast-gating suppresses the false positives without inventing new
alert classes.

**Tradeoff:** Slightly more complex logic than a threshold comparison —
intentional; do not "simplify" this back to raw-threshold without
re-reading the module docstring first.

---

## 2026-03-26 — AI agents are tool-callers only, never raw SQL/Azure access

**Decision:** `agent_requester.py` and `agent_admin.py` call the configured
LLM but act **only** through the same validated tool functions the HTML
forms use.

**Why:** Keeps the LLM's blast radius identical to a human using the UI —
no path for a prompt-injected or hallucinated action to run arbitrary SQL or
Azure SDK calls that bypass validation, audit, or the change ledger.

**Tradeoff:** Every new agent capability requires a corresponding validated
tool function; the agent can't be given ad-hoc access as a shortcut.

---

## 2026-07-31 — VM(s) deployment: plan resolved once, persisted, resumable

**Decision:** For `RequestType.VM_CREATE`, the per-VM deploy plan (names,
zones, NIC/disk names, Windows computer names) is resolved once at first
preview and persisted into `details["vm_plan"]`; re-opening the preview never
reshuffles it. Deploy loops the plan's pending VMs one at a time, persisting
progress after *every* VM (not just at the end), stops on first failure, and
never rolls back VMs that already succeeded — each gets its own
`changes.record()` entry with `revert_op="delete_vm"` so it's independently
revertable.

**Why:** A single "Deploy" click maps to N independent Azure mutations
(unlike every other request type, which maps one action to one mutation). If
names/zones were re-resolved on every preview, a re-opened admin panel could
silently rename a VM already referenced elsewhere. If a mid-loop crash rolled
back nothing, or rolled back everything, either partial state would be
wrong.

**Tradeoff:** More bookkeeping than the rest of the app's action model
(`_auto_advance()` needed a bespoke `VM_CREATE` branch reading `vm_plan`
directly, instead of the generic audit-derived done/required set comparison
every other type uses). Custom disk names + correct computer-name/SSH-
key/password provisioning can only coexist by declaring disks inline in the
VM's own creation call (a separately attached disk skips the guest-OS
provisioning agent) — see `create_vm()`'s docstring in `azure_tools.py`.
**Status:** implemented, committed 2026-08-01 (commit `65df6b9`) after partial
verification (see `current-state.md`).

---

## 2026-07-31 (approx) — Password never persisted for VM auth

**Decision:** When `auth_mode == "password"`, the admin's plaintext password
travels exactly one hop (deploy request body → `create_vm()`'s one API call)
— never written to `details`, `audit_log`, or `change_log`, and never
appears in a `create_vm()` return message. `azure_tools._guard` (the shared
dry-run decorator) excludes any kwarg matching
`password`/`secret`/`key`/`token`/`credential` from its dry-run log line and
simulated response.

**Why:** Without the `_guard` exclusion, a dry-run deploy would have echoed
the admin's password straight into the audit trail's `data` column via
`_audit_azure()` — caught before shipping, not after.

**Tradeoff:** None meaningful — this is a strict security requirement, not a
design tradeoff. `VM_REQUIRE_SSH_KEY` defaults on, hiding password auth from
the requester form entirely; when off, the requester still never types a
password, only records `auth_mode: "password"`.

---

## 2026-07-15 → 2026-07-29 — Rebrand: Subnet Manager → Network Copilot → Presight AlMadar 360

**Decision:** Two-step rebrand — "Subnet Manager" → "Network Copilot"
(2026-07-15), then "Network Copilot" → "Presight AlMadar 360" (2026-07-26,
with portal-area renames like Request Center + Tickets following on
2026-07-29); scope broadened from network-only to general cloud operations
across copy, AI agent prompts, and navigation groupings (Operations / ZPA
Analyzer / Governance).

**Why:** Product scope had already grown beyond networking (cost dashboard,
optimizer, AKS/VM deployment); the old name and framing undersold it.

**Tradeoff:** Old identifiers (`subnet_manager` Helm release name, repo name
`Subnet-Finder-App`, module names like `subinventory.py`) intentionally were
**not** renamed — `docs/BRANDING.md` explicitly notes not to assume
"network"/"subnet" in an old identifier means the feature is scoped that
way. Renaming identifiers repo-wide was judged not worth the churn.

---

## 2026-08-01 — Storage Account tag schema: extend `_deploy_tags`, don't replace

**Decision:** `_deploy_tags()` (app.py) gained a second set of governance tags
(`ApplicationName`, `BusinessUnit`, `Criticality`, `DataClassification`,
`Owner`, `Approver`, `Environment`, `ServiceClass`, `Sovereignty`) alongside
its original lowercase set (`owner`, `env`, `criticality`, `project`,
`requester`, `creator`). Both sets coexist in the same function.

**Why:** The Storage Account feature's spec called for a 9-tag governance
schema, but the existing tagging system (shared by AKS/VM/VNET) already uses
different, lowercase keys. Azure tags are case-preserving on write — `Owner`
and `owner` are two different tag keys on the same resource, not one tag
with two spellings — so there was no way to "reuse" the old schema for the
new one without either dropping fields the spec asked for, or silently
duplicating tags. Extending the shared function with the new keys (rather
than forking a Storage-specific tagging function) keeps one code path for
"how does this app tag things," while every existing request type keeps
tagging exactly as it did before (the new fields just come back empty for
types that don't collect them — `_tags()`'s existing empty-value drop
already handles that).

**Tradeoff:** Two tag vocabularies now exist on already-deployed resources
depending on when/what deployed them. No retroactive re-tagging was done or
planned — accepted as the cost of introducing a fuller schema without a
disruptive migration. Confirmed with the user before implementation — this
was a real fork, not an obvious call, since the alternative (map new fields
onto old keys, drop the rest) was also viable and less disruptive but
delivered a smaller tag set than what was asked for.

---

## 2026-08-01 — Storage Account rollback: delete-only, no "restore previous state"

**Decision:** `delete_storage_account()` (revert op) deletes the storage
account outright. There is no attempt to restore a previous configuration.

**Why:** Azure has no account-level "undo my last config change" API — the
only built-in recovery primitives are blob-level (soft delete, versioning),
which the feature already enables by default. Promising a config-level
restore that Azure doesn't support would be a real correctness gap dressed
up as a feature; every other revert op in this app (`delete_vm`,
`delete_aks_cluster`, `delete_spoke_vnet`) is equally delete-only, so this
matches existing architecture rather than deviating from it.

**Tradeoff:** None meaningful — this is a platform constraint, not a design
choice with a real alternative. The admin UI and `CLAUDE.md` both say this
plainly rather than implying a restore capability that isn't there.

---

## 2026-08-01 — Storage Account discovery: reuse `/api/azure/vnets`/`subnets`, don't duplicate

**Decision:** The Storage Account VNet/subnet picker reuses the existing
generic `/api/azure/vnets` and `/api/azure/subnets` routes instead of adding
`storage-vnets`/`storage-subnets` (which the original feature spec listed
by name).

**Why:** AKS and VM already both share these exact two routes for their own
VNet/subnet pickers — they're already generic (subscription/RG/VNet params,
not type-specific in any way). Building parallel routes to satisfy a literal
endpoint-naming list would have duplicated working, shared code for no
functional gain, directly contradicting "reuse shared components wherever
possible" from the same feature request.

**Tradeoff:** None — this is strictly less code with identical behavior.
Flagged explicitly to the user during planning (not silently done) since it
diverges from the literal spec text, even though it better serves the
spec's own stated intent.

---

## 2026-08-01 — Storage Account completion/ledger: "success" means "resource exists," not "every sub-step ok"

**Decision:** `create_storage_account()`'s top-level `success` reflects
whether the storage account itself was created — not whether every sub-step
(blob properties, each container, object replication) also succeeded.
Sub-step results travel separately via `res["steps"]` and
`res["all_steps_ok"]`, which `_auto_advance()` reads directly off the latest
audit entry to decide `STORAGE_DEPLOYED` vs. `COMPLETED`.

**Why:** The shared `_record_change()`/`_audit_azure()` helpers only record
a change-ledger entry (and thus a revert path) when `res["success"]` is
true. If `success` had instead meant "everything succeeded," a container
creation failure after the account was already real in Azure would leave
that billable resource with no ledger entry and no revert path — an orphan.
Decoupling "is this revertable" from "is this fully done" avoids that gap
without touching the shared helpers' semantics for every other request type.

**Tradeoff:** `_auto_advance()` needed a bespoke `STORAGE_ACCOUNT_CREATE`
branch reading `all_steps_ok` off the raw audit entry directly, rather than
the generic done/required-set comparison every simpler type uses — the same
kind of exception `VM_CREATE`'s bespoke branch (reading `vm_plan` directly)
already established as precedent in this codebase.

---

## 2026-08-02 — Resource Relationship Graph: ARG reverse index, not pure forward SDK traversal

**Decision:** New read-only module `resourcegraph.py` builds its dependency
graph from **one Azure Resource Graph (ARG) query per request**, then builds
a forward adjacency map from a declarative `REFERENCE_PATHS` dict and
inverts it into a reverse map; the BFS walks both directions. Typed SDK
calls (own isolated `RESGRAPH_*` Reader-only SP, never
`azure_tools._get_credential()`) are used only to enrich shown nodes and to
fetch the handful of sub-resources ARG doesn't return as rows (blob
containers, a PE's DNS zone group, an AKS node RG's LB/Public IP).

**Why:** The feature was originally scoped as "SDK-based rooted traversal"
(the user's explicit choice over a pure ARG/KQL approach). But a pure
forward walk — follow only the reference IDs a resource's own properties
point at — can't answer roughly half the form's own entry points: starting
the graph at a Route Table, an NSG, a Public IP, or a Private DNS Zone finds
nothing, because none of those types carry a property pointing back at what
references them. The Private-Endpoint-to-DNS-zone chain (one of the
feature's five required verification checks) is exactly this case. The ARG
reverse index closes the gap with one extra Azure call per request, not a
change to the "SDK-rooted" approach the user approved — SDK calls still do
all the node enrichment and the sub-resource fetches ARG can't provide.

**Tradeoff:** The module now depends on ARG's `Resources` table shape
(lowercase `type` strings, `properties` as returned by ARM) rather than only
on typed SDK client objects — a second Azure surface to track compatibility
with, versus a single SDK dependency. Documented in `CLAUDE.md` so a future
session doesn't "fix" it back to pure-SDK-only and reintroduce the gap.

---

## 2026-08-02 — Resource Relationship Graph: separate isolated Reader-only SP (4th credential set)

**Decision:** `RESGRAPH_TENANT_ID`/`CLIENT_ID`/`CLIENT_SECRET` under a new
`Settings → Resource Graph` tab — a fourth fully independent service
principal, alongside network automation, Cost, and Optimizer. Not reused
from any of the other three.

**Why:** User's explicit choice, matching this codebase's established
pattern (`CLAUDE.md` → "Separate credentials per concern") — a credential
leak or misconfiguration in a read-only diagnostic tool should never be able
to touch the automation credential that actually mutates Azure.

**Tradeoff:** A brand-new deployment needs a 4th SP provisioned (Reader
role only) before this feature does anything — same "not configured" gate
UX as Optimizer, no functional gap, just one more thing to set up.

---

## 2026-08-02 — Real-Azure testing caught two bugs offline mocked tests alone missed

**Decision/finding:** Two real bugs surfaced only once `resourcegraph.py`
was pointed at real Azure data (a temporary VM deployed via the existing
VM_CREATE feature specifically for this verification, then reverted):
(1) the `forward` edge map's keys used original-case resource IDs while
`id_map`/`included`/`reverse` all used lowercase, silently breaking the
primary forward-direction neighbor lookup (Azure doesn't return resource IDs
with consistent casing across ARG vs. SDK vs. a resource's own canonical
`id`); (2) ARM sub-resources embedded in an array (a NIC's
`ipConfigurations[]`, and by the same ARM convention likely an LB's
`frontendIPConfigurations[]`, a firewall's `ipConfigurations[]`) wrap their
own fields in a *nested* `properties`, not flat on the array item —
`REFERENCE_PATHS` entries written against the documented/assumed shape
(`ipConfigurations[].subnet.id`) silently found nothing on real data.

**Why this matters beyond the two fixes:** both bugs passed a 31-check
mocked offline test suite cleanly, because the mocks were built from the
same (wrong) assumptions as the code. Neither would have been caught without
deploying one real, cheap resource (Standard_B1s VM) and inspecting its
actual ARG row shape. Fixed via (1) a single `_add_edge()` helper that all
edge insertion now goes through (lowercases both ends — never append to
`forward[...]` directly), and (2) `_walk()` transparently falling through
into an item's nested `properties` when a key isn't found at the top level,
rather than special-casing every affected reference path by hand.

**Tradeoff:** None — pure bug fixes, both now covered by regression checks
in `scripts/test_resourcegraph_validation.py`. Recorded here mainly as a
process note: for any future module doing property-path extraction against
raw Azure API responses, budget for at least one real-resource smoke test
before trusting the offline mocks, since the ARM API's actual JSON shape
(nested-properties-on-sub-resources, inconsistent ID casing) is exactly the
kind of detail that's easy to get wrong from documentation/memory alone.

---

## 2026-08-02 — Resource Relationship Graph UI polish: concentric layout + hand-drawn icons, no new layout/icon library

**Decision:** For the enterprise-UI-polish pass, use Cytoscape core's
built-in `concentric` layout (keyed off a computed per-node importance
`level`, hub VNET highest/most-central) instead of adding a force-directed
layout extension (`cytoscape-fcose`), and hand-author a small set of inline
SVG icons for the ~9 requested resource types instead of pulling in an
icon-font/asset CDN.

**Why:** Both were explicit trade-offs put to the user before implementing
(layout: concentric-only vs. concentric+fcose; the user chose concentric-
only). `concentric` gives the literal "hub in center, spokes radial" ask
deterministically with zero new dependencies. For icons, Microsoft's actual
Azure icon set isn't freely redistributable, and pulling in a generic
icon-font would still need per-icon mapping work with no real fidelity gain
over a handful of simple line-art SVGs baked directly into the JS as data
URIs — same "no build step, self-contained" spirit as the rest of this
codebase.

**Tradeoff:** `cytoscape-navigator` (minimap) was still unavoidable — no
core Cytoscape equivalent exists — so this pass isn't 100% dependency-free,
just minimal. The concentric layout is also less optimal than a true
force-directed layout at minimizing edge crossings in a single dense ring
(e.g. many subnets under one spoke VNET); acceptable since the primary ask
was hub-centric hierarchy, not globally-optimal crossing minimization.

---

## 2026-08-02 — Relationship Analysis (direct/upstream/downstream/impact) computed entirely client-side

**Decision:** The side panel's new "Relationship Analysis" block — direct
dependencies, upstream (transitive outgoing), downstream/"impact if
deleted" (transitive incoming) — is computed in JS from `GRAPH.edges`
already fetched for the current query. No new backend endpoint or field.

**Why:** Every edge in this graph is already documented (see the original
"Resource Relationship Graph" CLAUDE.md section) as consistently meaning
"source depends on target." That gives a clean, uniform direction
convention for free — upstream is just outgoing-edge BFS, downstream is
just incoming-edge BFS — with no new Azure calls or backend traversal
logic needed.

**Tradeoff:** "Impact if deleted" is explicitly scoped to the *currently
rendered* graph, not the true environment-wide impact — the UI says so
plainly rather than implying completeness. A resource this graph didn't
discover (outside the hop/node cap, or in a different subscription/RG not
queried) that depends on the selected node won't show up. This is a real,
disclosed limitation, not a bug to fix later — closing it properly would
mean either removing the hop/node caps (defeating their purpose) or a
separate "reverse-dependency sweep" feature, out of scope for this pass.
