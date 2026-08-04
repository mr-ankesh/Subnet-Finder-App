# Current State

> Last updated: 2026-08-04. Update this file at the end of every session
> (see maintenance rules in `CLAUDE.md` → "Session Memory Protocol").

## Development Status

Active development. Storage Account feature committed (`8ef4ef2`,
2026-08-01). Resource Relationship Graph committed (`66824be` base feature,
`5b12f9f` UI polish pass, `9fff6b1` background-image bugfix, all
2026-08-02). AI Architecture Advisor V1 (Storage only) committed
2026-08-03 (`4ccd9d8`/`4ca0053`/`90ddc17`/`72d27c9`, plus KB duplicate-dir
cleanup `f06285c`). Advisor expanded from storage-only to six services
(Storage/AKS/VM/Postgres/AppGW selectable, Key Vault reference-only) —
committed 2026-08-03 (`1aca22a`/`cc1e58c`/`1c70a91`). Azure CNI Overlay
correction applied across both the single-service and composer KBs
(`ce7874b`/`d27d4cb`/`7bf8459`). Environment composer (Phase 3 — whole-
environment design, not pattern selection) committed 2026-08-03
(`2298611`/`6fde2de`/`9da0fe1`/`5144094`/`7e67cae`). Advisor converted to a
persistent, conversational chat with history (resume, free-form Q&A
mid-intake, correcting an earlier answer) — committed 2026-08-04
(`7250095`/`abbc024`/`e930961`/`b6adea0`/`27adfd7`/`93f97e7`). Advisor
Knowledge Base management (view/download/validate/upload/version/revert,
per-pattern `last_verified` + staleness note, two-source drift check) —
committed 2026-08-04 (`c0b5fc1`/`1103f97`/`22390f6`/`84c5e1d`/`c9b5452`/
`cb7340b`/`ca5a738`/`29940b4`) — see "Features In Progress" below for the
full breakdown. Remaining uncommitted
changes are
the unrelated cosmetic/infra items noted below (predate all advisor work).
`main` is the only branch; commits go straight to it.

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

### AI Architecture Advisor — storage-only V1, then expanded to six services

Guided-intake chat at `/advisor` that turns plain-English answers into a
Presight-approved request (prefilled, never auto-submitted). Entirely
knowledge-base-driven (`advisor_kb/`, checked in, provenance-tracked to
Microsoft/Kyndryl design documents) — see `CLAUDE.md` → "AI Architecture
Advisor" (rules decide/LLM explains separation, condition-language
evaluator, session storage, prefill handoff, Mermaid vendoring) and its
"Six-service expansion" subsection for what changed in the second round.
Makes **zero** Azure SDK calls of any kind (not even read-only).

**V1 (Storage only) — committed `4ccd9d8`/`4ca0053`/`90ddc17`/`72d27c9`,
KB dir cleanup `f06285c`:**
- New package `advisor/` (11 files: `catalog_loader`, `condition_eval`,
  `question_engine`, `rules_engine`, `pattern_matcher`, `prefill`,
  `diagram_builder`, `session_store`, `prompts`, `recommendation`,
  `__init__`), `templates/advisor.html`, `scripts/test_advisor_validation.py`
  (63 checks), `static/vendor/mermaid.min.js` (vendored UMD build).
- 4 routes in `app.py` (`/advisor`, `/api/advisor/chat`,
  `/api/advisor/diagram`, `/api/advisor/prefill`) + `requester_page()`
  extended for the `?advisor_session=<id>` prefill handoff.
- Verified: the full 63-check suite plus a real Flask-test-client run of
  every route (login → full conversation → recommendation → diagram →
  prefill → requester page shows the embedded payload).
- Real bugs found and fixed during this build (see
  `architecture-decisions.md` 2026-08-03 entries): 3 in `condition_eval.py`'s
  string rewriter; a dead-code gap in `prefill.py` that never actually
  flagged `service_class` as unfillable; a client-side "Change answer" bug;
  4 KB-vocabulary-vs-real-form-field mismatches (`identity_type`,
  `encryption_type`, `Premium_ZRS` missing from the SKU list, `ServiceClass`
  vocabulary mismatch).

