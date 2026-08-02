# Next Actions

> Prioritized, actionable only. Completed items are removed (not archived —
> history lives in `daily/`/`weekly`/`monthly` and `architecture-decisions.md`).
> Last updated: 2026-08-02.

## P0 — Ship-blocking

None currently.

## P1 — Follow-up

1. **Commit the Resource Relationship Graph UI polish pass** (built +
   partially real-Azure verified 2026-08-02, currently uncommitted, on top
   of the already-committed `66824be`) — `resourcegraph.py` (`hub_id`/
   `tags`/`subscriptionId`), `templates/resource_graph.html` (full rewrite:
   per-type styling, concentric layout, tooltip, relationship analysis,
   stats bar, minimap, theme), `scripts/test_resourcegraph_validation.py`
   (36 checks), CLAUDE.md section update.
2. Visually confirm the Resource Graph frontend in an actual browser — no
   headless-browser tool has been available in any session so far; every
   verification has been structural (page renders, JS syntax valid, manual
   code review) or via the API contract, never an actual rendered page.
   Specifically needs eyes on: no overlapping labels on initial render,
   hub-spoke topology obviousness, the concentric layout's visual quality,
   icon rendering, tooltip positioning, the minimap, and whether the
   `cytoscape-navigator` CSS class name guessed for its viewport-indicator
   styling (`cytoscape-navigatorView`) is actually correct.
3. Test the graph against a genuinely large scope (500+ resources) — the
   "works with 500+ resources" verification target from the UI-polish spec
   hasn't been exercised; the real sandbox subscription used this session
   only has a few dozen resources. May need `RESGRAPH_MAX_NODES` raised
   from its default (300) and a bigger test subscription.
4. Verify AKS-, Storage-Account-, and Key-Vault-rooted Resource Graph
   queries against real Azure once such resources exist in a test
   subscription — currently only covered by mocked offline tests
   (`_expand_aks_node_rg`, `_expand_storage_containers`,
   `_expand_pe_dns_zone_group` are untested against live data).
5. Provision a real, dedicated Reader-only SP for `RESGRAPH_*` in
   Settings → Resource Graph for actual use — verification this session
   used the same credential values as the main automation SP, which is
   fine for testing but defeats the point of the isolated-credential design
   for real use (see `architecture-decisions.md` 2026-08-02 entry).
6. Delete the leftover empty `rg-claude-e2e-qa-test` resource group in
   subscription `845e564b-31a3-44b0-b030-226798b31574` ("Sandbox
   Connectivity") — reused (not recreated) for the 2026-08-02 Resource Graph
   VM test, reverted again, confirmed empty via `az resource list`;
   resource-group deletion itself was blocked by the session's permission
   classifier both times, so it needs a human (or a future session with
   that permission) to remove it.
7. Confirm `helm/subnet-manager/values.yaml`'s `existingSecretName:
   "almadar-db"` change is correct for the target cluster (real secret
   exists with that name, key `DATABASE_URL`) before this reaches prod via
   Helm upgrade. (Still uncommitted, unrelated to VM/Storage/Resource-Graph
   work.)
8. Resolve `static/page-bg-original.jpg` (untracked) — decide keep-as-backup
   vs. delete.
9. Run `scripts/test_storage_validation.py` / `scripts/test_resourcegraph_validation.py`
   in CI or as a pre-commit habit when their respective modules change —
   nothing currently runs either automatically (no CI configured in this
   repo).
10. Consider exercising the failure/partial-failure paths against real Azure
    too, if worth the cost/time: a mid-loop VM failure, a container-creation
    failure after the storage account exists, CMK/user-assigned-identity
    deploys, and the object-replication/private-endpoint best-effort steps —
    none of these were covered by the 2026-08-01 VM/Storage real E2E pass.

## P2 — Ongoing / standing

11. Keep `.memory/` updated as part of "definition of done" for every future
    feature (standing process requirement — see `CLAUDE.md` → "Session Memory
    Protocol").
12. Run `graphify update .` after any further code changes to keep the
    knowledge graph current.

## Backlog (not yet actionable — needs scoping)

- GPU utilization dashboard — prerequisites documented in
  `docs/GPU_UTILIZATION.md`, nothing implemented yet. Needs a decision on
  metrics pipeline (Azure Managed Prometheus recommended) before work starts.
