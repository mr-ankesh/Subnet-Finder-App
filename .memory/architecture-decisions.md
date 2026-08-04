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

---

## 2026-08-02 — Fixed: empty `background-image` silently blanked the entire graph

**Bug:** After the UI polish pass shipped, the user opened `/resource-graph`
in a real browser (the first real-browser look this feature ever got — no
headless-browser tool was available in-session) and saw resource counts in
the stats bar but zero nodes drawn. A headless Cytoscape reproduction using
the exact real API response proved the graph model itself was fine (correct
node/edge counts, valid finite layout positions, no construction
exceptions) — which was the wrong place to look, because headless mode
never attempts to load/decode `background-image`, so it can't surface this
class of bug. The user then reported the actual browser console error:
`background-image:` invalid. Root cause: `TYPE_STYLE` intentionally leaves
`icon: null` for most types (NSG, DNS zone links, containers, Managed
Identity, NIC, Disk, etc. — only 9 of ~16 types have an icon per the
original spec), and the node-building code did
`icon: st.icon ? ICONS[st.icon] : ''` — an **empty string**, not `null`.
Cytoscape's canvas renderer rejects `''` as an invalid `background-image`
value, and that rejection appears to abort the render for the whole graph,
not just the affected node.

**Fix:** Never hand Cytoscape a `data()`-mapped value that can resolve to
`''`. Added a `hasIcon` boolean to node data and split the stylesheet: the
base `node` selector no longer mentions `background-image` at all; a new
`node[?hasIcon]` selector carries `background-image`/`background-fit`/etc.,
so nodes without an icon simply never match that rule instead of matching
it with an invalid value.

**Why this matters beyond the one fix:** this is the second time on this
feature that a headless/mocked verification passed cleanly while the real
browser/API surface behaved differently (see the two 2026-08-02
"real-Azure caught it" entries above, from the base feature). The pattern
holds here too, one layer up the stack: headless Cytoscape validates the
*graph model*, not the *rendering pipeline* — anything involving actual
image/asset loading needs a real (or real-headless-browser) render to
verify, not just a model-level reproduction. Recorded so a future session
doesn't trust "the headless repro passed" as proof the visual output is
correct.

---

## 2026-08-03 — AI Architecture Advisor: "Rules decide, LLM explains" enforced structurally, not just documented

**Decision:** The advisor's deterministic engine (`rules_engine.py` +
`pattern_matcher.py`) runs completely before any LLM call, and its output is
treated as data the LLM narrates, never a decision it can revise.
Concretely: `recommendation.py` builds every section of the output template
except one sentence ("Why this pattern") entirely from rule/pattern output;
the LLM is only ever handed already-decided facts to phrase, and if its
classification-stage output disagrees with the rules/matcher's own pick,
the rules/matcher's pick wins silently (the LLM's disagreement is not
surfaced as an alternative).

**Why:** This was the user's explicit non-negotiable framing for the whole
feature. The risk with LLM-driven architecture advisors generally is
silent scope creep — an LLM "helpfully" suggesting a slightly different
SKU, or omitting a mandatory DNS step it didn't think was needed. Making
the rules engine the sole source of every structured fact (and giving the
LLM only prose-filling and free-text-classification jobs, both with
deterministic fallbacks) makes this enforced by the code's own structure,
not just a policy someone has to remember to follow.

**Verification note:** this wasn't just a design intention — during
implementation, a real LLM provider call failed live (expired license,
403) and the recommendation still rendered completely and correctly via
the deterministic fallback path. The feature was proven to work with
**zero working LLM configuration**, which is the strongest evidence the
separation actually holds.

---

## 2026-08-03 — AI Architecture Advisor: new `advisor_sessions` table, not `chats.py`

**Decision:** Conversation state lives in a new `advisor_sessions` table
(`advisor/session_store.py`, own `ensure_table()`), not `chats.py`'s
existing `agent_chats` table.