**Six-service expansion (Storage/AKS/VM/Postgres/AppGW selectable, Key
Vault reference-only) — committed `1aca22a`/`cc1e58c`/`1c70a91`:**
- `advisor_kb/` extended additively (`MIGRATION.md`): catalog patterns,
  questions, decision matrices, request mappings for 4 more services, plus
  a shared `platform_constants.yaml`. Storage's own files untouched.
- `catalog_loader.SERVICE_FILES` map, `design.inherits` merge,
  `platform_constants`/composer-file loaders; `rules_engine.evaluate_full`
  now runs whatever `execution_order` a service's matrix declares (5 new
  matrices have 6 phases, no `constants`); `add_service` accumulation,
  `redirect`, verbatim `message_ref` rendering (the InfoSec gate) all new;
  `question_engine` normalizes two option/skip_if shapes the new KB
  introduced + adds the service-selection question as the literal first
  step, with a deterministic keyword fallback for free text.
- `condition_eval.evaluate()` now rejects conditions with no recognizable
  operator — several new mapping-file strings turned out to be plain-English
  prose that was silently evaluating to `True` via Python's implicit
  adjacent-string-literal concatenation, not raising as intended.
  `evaluate_safe()` added for the optional-item (`include_if`) call sites.
- `prefill.build_prefill_aks`/`build_prefill_vm`/`build_prefill_postgres`/
  `build_prefill_appgw` + `recommendation.build_recommendation_generic()` +
  `build_redirect_response()`. Postgres/AppGW have no dedicated `RequestType`
  yet, so both target `RequestType.OTHER` (its form has only
  `description`+`priority`) — the recommendation is composed into
  `description` text instead of field-by-field prefill.
- `app.py`'s `/api/advisor/chat` fully service-aware; `templates/advisor.html`
  renders the new `redirect` response type, verbatim `message_ref` content,
  `add_services`, and a generic recursive `design` dict renderer;
  `templates/requester.html`'s prefill JS reads `request_type` from the
  payload instead of hardcoding storage.
- `scripts/test_advisor_validation.py` grew from 63 to 172 checks (all 63
  original storage checks pass unmodified — only call-signature updates for
  the new required `service` argument).
- **Verified live against the running app** (not just the assert suite): a
  full AKS conversation through diagram render and prefill handoff; a
  Postgres `self_managed` conversation confirming the redirect to
  `vm_workload_standard` fires; an AppGW public-exposure conversation
  confirming the InfoSec gate escalation renders its `message_ref` content
  verbatim with `request_type: "other"`.
