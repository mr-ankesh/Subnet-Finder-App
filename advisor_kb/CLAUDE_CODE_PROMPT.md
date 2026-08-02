# Claude Code prompt — AI Architecture Advisor V1

Paste the block below into Claude Code from the repo root, **after** copying the
`advisor_kb/` folder into the repo (see "Where to put the files" at the bottom).

---

```text
Build the AI Architecture Advisor V1 (Storage only).

A knowledge base already exists at advisor_kb/. Read it first — it is the source of
truth. Do not invent architecture, defaults or policies that aren't in it.

advisor_kb/
  README.md                              provenance + non-negotiables
  catalog/_schema.md                     pattern schema + scoring rules
  catalog/*.yaml                         5 approved Presight patterns
  questions/storage_questions.yaml       intake flow, in ask-order
  rules/storage_decision_matrix.yaml     deterministic logic, runs BEFORE the LLM
  mapping/storage_request_mapping.yaml   answers -> storage_account_create fields
  templates/recommendation_template.md   output shape + 3 system prompts
  diagrams/*.mmd + README.md             Mermaid templates, placeholder-filled only

Core principle, non-negotiable:
  Rules decide. LLM explains. Forms validate. Azure deploys.

## Scope V1
- Storage advisory only. No VM/AKS/network advisory.
- Read-only. Zero Azure mutations. No new Azure SDK calls.
- Prefills the existing storage_account_create request. NEVER auto-submits.
- Requester-accessible, same auth pattern as the existing requester agent.

## Architecture — follow the repo's existing conventions
- New lazy-imported feature module `advisor/`, imported inside route functions only,
  exactly like agent_requester / agent_admin. Do not add module-level imports to app.py
  beyond the route surface.
- Reuse the configured LLM client and AGENT_PROVIDER settings. Do not add a provider.
- Reuse chats.py for conversation persistence if its shape fits; if it doesn't, add an
  advisor_sessions table via its own ensure_table() raw-SQL module and REMEMBER to add
  it to scripts/sqlite_to_postgres.py — raw-SQL tables are not picked up automatically.
- No module-level caches for session state. Prod runs 3 replicas. KB files are static
  config and may be loaded once per process; conversation state must go through the DB.
- Server-rendered Jinja + vanilla JS. No React, no build step.
- Use static/css/tokens.css for all colour/typography. No hard-coded colours.
- No per-card backdrop-filter and no scroll-linked JS animation (BRANDING.md perf rules).

## Build

1. advisor/ module
   - catalog_loader.py   load + validate YAML against catalog/_schema.md at startup;
                         fail loudly with the offending file/key on a malformed KB
   - question_engine.py  ask-order, skip_if, stop_if, follow_up_if, default_if_unknown
   - rules_engine.py     executes rules in the declared execution_order:
                         blockers -> escalations -> constants -> derivations ->
                         pattern_selection -> deviations -> warnings
   - pattern_matcher.py  scoring per catalog/_schema.md, incl. overrides + tiebreaks
   - prefill.py          build the prefill payload from mapping/
   - diagram_builder.py  placeholder substitution only; escape < > " and backticks
   - prompts.py          load the 3 system prompts from templates/

2. Routes in app.py
   - GET  /advisor                     page
   - POST /api/advisor/chat            next question OR final recommendation
   - POST /api/advisor/diagram         rendered Mermaid source
   - POST /api/advisor/prefill         persist payload, return the request-form URL
   All return {ok, data, error}. Never 500 to the browser on an LLM failure.

3. templates/advisor.html
   - Chat-style, one question at a time, numbered options as clickable chips
   - Free text accepted alongside chips
   - Answer summary the user can correct before the recommendation renders
   - Recommendation rendered per templates/recommendation_template.md, section order fixed
   - "Open the Storage Account request" button
   - Diagram offered last, rendered with Mermaid, securityLevel: 'strict'
   - Vendor mermaid into static/vendor/ — no CDN script tag

4. Prefill handoff
   - Write payload to a server-side advisor session
   - Open the storage form with ?advisor_session=<id>
   - Do NOT put the payload in the query string
   - Prefilled fields stay editable
   - _validate_storage_request() remains the sole validation authority — do not weaken,
     bypass or duplicate it

5. Navigation
   - Add to the Operations group in base.html, respecting the existing role blocks
   - Requester-visible

## Guardrails to implement explicitly
- Blockers halt the conversation and render the blocked-path template:
  no subscription -> HALO portal; public access requested -> policy exception;
  sovereign data -> platform + security teams.
- The LLM must never override a rule outcome. Rule output is authoritative.
- Never present ZRS / CMK / TLS 1.2 / private-endpoint-only as user choices.
- Never emit a currency figure, an SLA, or a policy name not present in the KB.
- Deviations and warnings are always surfaced, never suppressed.
- If no pattern scores above zero, escalate — do not guess.

## Verification (do all of these before reporting done)
1. "I need a storage account" starts the question flow, one question at a time.
2. Answering "no subscription" halts and points at the HALO portal.
3. Application files + AKS + SDK  -> storage_blob_private_standard.
4. Shared drive + SMB            -> storage_files_private_standard.
5. Analytics + Databricks        -> storage_datalake_private, with the HNS warning.
6. Compliance retention + rarely read -> storage_archive_retention, and the recommendation
   explicitly states the LRS/GRS deviation from the ZRS baseline.
7. Premium requested with no measured evidence -> downgraded to Standard, flagged.
8. Sovereign classification      -> blocked, routed to platform + security.
9. Recommendation always includes the DNS link as a required request.
10. ZPA request appears only when end-user access was selected.
11. Prefill opens the storage form populated; all fields editable; nothing auto-submitted.
12. Diagram renders with no raw {PLACEHOLDER} tokens left.
13. Malformed KB YAML fails loudly at startup, not silently at request time.

## Docs + memory (definition of done)
- CLAUDE.md: new "AI Architecture Advisor" architecture section
- docs/HOW_IT_WORKS.md: where the advisor sits in the request lifecycle
- .memory/current-state.md, next-actions.md, today's daily file
- .memory/architecture-decisions.md: record the "rules decide, LLM explains" decision
- graphify update . when done

## Output order
1. A short plan: files touched, what changes in each. Wait for my OK.
2. Then implement in stages, smallest reviewable commits.
```

---

## Where to put the files

```bash
cd ~/Library/CloudStorage/OneDrive-PresightAI/Desktop/Projects/Subnet-finder-app

# unzip the download next to the repo, then:
cp -r ~/Downloads/almadar-advisor-kb ./advisor_kb

git add advisor_kb
git commit -m "feat(advisor): add Presight storage architecture knowledge base"
```

Final layout:

```
Subnet-finder-app/
├── advisor_kb/          <- the knowledge base (this download)
├── advisor/             <- Claude Code creates this
├── app.py
├── azure_tools.py
├── templates/
└── CLAUDE.md
```

## Before you run the prompt — two decisions

1. **`ServiceClass` tag mapping.** `mapping/storage_request_mapping.yaml` infers
   Bronze/Silver/Gold/Platinum from criticality. That mapping is *inferred*, not quoted
   from the design documents — confirm it with the platform team or drop the tag.

2. **Container naming.** The advisor doesn't invent container names. If you'd rather it
   suggested a default (e.g. `data`, `logs`), add it to the mapping as non-blocking.