**Why:** `agent_chats` is a flat, append-only list of `{role, content, ts}`
messages — the right shape for a chat transcript, wrong shape for an
advisor conversation's actual state (answers-so-far, derived values,
selected pattern, escalation flags, prefill payload). Bolting structured
state onto a message-log column would conflate two different data models
in one field. A second, genuinely different table is the correct call here
— this is the same reasoning that led to `agent_chats` itself being kept
separate from `spoke_requests` for the original agent chat feature.

**Tradeoff:** One more raw-SQL table to remember for
`scripts/sqlite_to_postgres.py` (added — `TABLES` list, one line) and to
carry through any future schema change. Documented here specifically so a
future session doesn't try to "simplify" by merging it into `agent_chats`
without re-deriving why that doesn't fit.

---

## 2026-08-03 — AI Architecture Advisor: KB-vs-real-form mismatches found and resolved during implementation

**Decision/finding:** `advisor_kb/` was written with its own semantic
vocabulary for Azure storage settings, which in four places didn't
literally match this app's actual `templates/requester.html` form markup —
found by reading the real form directly rather than assuming the KB's
field/value names were the form's:

1. `identity_type`: KB says `UserAssigned`/`SystemAssigned`; the form's
   `<select>` options are `user`/`system`. Translated in `prefill.py`.
2. `encryption_type`: KB says `CMK`/`MMK`; the form's options are
   `customer_managed`/`microsoft_managed`. Translated in `prefill.py`.
