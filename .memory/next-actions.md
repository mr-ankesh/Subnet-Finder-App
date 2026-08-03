# Next Actions

> Prioritized, actionable only. Completed items are removed (not archived —
> history lives in `daily/`/`weekly`/`monthly` and `architecture-decisions.md`).
> Last updated: 2026-08-03.

## P0 — Ship-blocking

None currently.

## P1 — Follow-up

1. Visually confirm the Advisor's chat UI in an actual browser for all five
   selectable services — no headless-browser tool was reached for in this
   session (same standing gap as the Resource Relationship Graph);
   verification was via `scripts/test_advisor_validation.py` (172 checks,
   up from 63) plus real HTTP-level end-to-end runs against the live Flask
   dev server (a full AKS conversation through diagram+prefill handoff, a
   Postgres self_managed redirect, an AppGW public-exposure InfoSec-gate
   render) — not an actual rendered page in a browser. Specifically worth a
   look: the service-selection chip menu as the first step, the "you'll
   also need" (add_services) section, the generic recursive `design` dict
   renderer for AKS/VM/Postgres/AppGW, and the verbatim InfoSec-gate box.
2. Provision a real LLM provider with a valid `AGENT_PROVIDER` key/license
   for the advisor to actually use — every response so far has fallen back
   to the deterministic path (correct behavior, but the "Why this pattern"
   prose and free-text classification — including the new service-selection
   free-text routing's keyword fallback — haven't been exercised against a
   genuinely working LLM).
3. Consider whether `ServiceClass` deserves a real fix rather than "leave it
   blank" — either get the platform team to confirm a Bronze/Silver/Gold/
   Platinum-to-form-options mapping, or update the KB's own mapping to
   match the form's actual Standard/Business Critical/Mission Critical
   values. Applies identically to all five services now (`ServiceClass:
   skip: true` in every one of the four new mapping files, same stated
   reason). See `architecture-decisions.md` 2026-08-03 entry.
4. Build dedicated `RequestType.POSTGRES_CREATE`/`RequestType.APP_GATEWAY`
   types (form fields, validation, deploy actions) — the two mapping files'
   `user_must_provide` blocks are already the field spec, by their own
   stated intent. Once built, `prefill.py`'s `build_prefill_postgres`/
   `build_prefill_appgw` should be revisited to prefill field-by-field
   instead of composing into `RequestType.OTHER`'s `description`.
5. Decide whether Key Vault becomes a 6th selectable advisor menu item (its
   own question bank would need to be written) or stays reference-only
   permanently — currently reference-only per an explicit choice this
   session, not a gap that needs closing on its own timeline.
6. The environment composer (`advisor_kb/composer/`, minus `infosec_gate.yaml`
   which is already used) is Phase 3 — cross-service "whole environment"
   recommendations, `add_service`-driven auto-continuation into a second
   service's own conversation, etc. Explicitly out of scope for this round.
8. Fix the concentric layout's "hairball" density on denser Resource
   Relationship Graph queries. A real Puppeteer screenshot of the
   whole-subscription query (42 nodes, 35 edges) showed everything clustered
   into a dense central mass with heavy edge crossing and large empty
   canvas space around it — the "large empty areas" / "minimize edge
   crossing" goals from the original UI-polish spec aren't actually
   achieved despite `concentric` being chosen specifically to address them.
   Needs either tighter tuning (`minNodeSpacing`/`levelWidth` scaled by node
   count) or revisiting the earlier concentric-vs-fcose trade-off now that
   there's a concrete example of concentric's limits. See
   `.memory/daily/2026-08-02.md` ("First-ever real-browser visual
   verification" entry) for the screenshot-based evidence.
9. Test the Resource Relationship Graph against a genuinely large scope
   (500+ resources) — the "works with 500+ resources" verification target
   from the UI-polish spec hasn't been exercised; the real sandbox
   subscription used this session only has a few dozen resources. May need
   `RESGRAPH_MAX_NODES` raised from its default (300) and a bigger test
   subscription. Also worth checking alongside item 8, since a large graph
   will make the hairball problem worse, not better.
10. Verify AKS-, Storage-Account-, and Key-Vault-rooted Resource Graph
    queries against real Azure once such resources exist in a test
    subscription — currently only covered by mocked offline tests
    (`_expand_aks_node_rg`, `_expand_storage_containers`,
    `_expand_pe_dns_zone_group` are untested against live data).
11. Provision a real, dedicated Reader-only SP for `RESGRAPH_*` in
    Settings → Resource Graph for actual use — verification used the same
    credential values as the main automation SP, which is fine for testing
    but defeats the point of the isolated-credential design for real use
    (see `architecture-decisions.md` 2026-08-02 entry).
12. Delete the leftover empty `rg-claude-e2e-qa-test` resource group in
    subscription `845e564b-31a3-44b0-b030-226798b31574` ("Sandbox
    Connectivity") — reused across the 2026-08-02 Resource Graph VM test and
    this session's 2026-08-03 VM_CREATE dry-run-bug repro/cleanup, reverted
    each time, confirmed empty via `az resource list`; resource-group
    deletion itself keeps getting blocked by the session's permission
    classifier, so it needs a human (or a future session with that
    permission) to remove it.
13. Confirm `helm/subnet-manager/values.yaml`'s `existingSecretName:
    "almadar-db"` change is correct for the target cluster (real secret
    exists with that name, key `DATABASE_URL`) before this reaches prod via
    Helm upgrade. (Still uncommitted, unrelated to this session's work.)
14. Resolve `static/page-bg-original.jpg` (untracked) — decide
    keep-as-backup vs. delete.
15. Run `scripts/test_storage_validation.py` / `test_resourcegraph_validation.py`
    / `test_advisor_validation.py` in CI or as a pre-commit habit when their
    respective modules change — nothing currently runs any of them
    automatically (no CI configured in this repo).
16. Consider exercising the failure/partial-failure paths against real Azure
    too, if worth the cost/time: a mid-loop VM failure, a container-creation
    failure after the storage account exists, CMK/user-assigned-identity
    deploys, and the object-replication/private-endpoint best-effort steps —
    none of these were covered by the 2026-08-01 VM/Storage real E2E pass.

## P2 — Ongoing / standing

17. Keep `.memory/` updated as part of "definition of done" for every future
    feature (standing process requirement — see `CLAUDE.md` → "Session Memory
    Protocol").
18. Run `graphify update .` after any further code changes to keep the
    knowledge graph current.

## Backlog (not yet actionable — needs scoping)

- GPU utilization dashboard — prerequisites documented in
  `docs/GPU_UTILIZATION.md`, nothing implemented yet. Needs a decision on
  metrics pipeline (Azure Managed Prometheus recommended) before work starts.
- AI Architecture Advisor Phase 3 — the "whole environment" composer
  (`advisor_kb/composer/`, cross-service auto-continuation) — see P1 item 6.
