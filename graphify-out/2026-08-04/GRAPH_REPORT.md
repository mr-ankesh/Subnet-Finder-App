# Graph Report - Subnet-finder-app  (2026-08-04)

## Corpus Check
- 90 files · ~277,014 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1752 nodes · 3652 edges · 104 communities (98 shown, 6 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 15 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `93f97e71`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- insert_returning_id
- _guard
- costmgmt.py
- notifications.py
- add_firewall_network_rule
- reachability.py
- app.py
- route
- azure_tools.py
- record
- _network_client
- _get_credential
- admin_azure_action
- agent_admin.py
- datetime
- optimize.py
- create_spoke_vnet
- _compute_client
- netdiag.py
- approvals.py
- render.py
- require_subnet_access
- api_advisor_chat
- Expected output
- auth_oidc.py
- Architecture
- settings_store.py
- prompts.py
- composition_engine.py
- network_planner.py
- 2026-08-01
- SpokeRequest
- RequestType
- RequestProxy
- models.py
- resourcegraph.py
- brand.js
- intake.py
- admin_settings_save
- zpa-networkuser-wrapper.sh
- Keycloak (OIDC) Integration Guide
- deploy.sh
- require_itadmin
- orchestrator.py
- Architecture Decisions
- Approval
- changes.py
- Migration notes — adding services to the existing advisor
- Azure access required by AlMadar 360
- How Network Copilot Works — High-Level Architecture
- Current State
- Project Overview
- test_storage_validation.py
- _auto_advance
- Production Deployment (Docker / Kubernetes)
- GPU Utilisation Dashboard — Prerequisites & Plan
- Network Copilot — Brand & Motion System
- ZPA connector VM — read-only `networkuser` for the ZPA Analyzer
- Network Copilot — Helm Chart
- Month 2026-07
- Month 2026-08
- Feature Roadmap
- Next Actions
- Week 2026-W31 (Mon 2026-07-27 → Sun 2026-08-02)
- db_backend.py
- Known Issues
- migrate_excel_to_db.py
- README.md
- _containerservice_client
- subinventory.py
- 2026-08-02
- inventory_parser.py
- _Conn
- requester_get_status
- search.py
- Environment recommendation template
- composer/__init__.py
- conversations.py
- db_utils.py
- _create_service_request
- freeform.py
- evaluate_safe
- agent_requester.py
- diagram_builder.py
- recommendation.py
- auth_callback
- get_composer_file
- chats.py
- glossary.py
- _import_inventory
- test_advisor_conversations.py
- env_prefill.py
- 2026-08-04
- test_advisor_validation.py
- catalog_loader.py
- prefill.py
- question_engine.py
- pattern_matcher.py
- AlMadar AI Architecture Advisor — Knowledge Base (Storage V1)
- Recommendation output template
- 2026-08-03
- Diagram templates
- Catalog schema

## God Nodes (most connected - your core abstractions)
1. `require_login()` - 47 edges
2. `_guard()` - 45 edges
3. `record()` - 43 edges
4. `_network_client()` - 40 edges
5. `_is_not_found()` - 35 edges
6. `require_admin()` - 34 edges
7. `require_superadmin()` - 33 edges
8. `Architecture Decisions` - 29 edges
9. `current_actor()` - 27 edges
10. `2026-08-01` - 27 edges

## Surprising Connections (you probably didn't know these)
- `_SkipMigration` --uses--> `Approval`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `RequestStatus`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `RequestType`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `SpokeRequest`  [INFERRED]
  app.py → models.py
- `add_route_to_table()` --calls--> `_norm()`  [INFERRED]
  azure_tools.py → db_backend.py

## Import Cycles
- None detected.

## Communities (104 total, 6 thin omitted)

### Community 0 - "insert_returning_id"
Cohesion: 0.17
Nodes (8): _EmptyCursor, insert_returning_id(), _norm(), INSERT that returns the new row id on both backends., Normalise a cell so raw callers see the SQLite-era shape (str timestamps)., Eager, backend-neutral result: dict rows materialised at fetch time., Rows affected by the last UPDATE/DELETE — verified identical on both psycopg…, _Result

### Community 1 - "_guard"
Cohesion: 0.05
Nodes (60): add_cidr_to_firewall_rule(), add_route_to_table(), check_private_dns_zone(), create_dns_zone_in_hub(), decommission_check(), delete_dns_record(), delete_dns_zone(), delete_dns_zone_link() (+52 more)

### Community 2 - "costmgmt.py"
Cohesion: 0.11
Nodes (32): _cols(), _compute_summary(), configured(), cost_by_dimension(), cost_by_resource(), cost_daily(), _descendants(), _headers() (+24 more)

### Community 3 - "notifications.py"
Cohesion: 0.09
Nodes (49): _adaptive_card(), _compose_email(), draft_budget_alert(), _draft_email(), draft_threshold_alert(), _email_case(), _email_requester(), _looks_unusable() (+41 more)

### Community 4 - "add_firewall_network_rule"
Cohesion: 0.10
Nodes (27): add_firewall_application_rule(), add_firewall_network_rule(), allow_internet_rule(), _build_fw_rule(), _collection_kind_conflict(), _describe_fw_rule(), find_firewall_rules_for_address(), find_firewall_rules_for_pair() (+19 more)

### Community 5 - "reachability.py"
Cohesion: 0.10
Nodes (38): _classify(), configured(), _cpu_line(), health_all(), _is_ip(), _load_key(), _net_ifaces(), _num() (+30 more)

### Community 6 - "app.py"
Cohesion: 0.07
Nodes (60): _advisor_environment_session(), _advisor_owner_key(), advisor_page(), agent_chat(), agent_chat_delete(), agent_chat_get(), agent_chats_list(), api_advisor_conversations_create() (+52 more)

### Community 7 - "route"
Cohesion: 0.08
Nodes (44): admin_approvals_health(), admin_settings(), admin_settings_preview_name(), admin_settings_test_azure(), admin_settings_test_connector(), admin_settings_test_cost(), admin_settings_test_keycloak(), admin_settings_test_optimize() (+36 more)

### Community 8 - "azure_tools.py"
Cohesion: 0.08
Nodes (33): _addr_covers(), aks_tiers(), _analyze_coverage(), assign_vm_zones(), build_vm_plan(), derive_vm_resource_names(), derive_windows_computer_name(), _extract_taken_indexes() (+25 more)

### Community 9 - "record"
Cohesion: 0.07
Nodes (44): admin_assign_cidr_api(), admin_change_revert(), admin_changes(), admin_deallocate_api(), admin_find_subnets_api(), admin_firewall_lookup(), admin_list_requests_api(), admin_search() (+36 more)

### Community 10 - "_network_client"
Cohesion: 0.09
Nodes (25): add_cidr_to_nsg_rule(), add_udr_routes(), assign_route_table_to_subnet(), check_udr(), get_nsg_rule_status(), list_subnets(), list_vnet_subnets(), _network_client() (+17 more)

### Community 11 - "_get_credential"
Cohesion: 0.09
Nodes (26): aks_source_subnet(), _diag_subs(), _get_credential(), _is_cidr(), list_keyvault_keys(), list_keyvaults(), list_locations(), list_subscriptions() (+18 more)

### Community 12 - "admin_azure_action"
Cohesion: 0.09
Nodes (31): admin_azure_action(), admin_storage_preview(), _deploy_one(), _deploy_spoke_route_table(), _deploy_tags(), _fw_params(), For a 'required @ trigger' request type, block an Azure deploy until the line…, Parse the firewall-request details into SDK-ready parameters. ports_protocol… (+23 more)

### Community 13 - "agent_admin.py"
Cohesion: 0.16
Nodes (25): _actor(), build_system_prompt(), chat(), _chat_anthropic(), _chat_openai(), _compute_free(), _execute_tool(), _get_client() (+17 more)

### Community 14 - "datetime"
Cohesion: 0.22
Nodes (14): assess(), _conn(), ensure_table(), evaluate_and_send(), _last_severity(), Automatic over-budget alerts for subscriptions. The hard part isn't emailing at…, Check every opted-in subscription and email escalations. Returns a report:…, Start the periodic budget checker as a daemon thread (idempotent). Runs only… (+6 more)

### Community 15 - "optimize.py"
Cohesion: 0.14
Nodes (24): _arg(), configured(), _disk_month(), _f(), _headers(), list_subscriptions(), _metric_stats(), _pip_month() (+16 more)

### Community 16 - "create_spoke_vnet"
Cohesion: 0.08
Nodes (30): carve_subnets(), check_storage_name_availability(), create_aks_disk_encryption(), create_object_replication_policy(), create_route_table(), create_spoke_vnet(), create_storage_account(), create_storage_private_endpoint() (+22 more)

### Community 17 - "_compute_client"
Cohesion: 0.10
Nodes (22): check_vm_quota(), _compute_client(), list_aks_versions(), list_marketplace_images(), list_vm_images(), list_vm_sizes(), list_vm_skus(), list_vm_zones() (+14 more)

### Community 18 - "netdiag.py"
Cohesion: 0.14
Nodes (21): _addr_in(), _clean_llm(), diagnose(), _has_cjk(), _hub_fw_ip(), _is_ip(), _is_private(), _is_private_domain() (+13 more)

### Community 19 - "approvals.py"
Cohesion: 0.15
Nodes (19): enabled(), has_valid_trigger_approval(), manager_seen(), _mgr_state(), needs_trigger_approval(), open_submission_gate(), policy_for(), preflight() (+11 more)

### Community 20 - "render.py"
Cohesion: 0.16
Nodes (19): _env_label(), _fallback_summary(), Deterministic renderer for environment_recommendation_template.md's shape. This…, Verbatim from infosec_gate.yaml's user_message — never LLM-touched., Normalizes to {step, detail} only. One entry in network_sizing.yaml's…, Everything the frontend/template needs, fully structured — never a single…, The one paragraph an LLM narration pass may rewrite; this is the deterministic…, Never renders the Pod CIDR as a subnet row — it's returned as a separate… (+11 more)

### Community 21 - "require_subnet_access"
Cohesion: 0.15
Nodes (22): all_available(), allocate(), allocated(), allocator(), available_base_route(), candidates_from_free(), compute_free_blocks(), deallocate() (+14 more)

### Community 22 - "api_advisor_chat"
Cohesion: 0.24
Nodes (17): detect_public_access_request(), _conn(), create_session(), ensure_table(), get_session(), _now(), owns(), Persistent advisor conversation state — raw sqlite3/db_backend, same pattern as… (+9 more)

### Community 23 - "Expected output"
Cohesion: 0.11
Nodes (17): Before you start, Build sequence, Changelog — 2.0.0 → 2.1.0, Components, Expected output, Hub integration, Input, Negative test — must also pass (+9 more)

### Community 24 - "auth_oidc.py"
Cohesion: 0.13
Nodes (18): client(), _decode_jwt_payload(), end_session_url(), groups_from_token(), init_oidc(), manager_from_token(), _metadata_url(), Keycloak (OIDC) integration — Authlib. Kept deliberately thin: the OIDC… (+10 more)

### Community 25 - "Architecture"
Cohesion: 0.07
Nodes (28): AI agents are tool-callers, not free-form SQL/Azure access, AI Architecture Advisor: rules decide, LLM explains — not a `RequestType`, Approval flow: relationship-based routing with a dependency gate, Architecture, Auth: local password or Keycloak SSO, switched live, Azure changes: imperative SDK calls, not IaC, Budget alerts: forecast-gated, not raw-threshold, CIDR pool allocator (the app's namesake feature) (+20 more)

### Community 26 - "settings_store.py"
Cohesion: 0.23
Nodes (16): all_overrides(), _conn(), _decrypt(), delete_override(), _encrypt(), ensure_table(), _fernet(), get_override() (+8 more)

### Community 27 - "prompts.py"
Cohesion: 0.18
Nodes (15): load_text_file(), call_llm(), _get_client(), get_environment_recommendation_template(), get_environment_system_prompt(), get_recommendation_template(), get_system_prompts(), Loads the 3 system prompts verbatim from… (+7 more)

### Community 28 - "composition_engine.py"
Cohesion: 0.22
Nodes (17): dependency_graph(), environment_deviations(), environment_warnings(), evaluate_environment_blockers(), evaluate_full(), exposure_analysis(), infer_missing_components(), Runs advisor_kb/composer/composition_rules.yaml's 8-phase pipeline. Unlike… (+9 more)

### Community 29 - "network_planner.py"
Cohesion: 0.15
Nodes (26): _approx_capacity_nodes(), _bucket_lookup(), build_network_plan(), compute_vnet_plan(), mandatory_spoke_wiring(), _pe_service_names(), pod_cidr_info(), _prefix_total_usable() (+18 more)

### Community 30 - "2026-08-01"
Cohesion: 0.07
Nodes (27): 2026-08-01, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions (+19 more)

### Community 31 - "SpokeRequest"
Cohesion: 0.19
Nodes (9): open_trigger_gate(), Pick the approver for a new checkpoint. The requester's line manager if we have…, Recompute the cached approval_state on the request from its checkpoints., Admin-initiated: send a specific request for approval (discretion mode)., Raise a pending trigger checkpoint (called when a blocked deploy is attempted)., request_discretion_approval(), resolve_approver(), _sync_request_state() (+1 more)

### Community 32 - "RequestType"
Cohesion: 0.20
Nodes (4): policy_matrix(), View model for the settings matrix: one row per request type., Request kinds available in the requester portal, each with its own workflow., RequestType

### Community 33 - "RequestProxy"
Cohesion: 0.25
Nodes (3): Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field., RequestProxy, RequestStatus

### Community 34 - "models.py"
Cohesion: 0.12
Nodes (13): Exception, Sentinel to short-circuit the SQLite-only column backfill on Postgres., _SkipMigration, AppSetting, AuditLog, FwCollection, SQLAlchemy models for Spoke Request workflow and subnet inventory., Admin-editable config override (see settings_store.py, which reads/writes this… (+5 more)

### Community 35 - "resourcegraph.py"
Cohesion: 0.07
Nodes (43): _coerce(), Config, Central config — every value resolves live as: DB override → env var → default.…, Attribute access resolves live: DB override → env → default., _add_edge(), _arg(), build_graph(), _category_for_type() (+35 more)

### Community 37 - "intake.py"
Cohesion: 0.23
Nodes (14): _coerce(), find_question(), is_complete(), next_question(), _normalize_question(), _questions(), _questions_in_order(), Environment-mode intake flow controller —… (+6 more)

### Community 38 - "admin_settings_save"
Cohesion: 0.25
Nodes (8): admin_settings_reset(), admin_settings_save(), Return an error string, or None if the value is acceptable., _validate_setting(), Effective raw string value + its source: ('override'|'env'|'default')., Per-category field list for the settings page. Secret values are never included…, resolve(), settings_view()

### Community 39 - "zpa-networkuser-wrapper.sh"
Cohesion: 0.83
Nodes (3): _allow(), _deny(), zpa-networkuser-wrapper.sh script

### Community 40 - "Keycloak (OIDC) Integration Guide"
Cohesion: 0.10
Nodes (19): 3a. OIDC client registration (new `auth_oidc.py`), 3b. Routes (in app.py), 3c. Switch on `AUTH_PROVIDER`, 3d. Audit actor, How a user's team is determined, How it behaves once active, Keycloak (OIDC) Integration Guide, Keycloak-side prerequisite: the `groups` claim (+11 more)

### Community 42 - "require_itadmin"
Cohesion: 0.25
Nodes (8): it_connector_health(), it_connector_status(), it_reachability(), it_reachability_run(), Health dashboard: are the connector VMs (primary + secondary) up?, Richer per-VM diagnostics for the dashboard's 'More status'., Guards the Reachability Tester — IT-admins OR super-admins. Open when SSO is…, require_itadmin()

### Community 43 - "orchestrator.py"
Cohesion: 0.15
Nodes (22): new_state(), append_message(), Appends one transcript row, auto-incrementing `seq` per conversation. Sets the…, _touch(), _advance_guided(), _build_service_recommendation(), classify_turn(), detect_correction() (+14 more)

### Community 44 - "Architecture Decisions"
Cohesion: 0.07
Nodes (29): 2026-03-26 — AI agents are tool-callers only, never raw SQL/Azure access, 2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent, 2026-07-15 → 2026-07-29 — Rebrand: Subnet Manager → Network Copilot → Presight AlMadar 360, 2026-07-18 — PostgreSQL as an optional backend, SQLite stays default, 2026-07-26 — Budget alerts are forecast-gated, not raw-threshold, 2026-07-30 — Line-manager approval routing, dependency-gated, 2026-07-31 (approx) — Password never persisted for VM auth, 2026-07-31 — VM(s) deployment: plan resolved once, persisted, resumable (+21 more)

### Community 45 - "Approval"
Cohesion: 0.22
Nodes (8): can_decide(), decide(), pending_for(), Is this actor allowed to approve/reject this checkpoint?, Record an approve/reject decision and reconcile the request status., Approvals awaiting this actor's decision (their reports' requests, plus…, Approval, A single approval checkpoint on a request — routed to the requester's line…

### Community 46 - "changes.py"
Cohesion: 0.33
Nodes (12): _conn(), ensure_table(), execute_revert(), get_change(), list_changes(), _mark(), Change ledger — the platform's undo history. Every mutating operation records…, Restore the earlier state recorded in change #cid. Reason is mandatory and… (+4 more)

### Community 47 - "Migration notes — adding services to the existing advisor"
Cohesion: 0.17
Nodes (11): 1. `catalog_loader.py` — service-aware resolution, 2. Service selection step, 3. Two request types don't exist yet, 4. Existing types that DO exist, Code changes required, Consistency with your storage implementation, Design decision: additive, not a restructure, Files in this delta (+3 more)

### Community 48 - "Azure access required by AlMadar 360"
Cohesion: 0.22
Nodes (8): 1. Automation service principal, 2. Cost service principal (separate), 3. Optimizer service principal (separate), Azure access required by AlMadar 360, By feature, Least-privilege custom role (data actions it performs), Setup checklist, Simplest grant

### Community 49 - "How Network Copilot Works — High-Level Architecture"
Cohesion: 0.22
Nodes (8): 1. The one-paragraph answer, 2. Why not ARM templates / Bicep / Terraform?, 3. What each admin action does in Azure, 4. Identity & permissions, 5. Request lifecycle (end to end), 6. Data & configuration, 7. Deploying the app itself, How Network Copilot Works — High-Level Architecture

### Community 50 - "Current State"
Cohesion: 0.14
Nodes (13): Active Priorities, AI Architecture Advisor — storage-only V1, then expanded to six services, Current Blockers, Current State, Development Status, Environment composer — Phase 3, fully committed (`2298611`/`6fde2de`/`9da0fe1`/`5144094`/`7e67cae`), Features Completed (committed, on `main`), Features In Progress (+5 more)

### Community 51 - "Project Overview"
Cohesion: 0.22
Nodes (8): High-Level Architecture, Important Technologies, Important Workflows, Key Capabilities, Main Modules, Project Overview, Purpose, See Also

### Community 52 - "test_storage_validation.py"
Cohesion: 0.39
Nodes (7): Validation for a Storage Account request at submission time. Offline checks…, _validate_storage_request(), base_details(), check(), main(), Assert-based coverage of Storage Account request validation…, run_validate()

### Community 53 - "_auto_advance"
Cohesion: 0.15
Nodes (18): admin_audit(), _auto_advance(), _done_actions(), _pending_deploy_actions(), Portal actions that have succeeded for this request (dry-run included), minus…, Portal actions that must succeed before the request auto-completes., Move the status forward based on what has actually been done via the portal —…, Ordered list of required deploy steps not yet done, for the aggregated deploy.… (+10 more)

### Community 54 - "Production Deployment (Docker / Kubernetes)"
Cohesion: 0.25
Nodes (7): 1 — Image, 2 — Pre-deploy checklist, 3a — Deploy with Helm (preferred), 3b — Deploy with raw manifests, 4 — Post-deploy configuration (no restarts), 5 — Operations, Production Deployment (Docker / Kubernetes)

### Community 55 - "GPU Utilisation Dashboard — Prerequisites & Plan"
Cohesion: 0.25
Nodes (7): 1. On every GPU host (VM or AKS node), 2. A metrics pipeline (pick one), 3. Access (a read-only identity), 4. Config the app will need (once the pipeline exists), 5. What we'll build on top (once prerequisites are met), GPU Utilisation Dashboard — Prerequisites & Plan, Minimum viable path

### Community 56 - "Network Copilot — Brand & Motion System"
Cohesion: 0.29
Nodes (6): Don'ts, File map, Network Copilot — Brand & Motion System, Performance & accessibility contract, Recipes for new pages, Tokens you should use (never hard-code)

### Community 57 - "ZPA connector VM — read-only `networkuser` for the ZPA Analyzer"
Cohesion: 0.29
Nodes (6): Exact commands the analyzer runs (all read-only), Going deeper (needs extra access), Is `networkuser` already read-only?, Optional hardening — confine the key to read-only commands, Trade-off, ZPA connector VM — read-only `networkuser` for the ZPA Analyzer

### Community 58 - "Network Copilot — Helm Chart"
Cohesion: 0.29
Nodes (6): Guard rails baked into the templates, Key values, Network Copilot — Helm Chart, Operations, Quick start, Scaling out with PostgreSQL

### Community 59 - "Month 2026-07"
Cohesion: 0.29
Nodes (6): Lessons Learned, Major Deliverables, Month 2026-07, Outstanding Work (carried into August), Significant Architectural Changes, Upcoming Priorities (as of month-end)

### Community 60 - "Month 2026-08"
Cohesion: 0.29
Nodes (6): Lessons Learned, Major Deliverables (so far), Month 2026-08, Outstanding Work, Significant Architectural Changes, Upcoming Priorities

### Community 61 - "Feature Roadmap"
Cohesion: 0.33
Nodes (5): Deferred Work, Feature Roadmap, Future Enhancements (no design doc yet — ideas surfaced during past work), In Flight (see `current-state.md` for detail), Planned / Designed but Not Built

### Community 62 - "Next Actions"
Cohesion: 0.33
Nodes (5): Backlog (not yet actionable — needs scoping), Next Actions, P0 — Ship-blocking, P1 — Follow-up, P2 — Ongoing / standing

### Community 63 - "Week 2026-W31 (Mon 2026-07-27 → Sun 2026-08-02)"
Cohesion: 0.33
Nodes (5): Carry-Forward Items, Important Decisions, Major Accomplishments, Risks, Week 2026-W31 (Mon 2026-07-27 → Sun 2026-08-02)

### Community 64 - "db_backend.py"
Cohesion: 0.25
Nodes (9): connect(), _database_url(), Backend-agnostic database access for the raw-SQL modules (db_utils, audit,…, A wrapped connection. `with connect() as conn: conn.execute(...)`., URI for Flask-SQLAlchemy — Postgres (psycopg3 driver) or the SQLite file., URI with any password masked — for /health output., safe_uri(), sqlalchemy_uri() (+1 more)

### Community 65 - "Known Issues"
Cohesion: 0.40
Nodes (4): Investigations Needed, Known Issues, Open Bugs, Technical Debt

### Community 66 - "migrate_excel_to_db.py"
Cohesion: 0.60
Nodes (4): ensure_table(), get_pool_key(), One-time migration script: imports subnet inventory from subnets.xlsx into the…, run()

### Community 68 - "_containerservice_client"
Cohesion: 0.22
Nodes (10): _aks_dns_prefix(), _aks_pool_summary(), _containerservice_client(), create_aks_cluster(), delete_aks_cluster(), get_aks_cluster_status(), A valid dnsPrefix: alphanumerics/hyphens, start+end alphanumeric, ≤ 54 chars., Read-only: does the cluster exist and what is its provisioningState? Runs for… (+2 more)

### Community 69 - "subinventory.py"
Cohesion: 0.36
Nodes (9): all_records(), _conn(), ensure_table(), Subscription inventory — the manually-owned metadata that Azure doesn't hold:…, All stored inventory rows keyed by subscription id., Create or update the owner/budget metadata for a subscription., Toggle scheduled over-budget alerts for one subscription (updates only that…, set_auto_alerts() (+1 more)

### Community 70 - "2026-08-02"
Cohesion: 0.08
Nodes (24): 2026-08-02, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Design Decisions, Design Decisions (+16 more)

### Community 71 - "inventory_parser.py"
Cohesion: 0.27
Nodes (10): _count_for(), _counted_pattern(), format_confirmation(), _normalize_word_numbers(), parse_inventory(), Parses a free-text environment inventory ("10 VMs, 1 AKS cluster and a managed…, Best-effort structured parse. Always 0 for anything not mentioned — never…, Fills environment_questions.yaml's `confirm_template`'s {parsed_inventory}… (+2 more)

### Community 73 - "requester_get_status"
Cohesion: 0.29
Nodes (7): _owns_request(), The signed-in requester's own requests (SSO only). Empty in open mode., (name, email) identifying the signed-in requester's requests, or None when SSO…, True if the current user may view this request. Admins: all. SSO requesters:…, requester_get_status(), requester_my_requests(), _requester_owner()

### Community 74 - "search.py"
Cohesion: 0.47
Nodes (5): _conn(), global_search(), Global keyword search across requests, VNET info, subnet inventory and the…, Return {'requests': [...], 'vnets': [...], 'subnets': [...], 'audit': [...]}., _rows()

### Community 75 - "Environment recommendation template"
Cohesion: 0.40
Nodes (4): Environment recommendation template, Rendered shape, Rules specific to environment output, System prompt — environment composition

### Community 77 - "conversations.py"
Cohesion: 0.25
Nodes (21): _conn(), create_conversation(), delete_conversation(), _ensure_state_row(), ensure_tables(), get_conversation(), get_state(), list_conversations() (+13 more)

### Community 78 - "db_utils.py"
Cohesion: 0.17
Nodes (18): allocate_subnet(), Validate and allocate a subnet, writing the record to the DB., requester_vnet_created(), allocate_subnet_db(), _conn(), count_used_subnets_db(), create_spoke_request(), get_allocated_subnets_db() (+10 more)

### Community 79 - "_create_service_request"
Cohesion: 0.12
Nodes (19): _apply_submission_gate(), _available_teams(), _cached_vm_skus(), _create_service_request(), _create_vnet_request(), _fqdn_errors(), Request-scoped memoization of list_vm_skus (a 1000+ entry resourceSkus scan) —…, If the approval flow holds this request type at submission, open the gate (sets… (+11 more)

### Community 80 - "freeform.py"
Cohesion: 0.20
Nodes (16): get_platform_constants(), Shared, service-agnostic reference facts (naming pattern, DNS zones, encryption…, aks_private_zone_note(), answer(), _from_catalog(), _from_glossary(), _from_platform_constants(), _narrate() (+8 more)

### Community 81 - "evaluate_safe"
Cohesion: 0.20
Nodes (16): gate_fires(), build_waves(), critical_path(), parallelism_message(), Turns advisor_kb/composer/request_sequence.yaml into the ordered, wave-based…, Returns (submittable_request_type, secondary_note|None)., Ordered list of {wave, name, parallel, depends_on, requests: [...]} — only…, _request_included() (+8 more)

### Community 82 - "agent_requester.py"
Cohesion: 0.26
Nodes (15): chat(), _chat_anthropic(), _chat_openai(), _execute_tool(), _get_client(), Requester Agent — helps requesters submit CIDR requests, update statuses, and…, Create a VNET request via the same validated path as the form API., Create a non-VNET request via the same validated path as the form API. (+7 more)

### Community 83 - "diagram_builder.py"
Cohesion: 0.23
Nodes (14): _backend_label(), _engine_confirmed_uses_blob(), _escape(), _illustrative_base_name(), _placeholder_values(), Renders a pattern's Mermaid diagram template (advisor_kb/diagrams/*.mmd) by…, Diagram-only illustration, never the real prefilled vm_base_name field (which…, environment_full.mmd, for the whole-environment composer — NOT tied to a single… (+6 more)

### Community 84 - "recommendation.py"
Cohesion: 0.17
Nodes (13): build_recommendation(), build_recommendation_generic(), build_redirect_response(), _fallback_why_generic(), Assembles the final recommendation per…, Generic (service-agnostic) version of build_recommendation()'s requests-list…, Same shape and spirit as build_recommendation() (storage), generalized for…, A `redirect` escalation (currently only Postgres's self_managed ->… (+5 more)

### Community 85 - "auth_callback"
Cohesion: 0.13
Nodes (15): admin_login(), admin_logout(), _approvals_nav_ctx(), auth_callback(), auth_login(), auth_logout(), _home_endpoint(), inject_globals() (+7 more)

### Community 86 - "get_composer_file"
Cohesion: 0.22
Nodes (13): get_composer_file(), A composer/*.yaml file (e.g. infosec_gate.yaml), referenced by a decision…, _backend_description(), draft_brief(), _field_value(), _gate(), get_message_ref(), The InfoSec public-exposure gate: detection, verbatim message rendering, and a… (+5 more)

### Community 87 - "chats.py"
Cohesion: 0.38
Nodes (10): append_messages(), _conn(), create_chat(), delete_chat(), ensure_table(), list_chats(), _now(), Persistent agent chats — conversations survive across sessions and devices so… (+2 more)

### Community 88 - "glossary.py"
Cohesion: 0.36
Nodes (8): all_terms(), find_term(), _glossary(), _index(), _normalize(), Loads advisor_kb/glossary.yaml — the grounding source for "what does X mean?"…, normalized alias/term text -> term dict. Longer keys first isn't needed here…, Matches `query` against every term name and alias. Whole-phrase match on the…

### Community 89 - "_import_inventory"
Cohesion: 0.25
Nodes (8): admin_inventory(), _auto_migrate_excel(), _import_inventory(), Bulk-load current allocations. rows = [[subnet, purpose, requested_by,…, Post-deployment onboarding: the app ships with an EMPTY inventory — the admin…, One-time migration: if subnets.xlsx exists and subnet_records table is empty,…, get_pool_key(), Return the pool key ('10.110' / '10.119') for a subnet, or None.

### Community 91 - "env_prefill.py"
Cohesion: 0.47
Nodes (5): build_request_list(), _common_fields(), _known_fields_for(), Builds the ordered, prefilled request list for…, Flattens every wave's requests into one ordered list, each carrying {wave,…

### Community 92 - "2026-08-04"
Cohesion: 0.40
Nodes (4): 2026-08-04, Key Decisions, Open Items, Work Completed

### Community 94 - "test_advisor_validation.py"
Cohesion: 0.12
Nodes (28): get_catalog(), get_rules(), Process-wide cached catalog — static config, safe to cache per the module…, apply_set(), evaluate(), _quote_bare_enums(), Shared restricted-expression evaluator for the small condition language used…, Evaluate a KB condition string against `namespace` (field name -> value, values… (+20 more)

### Community 104 - "catalog_loader.py"
Cohesion: 0.21
Nodes (15): AdvisorKBError, get_questions(), load_catalog(), _load_yaml(), load_yaml_file(), Exception, Loads and validates the AI Architecture Advisor knowledge base (advisor_kb/).…, Pattern `design.inherits: <base_pattern_id>` (e.g. aks_gpu_nodepool inheriting… (+7 more)

### Community 109 - "prefill.py"
Cohesion: 0.17
Nodes (24): get_mapping(), AttrDict, Dict that also supports attribute access (derived.lifecycle_to_archive,…, build_prefill(), build_prefill_aks(), build_prefill_appgw(), build_prefill_postgres(), build_prefill_vm() (+16 more)

### Community 112 - "question_engine.py"
Cohesion: 0.19
Nodes (14): _coerce_value(), find_question(), is_complete(), next_question(), _questions_in_order(), Ask-order flow control for advisor_kb/questions/<service>_questions.yaml:…, Records a (already-normalized) answer, applying default_if_unknown, queuing any…, `service` may be None (only the service-selection question is findable before a… (+6 more)

### Community 123 - "pattern_matcher.py"
Cohesion: 0.39
Nodes (8): _is_disqualified(), _matches(), _passes_required(), _preferred_score(), Scores the catalog against captured answers, per advisor_kb/catalog/_schema.md:…, True if `value` (a single answer, or a list for multi_choice questions)…, Returns one of: {"outcome": "no_match", "winner": None, "candidates": []} --…, score()

### Community 133 - "AlMadar AI Architecture Advisor — Knowledge Base (Storage V1)"
Cohesion: 0.25
Nodes (7): AlMadar AI Architecture Advisor — Knowledge Base (Storage V1), Directory map, Extending beyond storage, Non-negotiables the advisor must never override, Provenance, Runtime flow, Wiring into AlMadar

### Community 134 - "Recommendation output template"
Cohesion: 0.25
Nodes (7): Blocked-path template, Recommendation output template, Rendered shape, System prompt — classification stage, System prompt — explanation stage, System prompt — question stage, Tone and content rules

### Community 135 - "2026-08-03"
Cohesion: 0.12
Nodes (15): 2026-08-03, Blockers, Blockers, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions, Files Changed, Files Changed (six-service expansion, on top of Session 1's advisor files), Next Steps (+7 more)

### Community 143 - "Diagram templates"
Cohesion: 0.29
Nodes (6): Adding a diagram for a new pattern, Diagram templates, Legend, Placeholders, Rendering, Rules

### Community 151 - "Catalog schema"
Cohesion: 0.40
Nodes (4): Catalog schema, `match` scoring, Rules that apply to every pattern, `status` semantics

## Knowledge Gaps
- **265 isolated node(s):** `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent`, `2026-07-30 — Line-manager approval routing, dependency-gated`, `2026-07-26 — Budget alerts are forecast-gated, not raw-threshold` (+260 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_name()` connect `admin_azure_action` to `azure_tools.py`, `_guard`, `app.py`, `route`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `sanitize()` connect `admin_azure_action` to `azure_tools.py`, `_guard`, `app.py`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Why does `RequestType` connect `RequestType` to `RequestProxy`, `models.py`, `app.py`, `db_utils.py`, `evaluate_safe`, `approvals.py`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent` to the rest of the system?**
  _265 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `_guard` be split into smaller, more focused modules?**
  _Cohesion score 0.05423728813559322 - nodes in this community are weakly interconnected._
- **Should `costmgmt.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10606060606060606 - nodes in this community are weakly interconnected._
- **Should `notifications.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08735150244584207 - nodes in this community are weakly interconnected._