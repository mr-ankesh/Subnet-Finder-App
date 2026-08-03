# Next Actions

> Prioritized, actionable only. Completed items are removed (not archived —
> history lives in `daily/`/`weekly`/`monthly` and `architecture-decisions.md`).
> Last updated: 2026-08-04 (persistent conversational chat session).

## P0 — Ship-blocking

None currently.

## P1 — Follow-up

1. Visually confirm the Advisor's chat UI in an actual browser — all five
   single-service options, the environment-composer mode, AND (new this
   round) the persistent-conversation chat (sidebar + transcript +
   resume). A headless browser was genuinely ATTEMPTED this round, not
   just checked for: `pip install playwright` succeeded, but `playwright
   install chromium` never completed after 35+ minutes in this sandboxed
   environment's network (it retried its own download at least once) and
   was killed rather than left running — same standing gap as the
   Resource Relationship Graph, now confirmed as a real install failure,
   not just "not found". Next session: try again with more time budgeted,
   or from an environment with faster/unrestricted network egress.
   Verification so far: `scripts/test_advisor_validation.py` (232 checks)
   + `scripts/test_advisor_conversations.py` (38 checks, new) + real
   HTTP-level end-to-end runs against the live Flask dev server for every
   flow — not an actual rendered page in a browser. Manual click-through
   checklist for whenever a browser is available:
   - Sidebar: conversations actually group under Today/This week/Older
     correctly (not just by creation order), mode badge shows Svc/Env
     correctly, delete removes the row without a page reload glitch.
   - Transcript: scrolls to the latest message, guided vs. freeform turns
     are visually distinguishable at a glance (not just in the DOM), long
     recommendation content doesn't break the layout.
   - Chips render correctly for yes_no/single_choice questions and free
     text is always available alongside them; the working indicator shows
     during a real (slow) LLM call, not just instantly.
   - Recommendation re-renders identically on a fresh page load (resume)
     as it did live — both modes, including the environment plan's subnet
     table/arithmetic/Pod CIDR paragraph/InfoSec box/wave table.
   - The Mermaid diagram render (existing gap, still open) for both
     `environment_full.mmd` variants.
   - The mode-picker cards and "you'll also need" (add_services) section
     from the six-service/environment-composer rounds — still unverified
     visually from those earlier sessions too.
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
6. Cross-service `add_service`-driven auto-continuation (e.g. AKS flagging
   AppGW as a companion service actually launching that service's OWN
   guided conversation, rather than just listing it as "you'll also need")
   remains out of scope — noted but not built in the environment composer
   either, which instead computes the full cross-service plan directly
   from one intake rather than chaining conversations.
7. `env_prefill.py`'s per-wave-item prefill is deliberately shallow (tag
   fields + a handful of directly-collected answers, not a real
   pattern-selected settings table) — see `CLAUDE.md` → "Environment
   composer" for why. If this proves insufficient in practice, the
   alternative is asking each embedded service's own full question set
   during the environment intake too, which conflicts with the intake's
   explicit "deliberately short" design goal — worth a product decision,
   not a silent code change.
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
19. Migrate the original single-shot advisor routes (`/api/advisor/chat`,
    `/diagram`, `/prefill`, and the four `/environment/*`) onto the new
    `advisor/conversations.py` schema, deprecating `advisor/session_store.py`
    entirely. Deliberately NOT done in the persistent-chat build — that
    build was explicitly additive (new tables, new routes, `advisor_sessions`
    and every existing route untouched) specifically to avoid rewriting seven
    routes that had just been verified live. Once this migration happens,
    `session_store.py` can be deleted and the `?advisor_session=<id>` prefill
    handoff should be re-pointed at the new schema.
20. Move the raw-SQL table inventory currently living only in
    `scripts/sqlite_to_postgres.py`'s `TABLES` list into a proper
    `RAW_SQL_TABLES` constant in `db_backend.py`, with the migration script
    importing/reading from there instead of maintaining its own copy. That
    script is otherwise dead code now that prod is already Postgres — a dead
    script being the sole authoritative inventory of every raw-SQL table only
    works while everyone remembers it's there and remembers to update it.
    This has already caused real gaps before (`subscription_inventory`,
    `budget_alert_state`, `agent_chats` each went missing from it until
    someone noticed) and the three new `advisor_conversations`/
    `advisor_messages`/`advisor_state` tables just added to it are exactly
    the kind of addition that's easy to forget next time.

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