- Real KB-vs-real-form mismatches found (same discipline as V1, re-verified
  against actual markup): AKS/VM's form field is `project`, not
  `application_name`; VM's `auth_mode` only offers `ssh_key`/`password`
  (not the KB's derived `admin_password_at_deploy`); AKS's `node_pool_name`/
  `zpa_rnd_access` are required but missing from the KB's own
  `user_must_provide`; `gpu_node_pool`/VM's curated-image transform have no
  backing form field or data source — never guessed, only checklist notes.

### Environment composer — Phase 3, fully committed (`2298611`/`6fde2de`/`9da0fe1`/`5144094`/`7e67cae`)

Third advisor mode, reachable from `/advisor`'s mode picker: describe a
whole environment ("10 VMs, 1 AKS cluster, 1 managed PostgreSQL, public")
and get one COMPUTED architecture (network arithmetic, inferred components,
an InfoSec gate, an ordered build sequence) instead of a selected catalog
pattern. See `CLAUDE.md` → "Environment composer" for the full design.
`composer/worked_example.md` is the literal acceptance test (positive +
negative case, both verified to pass exactly).

- New package `advisor/composer/` (7 files: `inventory_parser`,
  `network_planner`, `composition_engine`, `sequencer`, `infosec`, `intake`,
  `render`, `env_prefill` — 8 actually, listed individually below), reusing
  `advisor_kb/composer/`'s already-existing KB content (`environment_questions
  .yaml`, `network_sizing.yaml`, `composition_rules.yaml`, `infosec_gate.yaml`,
  `request_sequence.yaml`, `worked_example.md`, `environment_recommendation_
  template.md`, `diagrams/environment_full.mmd`).
- `network_planner.py` (highest-risk module — TechOps approves the CIDR off
  its output): reproduces both `network_sizing.yaml` canonical examples
  exactly, including the strictly-greater-than-75% utilisation flag (positive
  example lands on exactly 75.0%, must not trip) and two deliberately separate
  AKS sizing formulas (bucket lookup for the actual size vs. a live
  actual-node-count formula for the "N nodes + surge headroom ≈ M today"
  prose — different rounding, different inputs, never merged). Pod CIDR is
  always a separate non-subnet field.
- `composition_engine.py` runs `composition_rules.yaml`'s 8-phase pipeline
  (no `pattern_selection` phase — there's no single pattern here);
  `sequencer.py` turns `request_sequence.yaml` into ordered build waves with
  real service labels (never bare "Other", even though `postgres_create`/
  `app_gateway`/`private_endpoint` aren't real `RequestType`s);
  `infosec.py` reuses the AppGW gate's verbatim-`message_ref` rendering
  discipline.
- `intake.py` is its own flow controller (NOT a 6th `SERVICE_FILES` entry) —
  handles `type: text_parsed` inventory parsing + mandatory confirm-back,
  and dynamically-injected `ask:` follow-ups question_engine.py has no
  concept of.
- `session_store.create_session(mode="environment")` — no schema change,
  the `state` column was already a schema-free JSON blob.
- 4 new routes, all `{ok, data, error}`: `/api/advisor/environment/chat`
  (Q&A only), `/plan` (idempotent, recomputed fresh every call — no module-
  state cache, 3 replicas in prod), `/diagram`
  (`diagram_builder.render_environment()`), `/requests` (deliberately does
  NOT re-run each embedded service's own full pattern-selection pipeline —
  environment intake never asks the fields those need; uniform tag+known-
  field prefill instead).
- `templates/advisor.html` mode picker + environment-mode plan renderer
  (subnet table, arithmetic block, Pod CIDR paragraph, hub integration,
  private connectivity table, InfoSec box, build-sequence wave table,
  security posture, before-you-start checklist).
- `scripts/test_advisor_validation.py` grew from 175 to 232 checks (all
  27 spec items: positive/negative canonical cases, arithmetic integrity
  compared byte-for-byte against the planner's own structured output, both
  network_sizing.yaml canonical_examples reproduced by reading the YAML
  directly, a forced-LLM-failure fallback check, regression greps for
  classic-CNI phrasing and the retired `aks_cni_sizing` warning id).
- **Verified live against the running dev server** (not just the assert
  suite): full HTTP round-trip through all 4 routes for both the positive
  (public, 10 VMs/1 AKS/1 Postgres) and negative (internal-only) canonical
  cases, reproducing `worked_example.md`'s arithmetic/components/waves/
  InfoSec section exactly.
- Two real bugs found and fixed during this build: (1) the subscription/
  sovereignty blocker was only checked once intake finished, not after
  every answer like the single-service flow — moved to fire immediately;
  (2) `environment_full.mmd`'s internal-only edge-stripping regex had a
  newline-boundary-consumption bug where three consecutive substitutions
  sharing consumable newlines meant only the first/last edge in a block
  got removed, leaving an orphan `AGW --> AKS` reference — fixed by
  switching to line-anchored `MULTILINE` matching.
- **No headless browser available** in this environment (checked:
  `npm ls puppeteer playwright` empty, no `chromium`/`google-chrome`
  binary) — the UI was verified via HTTP round-trip + a `node --check`
  syntax pass on the extracted inline JS, NOT a true visual click-through.
  Manual verification still needed: load `/advisor`, pick "a whole
  environment", walk the intake chat, confirm the plan section renders
  correctly (including the InfoSec box and the diagram's Cloudflare
  subgraph), repeat for an internal-only environment to confirm those
  sections are absent.

### Persistent conversational chat — fully committed (`7250095`/`abbc024`/`e930961`/`b6adea0`/`27adfd7`/`93f97e7`)

Third build on the advisor this cycle: converts it from a single-shot
guided flow into a real, resumable conversation with history — for BOTH
the single-service and environment-composer modes above. Additive, not a
rewrite: `advisor/session_store.py` and every route from the two phases
above are untouched; `ADVISOR_CHAT_HISTORY_ENABLED` (Settings → Advisor)
switches which UI `/advisor` renders. See `CLAUDE.md` → "Persistent,
conversational chat" for the full design.

- New `advisor/conversations.py` (3 raw-SQL tables: `advisor_conversations`/
  `advisor_messages`/`advisor_state`, cascade delete, optimistic
  concurrency via a `version` column), `advisor/orchestrator.py` (the
  state machine — reuses `question_engine.py`/`advisor/composer/intake.py`
  entirely unchanged; the LLM only classifies a turn and narrates
  free-form answers, never advances the pending-question pointer),
  `advisor/glossary.py` (loads the new `advisor_kb/glossary.yaml`, 51
  terms/83 aliases), `advisor/freeform.py` (grounded Q&A: glossary → 
  catalog → platform_constants, labelled presight_standard/general_azure/
  outside_scope, never invents a Presight practice, never recomputes a
  CIDR).
- 5 new/changed routes (`/advisor` branches on the setting, `/advisor/c/<id>`,
  `POST /api/advisor/conversations`, `DELETE .../<id>`,
  `POST .../<id>/messages`); `config.py` gained an Advisor settings
  category (4 entries); `session["sso_sub"]` now captured in the OIDC
  callback for stable per-user ownership.
- `templates/advisor.html` gained a sidebar (conversations grouped Today/
  This week/Older, mode badge) + transcript UI with guided/freeform turns
  visually distinguished, entirely inside an `ADVISOR_CHAT_HISTORY_ENABLED`
  branch — the old single-shot markup/JS is untouched in its own branch.
- `scripts/test_advisor_conversations.py` (new, 38 checks covering all 19
  spec verification items) + `test_advisor_validation.py` unaffected (232
  checks still passing).
- **Verified for real, not reasoned about**: schema creation + CRUD +
  optimistic concurrency + cascade delete ran against BOTH SQLite and a
  real local Postgres 18 instance (Homebrew, no Docker daemon available in
  this environment) with identical results. Full conversation lifecycles
  (create → turn → resume) verified live via HTTP against the running dev
  server for both modes, including the environment-mode resume carrying
  the complete canonical positive plan (arithmetic, Pod CIDR paragraph,
  InfoSec section, all 7 waves).
- **Three real bugs found and fixed during this build** (see
  `architecture-decisions.md` 2026-08-04 entries): `classify_turn` crashed
  on any synthetic/dynamic question via an unguarded `.get()` on `None`,
  only "working" because the broad exception handler caught it and fell
  back to guided by accident; `save_state()`'s optional fields defaulted to
  `None` instead of "leave unchanged," so any call that only meant to
  touch `answers_json` silently wiped an already-stored recommendation;
  `_advisor_owner_key()`'s local-dev fallback initially copied
  `_chat_owner("admin")`'s single shared identity instead of
  `_chat_owner("requester")`'s per-session one — a real cross-owner
  conversation leak caught live (the advisor is requester-facing, not
  admin-only) and fixed before this reached the UI-verification pass.
- **Browser verification**: genuinely attempted, not just re-checked for
  availability — `pip install playwright` succeeded, but `playwright
  install chromium` never completed after 35+ minutes (retried its own
  download at least once) in this sandboxed environment's network and was
  killed rather than left running indefinitely. No Docker daemon and no
  pre-installed browser either. UI verification that did complete instead:
  full HTTP round-trip for both modes including the environment-mode
  resume bootstrap, a `node --check` syntax pass on the extracted inline
  JS, and a hand-verified Jinja if/else/endif balance check — NOT an
  actual rendered-page/click-through pass. See `next-actions.md` for the
  specific manual checklist.

### Advisor Knowledge Base management — fully committed (`c0b5fc1`/`1103f97`/`22390f6`/`84c5e1d`/`c9b5452`/`cb7340b`/`ca5a738`/`29940b4`)

Fourth build on the advisor this cycle: lets a super-admin view, download,
validate, upload, version and revert the entire `advisor_kb/` knowledge
base from Settings, plus per-pattern `last_verified` staleness tracking and
a two-source drift check (KB vs. `config.py`, KB vs. live Azure). See
`CLAUDE.md` → "Knowledge Base management" for the full design.

- New `advisor/kb_store.py` (DB-override storage — `advisor_kb_versions`/
  `advisor_kb_files`, mirrors `settings_store.py`'s pattern applied to
  whole files, since `advisor_kb/` is baked into the image and prod runs 3
  replicas), `advisor/kb_validate.py` (9-stage atomic validation gate),
  `advisor/kb_diff.py` (semantic effects diff), `advisor/kb_drift.py`
  (LOCAL + AZURE drift check), `templates/advisor_kb.html`.
  `catalog_loader.py` gained real generation-gated cache invalidation and
  `contextvars`-based per-conversation pinning (`pinned_to()`) — a genuine
  mechanical guarantee now, not just an honest label, that an in-flight
  conversation finishes against the KB version it started on even across
  a later activation.
- 7 new `@require_superadmin` routes in `app.py`
  (`/admin/advisor-kb` + 6 `/api/admin/advisor-kb/*`), 4 new `config.py`
  settings, a "Manage Knowledge Base" link on the Settings → Advisor tab,
  a quiet `staleness_note` rendered in both the live and resumed
  recommendation views.
- `scripts/test_advisor_kb.py` (new, 36 checks covering all 21 spec
  verification items); `test_advisor_validation.py`/
  `test_advisor_conversations.py` unaffected (232 + 38 checks still pass).
- **Verified for real, extensively**: schema creation + cache invalidation
  + true cross-activation pinning ran against both SQLite and a real local
  Postgres instance; a 9-stage-validation self-check against the real
  shipped KB (clean pass) plus 8 deliberately-broken fixtures (one per
  rejection stage); a full activate → activate → revert cycle verified
  against both the change ledger and audit trail; the entire upload →
  validate → diff → activate → version-history → revert flow driven
  through a real running dev server via HTTP AND through a real headless
  Chromium browser (screenshots inspected directly); the drift check run
  against the real sandbox subscription (`845e564b-31a3-44b0-b030-226798b31574`)
  already configured in this repo's local dev DB from an earlier session.
- **Four real bugs found and fixed during this build** (see
  `architecture-decisions.md` 2026-08-04 entries): (1) `kb_store`'s
  version-label generator opened a second Postgres connection while
  `activate()`'s outer transaction was still open, self-deadlocking on
  `CREATE INDEX` — only surfaced against real Postgres, masked on SQLite's
  WAL-mode MVCC; (2) the drift check's VM SKU-family match compared against
  the underscored display convention (`"Standard_D"`) instead of Azure's
  actual PascalCase `resourceSkus` family field (`"StandardDadsv7Family"`)
  — every family check false-mismatched until caught against the real
  sandbox subscription; (3) `glossary.yaml` had 7 genuinely dangling
  `related:` references (`storage_account`, `DNS`, `subscription`,
  `audit_trail`, `firewall_policy`) that had shipped undetected until the
  new validator's stage (f) ran against it for the first time — fixed by
  removing each dangling reference; (4) a SQLite WAL-mode gotcha — a plain
  `cp` restore of `data/requests.db` while the dev server held an open
  connection did not reliably take, leaving two rounds of test data visible
  until caught via a real browser screenshot and fixed by stopping the
  server and removing the `-wal`/`-shm` sidecar files before restoring.
- **Browser verification succeeded this time** — `playwright install
  chromium` completed cleanly (the two prior attempts across the
  environment-composer and persistent-chat builds both failed after 35+
  minutes; documented as resolved in `next-actions.md`).

### Resource Relationship Graph — fully committed (`66824be`, `5b12f9f`, `9fff6b1`)

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

**2026-08-03, VM_CREATE re-verification caught a DB-vs-env `AZURE_DRY_RUN`
mismatch.** Before this session's advisor work, `VM_CREATE` was
re-verified end-to-end in local dev (SQLite, local auth) at the user's
request. `.env` had `AZURE_DRY_RUN=true`, but Settings had a **DB-level
override of `false`** (config.py's resolution order is DB → env → default)
— a `vm_deploy` action ran for real against the sandbox subscription before
this was caught, creating a real throwaway VM+NIC in `rg-claude-e2e-qa-test`.
An attempted UI revert also silently no-op'd (it ran *after* `AZURE_DRY_RUN`
had since been flipped back to `true`, so `delete_vm` itself simulated
under dry-run even though the change-ledger entry got marked `reverted`).
Cleaned up via a direct `azure_tools.delete_vm()` call with dry-run
genuinely off, with an audit-trail note added explaining the ledger's
"reverted" status didn't reflect reality until that manual cleanup. Lesson
for future sessions: **always check the DB `app_settings` override, not
just `.env`, before assuming `AZURE_DRY_RUN`'s live value** — the two can
disagree, and only the DB value is authoritative once one exists.

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