3. `storage_premium_temporary`'s `design.sku` is `Premium_ZRS`, which had
   **no matching `<option>`** on the form at all (only `Premium_LRS`
   existed) — fixed by adding `Premium_ZRS` to `config.py`'s `STORAGE_SKUS`
   and the form's dropdown. This is a genuine pre-existing gap the advisor
   surfaced (the manual form couldn't have offered this SKU either), not
   scope creep, and it only *widens* what `_validate_storage_request()`
   accepts — nothing about validation was weakened.
4. `ServiceClass`: the KB's own mapping derives Bronze/Silver/Gold/Platinum
   from criticality, but the form's actual `service_class` field only
   offers Standard/Business Critical/Mission Critical — a different
   vocabulary entirely, and the KB's own mapping file already flags this
   exact mapping as "inferred, not quoted from the design documents."
   Rather than force either vocabulary onto a field neither confirms, this
   is left blank with an explicit checklist note explaining why, so the
   user picks it themselves instead of silently getting a wrong-looking
   value that superficially seems prefilled correctly.

**Why this matters beyond the four fixes:** this is the same pattern as the
Resource Relationship Graph's "the mock's assumption was wrong" lesson,
one layer up — a KB or spec document describing intended behavior can be
correct in its own terms and still not match what the actual running
application does. Reading the real form markup (not the KB's description
of what a form *should* have) was what caught all four.

**Tradeoff:** None for 1-3 (straightforward, complete fixes). For 4, the
tradeoff is an intentionally incomplete prefill on one field — accepted
because a wrong-but-plausible-looking auto-selection would be worse than a
clearly-flagged manual step.

## 2026-08-03 — AI Architecture Advisor: expanded from storage-only to six services

**Decision:** `advisor_kb/` was extended additively (per its own
`MIGRATION.md`) with catalog patterns, questions, decision matrices and
request mappings for AKS/VM/Postgres/AppGW, plus a shared
`platform_constants.yaml`, plus a `key_vault` catalog pattern. Rather than
duplicate `catalog_loader.py`/`rules_engine.py`/etc. per service, the
existing storage-only engine was generalized to take a `service` parameter
throughout — `SERVICE_FILES` maps each service id to its own
questions/rules/mapping files, and `rules_engine.evaluate_full` now
iterates whatever `execution_order` a service's own matrix declares instead
of assuming storage's fixed seven phases (the five new matrices only
declare six — no `constants`, which is storage's own concept).

**Finding: a service's internal `service:` id is not always its real
`RequestType`.** Every KB file (rules/questions/mapping) for a given
service agrees on one `service:` field value, but storage's is
`storage_account` while its real `RequestType`/form section is
`storage_account_create` — only each mapping file's own
`target_request_type` field is the source of truth for which form to
prefill. AKS's and VM's `service:` values happen to equal their real
`RequestType` (`aks_cluster`, `vm_create`), which could easily have masked
this distinction if only tested against those two — caught because
Postgres/AppGW's `target_request_type` (`postgres_create`/`app_gateway`)
also doesn't correspond to any real `RequestType` at all yet (see below).

**Decision: Key Vault stays reference-only, not a 6th menu item.** The KB
ships a full `keyvault_premium_private` catalog pattern (so other services'
recommendations can cite it) but no question/rules/mapping files, and the
task's own menu spec listed five items, not six. Asked the user explicitly
rather than assuming either way; confirmed reference-only.

**Decision: Postgres/AppGW ship now targeting `RequestType.OTHER`**, per
the user's explicit instruction, rather than waiting on two new dedicated
request types being built first — both mapping files say so themselves
("this mapping is the specification for its field set — build the request
type before wiring this in"). `RequestType.OTHER`'s real form has exactly
two fields (`description`, `priority`) — nothing like the project/env/
owner/criticality block every other type gets — so neither service's rich
`mapped_fields` can be prefilled field-by-field; the whole recommendation
(pattern, key derived facts, the full `user_must_provide` checklist, an
explicit "dedicated type is coming" note) is composed into the description
text instead.

**Decision: cross-service mechanics render, never auto-orchestrate.**
`add_service` (a companion service, e.g. AKS flagging `app_gateway` for
public exposure) is shown as a "you'll also need" list — never
auto-continued into that service's own question flow, which is explicitly
the Phase 3 "whole environment" composer's job, out of scope here. A
`redirect` (Postgres's `self_managed` escalation sending the whole
recommendation to `vm_workload_standard`) renders only the target pattern's
summary plus a restart hint — never a fabricated VM-shaped prefill built
from Postgres-shaped answers. Both decisions follow the same "never invent
a mapping the KB doesn't sanction" discipline established for storage's
curated-VM-image and ServiceClass gaps.

**Bug found: `condition_eval.evaluate()` could silently return the wrong
answer instead of raising.** The new mapping files' `include_if`/
`required_requests[].condition` strings turned out to be genuine
plain-English prose in several places (`"egress_destinations specified"`,
`"engineers need kubectl access"` — confirmed by scanning every condition
string in the whole KB, not just the ones exercised by this session's own
tests). `_quote_bare_enums` quotes every bare word in a condition into its
own string literal; when a string has NO operator at all, that produces two
or more adjacent string literals, which Python's implicit adjacent-literal
concatenation happily evaluates to a truthy non-empty string instead of
raising a `NameError`/`SyntaxError`. This was going to be silently `True`
for every one of these strings, not "safely fail" — worse than the
originally-planned "wrap in try/except" defensive fallback, since there was
no exception to catch. Fixed by making `evaluate()` itself reject any
rewritten condition with no recognizable operator (`==`, `!=`, `in`, `is`,
`and`, `or`, `not`, comparisons), verified safe against every real
condition string in the whole KB (92 unique strings scanned; the 14 that
now correctly fail all live in `mapping`/`catalog`/`composer` contexts that
already use plain-string checks or the new `evaluate_safe()`, never the
strict blockers/escalations/derivations path). `evaluate_safe()` (catches
any exception, logs, returns `False`) is the actual fail-closed wrapper used
at the optional-item call sites this was originally scoped for.

**Tradeoff:** Postgres/AppGW's `RequestType.OTHER` prefill is a genuinely
weaker experience than storage/AKS/VM's field-by-field prefill — accepted
as the explicitly-chosen interim state per the user's instruction, with the
mapping files' `user_must_provide` blocks already positioned as the future
field spec once dedicated types exist.

## 2026-08-03 — Environment composer (Phase 3): the LLM never computes a CIDR

**Decision:** `network_planner.py` is the sole source of every subnet size,
VNET size, address count, and utilisation percentage in the environment
composer. The LLM narration pass (`render.render_summary`, the only place
an LLM may touch this feature's output) is structurally forbidden from
producing a number — it only ever rewrites the opening summary paragraph
of an already-fully-computed plan, never a table, never a figure. Every
other section (subnet table, arithmetic line, Pod CIDR paragraph, wave
table, InfoSec section) is assembled deterministically in `render.py` from
`network_planner`/`composition_engine`/`sequencer`'s structured output,
with nothing left for an LLM to decide — the same pattern already proven
for the single-service advisor's settings table, just applied to something
that's computed rather than merely selected.

**Why:** this plan goes to TechOps for real CIDR approval. An explaining
model reaches for a rounder number; TechOps rejects a figure that doesn't
match its own derivation. A wrong CIDR here is a routing incident, not a
cosmetic mismatch — qualitatively higher stakes than every prior advisor
phase, where the LLM's job was narrating a *selected* catalog pattern
rather than a *computed* arithmetic result.

**How enforced:** verified two ways. (1) `scripts/test_advisor_validation.py`
forces `prompts.call_llm` to raise (an existing suite-wide monkeypatch from
the six-service build) and confirms the plan still renders correctly from
the deterministic path — the "forced LLM failure" check. (2) A dedicated
arithmetic-integrity check compares the renderer's own output string
byte-for-byte against `network_planner.build_network_plan()`'s structured
return for the same inputs, and both `network_sizing.yaml` canonical
examples are reproduced by reading the YAML's own `canonical_examples`
block directly (not hand-copied expected numbers), so a future change to
the KB's own worked figures would immediately surface as a test failure
rather than silently drifting out of sync.

## 2026-08-03 — Environment composer: AKS subnet sizing needs two separate formulas, not one

**Decision:** `network_planner.size_aks_subnet()` computes two genuinely
different numbers and returns both under distinct names, rather than
picking one formula and deriving the other from it:
- **Size selection** (`size`/`total`/`usable`): a bucket-table lookup
  against `network_sizing.yaml`'s `snet_aks.sizing_table` by node count
  ("up to 10 nodes" → `/26`). This reproduces the table's own pre-baked
  `min_addresses` column via `floor(0.33 * bucket_ceiling)` (e.g.
  `floor(0.33*10)=3` → the table's `18`).
- **Prose headroom figure** (`actual_surge`/`actual_min_addresses`): a
  *live* computation from the real node count using different rounding —
  `surge = max(1, round(0.33 * node_count))`,
  `min_addresses = node_count + surge + 5`. For 6 nodes:
  `round(1.98) = 2`, `6+2+5 = 13`, matching `worked_example.md`'s "6 nodes
  + surge headroom ≈ 13 today" exactly. `floor` on the same input would
  give `12`, one address short of the acceptance test's own figure.

**Why:** these answer different questions. The bucket table's job is
picking a size that won't need re-provisioning as the cluster grows
(deliberately generous, keyed to a ceiling); the prose's job is explaining
*today's* headroom in terms the requester actually asked about (keyed to
the real count they gave). Collapsing them into one formula produces a
number that's right for one purpose and silently wrong — or unverifiable
against the acceptance test — for the other. This was flagged as a live
risk by a second-opinion review before implementation (the exact
observation: "two different surge computations, one for the size lookup
and one for the prose — make the planner return both as distinct named
fields so the test can assert each against its own source") and confirmed
necessary once the actual arithmetic was traced through by hand against
`worked_example.md`'s stated figures.

## 2026-08-03 — Environment composer: `snet_pe`'s `recommended_default` override is a judgment call, documented as one

**Decision:** `network_sizing.yaml`'s `snet_pe` subnet has both a raw
per-count `sizing_table` ("up to 10 endpoints" → `/28`) AND a
`recommended_default: "/27"` with the stated reason "private endpoints
accumulate — every new PaaS service adds one." `network_planner.size_pe_subnet()`
always prefers `recommended_default` when present, falling back to the
bucket table only if a future KB revision removes the override. The
canonical worked example (4 endpoints → `/27`, not the naive per-count
`/28`) confirms this reading is the intended one.

**Why:** resolving an ambiguous KB structure silently (picking whichever
number "worked" for the test) would hide a real interpretive choice from
whoever maintains this KB next. Documenting it as a deliberate override —
with the reasoning inline in the code comment — means a future KB author
who changes `recommended_default` sees exactly what depends on it, instead
of rediscovering the override mechanism from scratch.

## 2026-08-03 — Environment composer: intake is its own flow controller, not a 6th `SERVICE_FILES` entry

**Decision:** `advisor/composer/intake.py` does NOT register `"environment"`
as a service in `catalog_loader.SERVICE_FILES`, and does not route through
`question_engine.py`'s per-service question walk, despite reusing its
`_normalize_options`/`_normalize_skip_if` helpers as plain functions.

**Why:** `SERVICE_FILES` is a load-time contract for services that
ultimately select a `RequestType` via catalog pattern matching — the
environment composer does neither (there is no single pattern; the
environment IS the composition). Forcing it in would mean
`get_mapping("environment")` needing a fake mapping file,
`_select_pattern` needing a special-case to never actually select
anything, and `services.is_valid()` needing a carve-out — every future
reader of that machinery would have to hold "except this one isn't really
a service" in their head. The environment composer also needs three
mechanics `question_engine.py` genuinely doesn't have: `type: text_parsed`
inventory parsing with mandatory confirm-back, and `ask:` follow-up
questions injected dynamically from `composition_engine.infer_missing_components`
rather than existing in any static question bank. A dedicated, small flow
controller was cheaper and clearer than bending the existing one to fit a
shape it wasn't designed for — flagged as the right call by a second
opinion during planning before any code was written.

## 2026-08-03 — Environment composer: session storage needed no new table

**Decision:** `session_store.create_session()` gained an optional `mode`
parameter (`"single_service"` default, `"environment"` for the composer),
stored *inside* the existing JSON `state` blob rather than as a new
column or a new table.

**Why:** `advisor_sessions.state` was already schema-free (a single JSON
column: answers, derived values, selected pattern, whatever a given
conversation needs) — nothing in the table definition assumed a
single-service shape. The environment composer's state (parsed inventory,
`resolved_asks`, `pending_confirm`) fits the same column without a
migration, and `scripts/sqlite_to_postgres.py` needs no new entry, unlike
every genuinely new raw-SQL table added in prior phases (see the
`subscription_inventory`/`budget_alert_state`/`agent_chats` precedent in
`CLAUDE.md`'s "Two DB backends" section).

## 2026-08-04 — Persistent chat: the state machine owns the pending-question pointer, the LLM never advances it

**Decision:** Every state transition in the advisor's new persistent
conversation (`advisor/orchestrator.py`) goes through `question_engine.py`
(service mode) or `advisor/composer/intake.py` (environment mode) — the
same pure-function engines the original single-shot flow already used,
completely unchanged. The LLM is consulted in exactly two places
(`classify_turn()`, `advisor.freeform.answer()`), and neither is ever
allowed to record an answer the underlying engine didn't validate, move
the pending-question pointer, skip a question, or invent a number.
`classify_turn()`'s job is narrowly "is this turn guided, freeform, or
both" — nothing more; the actual recording/advancing always happens via
`record_answer()`/`next_question()`.

**Why:** free-form chat is exactly where the advisor's core contract
("Rules decide. LLM explains.") is easiest to accidentally violate — a
general-knowledge model asked to help mid-conversation will happily
narrate something that contradicts a rule outcome, or "helpfully" infer
that the next question can be skipped. Keeping the state machine as the
sole owner of the pointer means a classification mistake can, at worst,
cause one extra clarifying turn — it can never corrupt the underlying
answers or desynchronize the conversation from what was actually recorded.

**Classification-failure fallback, and why it's tested as TWO separate
failure modes, not one.** Any classifier exception, timeout, or malformed
JSON response falls back to treating the turn as a guided answer for the
pending question. This is deliberately verified as a DISTINCT test from
"the whole LLM provider is dead": killing `prompts.call_llm`
unconditionally (item 13) exercises the same fallback for a different
reason (nothing works at all) than breaking ONLY the classification call
while narration would still succeed if reached (item 14) — collapsing
these into one test would have missed a real bug found during this build
(see below), since the two failure paths went through different code.

**Bug found: `classify_turn` crashed on every synthetic/dynamic question**
(the inventory confirm-back turn, a correction confirmation, an
environment composer `ask:` follow-up) via an unguarded
`pending_question.get(...)` call when `pending_question` was `None` — it
only "worked" because the broad `except Exception` caught the
`AttributeError` and fell back to guided, which happened to be the
correct behavior anyway, but for the wrong reason and with a misleading
log message masking what actually failed. Fixed by making
`pending_question is None` an explicit, intentional guided-always branch,
same treatment as `type: text_parsed` — both are cases where "did the user
mean something else instead" isn't a meaningful classification to make.

**Bug found: `conversations.save_state()`'s optional fields defaulted to
`None` instead of "leave unchanged".** Every call updated
`pending_question_id`/`selected_pattern`/`recommendation_json`/
`prefill_payload_json` unconditionally — a call that only meant to persist
`answers_json` (e.g. flagging a pending correction) silently wiped an
already-stored recommendation and the current pending question, since
omitting a kwarg is indistinguishable from passing `None` in Python.
Caught live: confirming a correction on an already-completed conversation
first appeared to lose its recommendation before the user had even
answered the confirmation. Fixed with a private `_UNSET` sentinel default
and a read-merge inside `save_state()` — each call now only touches the
fields it explicitly passes, verified by re-running the correction flow
and confirming the recommendation survives right up until an explicit
"yes" actually invalidates it.

**Bug found (not the orchestrator's — a genuine security-relevant one):
`_advisor_owner_key()`'s local-dev fallback copied `_chat_owner("admin")`'s
single shared `"admin"` identity instead of `_chat_owner("requester")`'s
per-session `chat_uid`.** The advisor is requester-facing
(`@require_login`, not `@require_admin`), so every unauthenticated local
session collapsed onto the same owner key and could open each other's
conversations by ID — caught live while smoke-testing item 3's
cross-owner-denial check (a second `requests.Session()` was getting a 200
with the first session's transcript, not a 404). Fixed to mirror the
requester fallback; reverified with two separate sessions.

## 2026-08-04 — Advisor KB management: DB overrides live in the DB, not on disk

**Decision:** KB overrides live in the DB, not on disk, because the KB is
baked into the container image and prod runs 3 replicas. `advisor_kb_versions`/
`advisor_kb_files` store every activated KB version's full file content in
the database (`advisor/kb_store.py`), mirroring `settings_store.py`'s
existing DB-override/env/default resolution chain but applied to whole
files instead of scalar values.

**Why:** `advisor_kb/` ships baked into the container image, and prod
deploys that image to 3 replicas via a rolling Helm upgrade — there is no
shared writable filesystem between pods, and even a single pod's local
disk write would vanish on the next restart/rollout. A DB-backed override
is the only storage that is simultaneously durable across restarts and
consistent across all 3 replicas without needing a shared volume. This is
the decision most likely to be questioned later, since "just write the
uploaded KB to `advisor_kb/`" looks simpler in a single-replica mental
model — it silently breaks the moment there's more than one pod.

## 2026-08-04 — Advisor KB management: true per-conversation pinning via contextvars, not just an honest label

**Decision:** `catalog_loader.pinned_to(version_id)` (a `contextvars`-based
context manager) makes every KB read within its scope resolve against one
specific `advisor_kb_versions` row's stored content, regardless of what
becomes active afterward. `advisor/orchestrator.py`'s `start_conversation`/
`process_turn` wrap their entire body in it, using the pinned
`advisor_conversations.kb_version_id` (new column) recorded at conversation
creation.

**Why:** the persistent-chat build (2026-08-04, earlier the same day)
introduced `kb_version` as "a single hand-bumped constant... an honest
scope limit, not a hidden gap" specifically because `catalog_loader`'s
`lru_cache` never invalidated and there was no versioned KB storage to
pin against — so "finishing against the KB you started on" was true only
by accident (nothing ever changed at runtime). Once the KB became
DB-mutable and its caches started invalidating for real, that accident
stopped holding: without an explicit pin, a conversation's next read after
a mid-conversation activation would silently pick up the new content.
`contextvars` was chosen over threading through an explicit `kb_version_id`
parameter to every engine function (`question_engine`/`rules_engine`/
`intake`/`composition_engine`/`network_planner`) because those modules are
pure functions of a state dict already, called from many places across
three advisor phases built over the preceding days — re-plumbing all of
them for one cross-cutting concern would have touched far more surface
than the pin itself needed, and risked reintroducing exactly the kind of
regression the "zero signature changes needed" property was designed to
avoid. Verified directly, not by construction: activate version A, start a
conversation (pins to A), activate version B, confirm the conversation's
next read under `pinned_to(A)` still returns A's byte-for-byte content
even though the live active version is now B — on both SQLite and a real
local Postgres instance.

## 2026-08-04 — Advisor KB management: `selectable` added as a required schema key, backfilled mechanically

**Decision:** every catalog pattern now requires a `selectable: true|false`
key (added to `catalog_loader.REQUIRED_PATTERN_KEYS`), backfilled into all
12 shipped patterns as part of the same build that started requiring it.

**Why:** the validation spec's item (g)/(h) ("selectable: true requires a
matching question bank; selectable: false must NOT have one") needed
something to check bidirectionally, but no such field existed anywhere in
the KB before this — selectability was only ever implied by
`catalog_loader.SERVICE_FILES`'s 5 keys and `advisor/services.py`'s menu.
Rather than inferring selectability at validation time from that
code-side mapping (which would leave the KB itself silent about a fact
that materially changes how a pattern is used), the KB was made
self-describing: `true` for every pattern whose `service` is one of
`SERVICE_FILES`'s 5 keys, `false` for `keyvault_premium_private` (the one
reference-only pattern) — mechanically derived from already-real behavior,
not a new editorial decision. Every existing catalog pattern needed the
backfill in the same commit as the schema change, or the validator would
have rejected the KB shipped in the image the moment it ran.

## 2026-08-04 — Advisor KB management: four real bugs, three caught only because testing went past mocks/offline checks

**Bug found: a genuine self-deadlock on Postgres, invisible on SQLite.**
`kb_store`'s version-label generator (`_next_version_label`) originally
opened its own new connection and called `ensure_tables()` (which runs
`CREATE INDEX IF NOT EXISTS`) while `activate()`'s OUTER transaction — on
a different, still-open connection — held an uncommitted row-exclusive
lock on `advisor_kb_versions` from its own `UPDATE ... SET status =
'superseded'`. On Postgres, `CREATE INDEX` needs a `SHARE` lock that
conflicts with `ROW EXCLUSIVE`, so the second connection blocked forever
waiting on the first connection's lock — which itself was never going to
release, because the first connection's own Python code was blocked
waiting for the second connection's query to return. Postgres's own
deadlock detector never caught it, since from the server's point of view
neither connection was waiting on the other in a way it tracks — it's a
client-side self-inflicted stall, not a lock-graph cycle. SQLite's
WAL-mode MVCC let the second connection's read through anyway, masking the
bug completely in every offline test. Caught only because this session
spun up a real local Postgres instance and drove `activate()` through it
directly, per this session's established discipline of not trusting
SQLite-only testing for anything DB-shaped. Fixed by threading the
caller's already-open connection through instead of opening a new one.

**Bug found: the AZURE-source drift check's SKU-family match used the
wrong string format.** `vm_workload_standard.yaml`'s design prose names
"D-series general purpose, E-series memory-heavy, F-series compute-heavy"
— the check compared this against `list_vm_skus()`'s returned `family`
field using the underscored `"Standard_D"` display convention (the same
spelling Azure shows in `Standard_D2s_v3`-style SKU names). Azure's actual
`resourceSkus` API returns family as PascalCase with no underscore and a
`Family` suffix (`"StandardDadsv7Family"`), confirmed by printing the real
values returned against the sandbox subscription
(`845e564b-31a3-44b0-b030-226798b31574`) — every family check
false-mismatched (reported "NOT available" for D/E/F-series that were
genuinely available) until this was caught and fixed. This is exactly the
class of bug the Resource Relationship Graph build hit twice already this
project (casing/format mismatches between what an API actually returns
and what a developer assumes) — another data point that this repo's
Azure-adjacent code needs live-API verification, not just mocked shapes,
before it can be trusted.

**Bug found: `glossary.yaml` had 7 dangling `related:` references that had
shipped undetected.** Running the new stage (f) validator against the
real, already-in-production KB for the first time surfaced
`storage_account`, `DNS`, `subscription`, `audit_trail`, and
`firewall_policy` as `related:` targets with no matching glossary term or
alias anywhere in the file. These are pre-existing content, not something
introduced by this build — nobody had ever mechanically checked this
before. Stage (f)'s matcher was also itself corrected mid-build: it
initially used raw exact-string `term:` matching, which is STRICTER than
the actual runtime resolution mechanism (`advisor/glossary.py`'s
`find_term()`, which normalizes case/punctuation and matches against
aliases too) — using the stricter check would have produced false
positives on legitimately-resolvable phrasing variants. Fixed the matcher
to mirror `find_term()`'s real normalization, then fixed the 7 genuine
dangling references by removing each one (not inventing new glossary
entries for Storage Account/DNS/Subscription/Audit Trail/Firewall Policy —
those are real concepts that deserve real authorship, not a guess written
to make a validator pass).

**Bug found (methodology, not product code): SQLite WAL-mode defeats a
plain `cp` restore.** While cleaning up test state between stages, this
session repeatedly did `cp data/requests.db.backup ... data/requests.db`
to restore the local dev DB to its pre-test state while the Flask dev
server was still running with an open connection. This did NOT reliably
take — SQLite's WAL mode keeps recent writes in a separate `-wal` sidecar
file, and a file-level copy of just the main `.db` file while a `-wal`
file with uncommitted-to-main data still exists (and/or a live connection
still has it open) can leave the "restored" file showing stale test data
on next read. Two rounds of HTTP-level testing (Stages 5 and 6) left
visible rows behind despite an apparently-successful restore each time —
only caught because Stage 7's real-browser screenshot showed test
timestamps/notes that should not have been there. Fixed by stopping the
server first and deleting the `-wal`/`-shm` sidecar files before copying
the backup over, then verifying via a fresh server start that the version
table was genuinely empty and `request_count` matched the pre-session
baseline. Worth remembering for any future session that touches this
repo's local dev DB for testing: **stop the server, and delete `-wal`/
`-shm`, before restoring — a running server's open connection can silently
undo a file-level restore.**
