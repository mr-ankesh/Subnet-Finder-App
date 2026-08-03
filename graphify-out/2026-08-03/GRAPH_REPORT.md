# Graph Report - Subnet-finder-app  (2026-08-03)

## Corpus Check
- 76 files · ~247,632 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1515 nodes · 3060 edges · 89 communities (84 shown, 5 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 12 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ce7874b6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- insert_returning_id
- _is_not_found
- costmgmt.py
- notifications.py
- add_firewall_network_rule
- reachability.py
- app.py
- route
- azure_tools.py
- require_admin
- _guard
- _get_credential
- render_name
- agent_admin.py
- budgetalerts.py
- optimize.py
- create_spoke_vnet
- _compute_client
- netdiag.py
- approvals.py
- api_advisor_chat
- require_subnet_access
- _chat_owner
- Expected output
- auth_oidc.py
- Architecture
- settings_store.py
- recommendation.py
- db_utils.py
- auth_callback
- 2026-08-01
- Approval
- RequestType
- RequestProxy
- _SkipMigration
- resourcegraph.py
- brand.js
- _import_inventory
- record
- zpa-networkuser-wrapper.sh
- Keycloak (OIDC) Integration Guide
- deploy.sh
- require_itadmin
- SpokeRequest
- Architecture Decisions
- can_decide
- changes.py
- Migration notes — adding services to the existing advisor
- Azure access required by AlMadar 360
- How Network Copilot Works — High-Level Architecture
- Current State
- Project Overview
- test_storage_validation.py
- request_terminate
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
- datetime
- README.md
- _containerservice_client
- subinventory.py
- 2026-08-02
- audit.py
- _Conn
- request_detail
- search.py
- Environment recommendation template
- api_budget_email_preview
- rules_engine.py
- diagram_builder.py
- catalog_loader.py
- prefill.py
- test_advisor_validation.py
- pattern_matcher.py
- AlMadar AI Architecture Advisor — Knowledge Base (Storage V1)
- Recommendation output template
- 2026-08-03
- Diagram templates
- Catalog schema

## God Nodes (most connected - your core abstractions)
1. `_guard()` - 45 edges
2. `record()` - 43 edges
3. `_network_client()` - 40 edges
4. `require_login()` - 39 edges
5. `_is_not_found()` - 35 edges
6. `require_admin()` - 34 edges
7. `require_superadmin()` - 33 edges
8. `current_actor()` - 27 edges
9. `2026-08-01` - 27 edges
10. `2026-08-02` - 24 edges

## Surprising Connections (you probably didn't know these)
- `api_advisor_chat()` --calls--> `build_blocked_response()`  [EXTRACTED]
  app.py → advisor/recommendation.py
- `_SkipMigration` --uses--> `Approval`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `RequestStatus`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `RequestType`  [INFERRED]
  app.py → models.py
- `_SkipMigration` --uses--> `SpokeRequest`  [INFERRED]
  app.py → models.py

## Import Cycles
- None detected.

## Communities (89 total, 5 thin omitted)

### Community 0 - "insert_returning_id"
Cohesion: 0.20
Nodes (7): _EmptyCursor, insert_returning_id(), _norm(), INSERT that returns the new row id on both backends., Normalise a cell so raw callers see the SQLite-era shape (str timestamps)., Eager, backend-neutral result: dict rows materialised at fetch time., _Result

### Community 1 - "_is_not_found"
Cohesion: 0.10
Nodes (32): check_private_dns_zone(), create_dns_zone_in_hub(), decommission_check(), delete_dns_record(), delete_dns_zone(), delete_dns_zone_link(), _dns_record_dict(), get_dns_record_status() (+24 more)

### Community 2 - "costmgmt.py"
Cohesion: 0.11
Nodes (32): _cols(), _compute_summary(), configured(), cost_by_dimension(), cost_by_resource(), cost_daily(), _descendants(), _headers() (+24 more)

### Community 3 - "notifications.py"
Cohesion: 0.08
Nodes (58): admin_azure_action(), _auto_advance(), _done_actions(), _pending_deploy_actions(), For a 'required @ trigger' request type, block an Azure deploy until the line…, Run a single Azure onboarding action for a request: vnet -> create the spoke…, Portal actions that have succeeded for this request (dry-run included), minus…, Portal actions that must succeed before the request auto-completes. (+50 more)

### Community 4 - "add_firewall_network_rule"
Cohesion: 0.10
Nodes (27): add_firewall_application_rule(), add_firewall_network_rule(), allow_internet_rule(), _build_fw_rule(), _collection_kind_conflict(), _describe_fw_rule(), find_firewall_rules_for_address(), find_firewall_rules_for_pair() (+19 more)

### Community 5 - "reachability.py"
Cohesion: 0.10
Nodes (38): _classify(), configured(), _cpu_line(), health_all(), _is_ip(), _load_key(), _net_ifaces(), _num() (+30 more)

### Community 6 - "app.py"
Cohesion: 0.08
Nodes (45): advisor_page(), allocator(), api_approvals_pending(), api_request_approvals(), approvals_page(), azure_aks_options(), azure_disk_skus(), azure_regions() (+37 more)

### Community 7 - "route"
Cohesion: 0.09
Nodes (38): admin_approvals_health(), admin_audit(), admin_settings(), admin_settings_preview_name(), admin_settings_test_azure(), admin_settings_test_connector(), admin_settings_test_cost(), admin_settings_test_keycloak() (+30 more)

### Community 8 - "azure_tools.py"
Cohesion: 0.08
Nodes (33): add_cidr_to_firewall_rule(), _addr_covers(), aks_tiers(), _analyze_coverage(), assign_vm_zones(), build_vm_plan(), derive_vm_resource_names(), derive_windows_computer_name() (+25 more)

### Community 9 - "require_admin"
Cohesion: 0.08
Nodes (24): admin_assign_cidr_api(), admin_changes(), admin_deallocate_api(), admin_find_subnets_api(), admin_firewall_lookup(), admin_inventory(), admin_list_requests_api(), admin_search() (+16 more)

### Community 10 - "_guard"
Cohesion: 0.06
Nodes (41): add_cidr_to_nsg_rule(), add_route_to_table(), add_udr_routes(), assign_route_table_to_subnet(), check_udr(), delete_route_from_table(), delete_spoke_route_table(), delete_spoke_vnet() (+33 more)

### Community 11 - "_get_credential"
Cohesion: 0.09
Nodes (26): aks_source_subnet(), _diag_subs(), _get_credential(), _is_cidr(), list_keyvault_keys(), list_keyvaults(), list_locations(), list_subscriptions() (+18 more)

### Community 12 - "render_name"
Cohesion: 0.18
Nodes (13): _deploy_one(), Run one VNET deploy step with server-derived defaults (aggregated deploy)., delete_hub_spoke_peerings(), peer_hub_vnet(), Delete both peering directions (spoke→hub and hub→spoke)., Creates VNET peering in both directions (spoke→hub, hub→spoke). If peering…, Resource-name rendering from the admin-configurable naming templates. Templates…, Lowercase, alnum + dash — safe inside an Azure resource name. (+5 more)

### Community 13 - "agent_admin.py"
Cohesion: 0.06
Nodes (67): _actor(), build_system_prompt(), chat(), _chat_anthropic(), _chat_openai(), _compute_free(), _execute_tool(), _get_client() (+59 more)

### Community 14 - "budgetalerts.py"
Cohesion: 0.23
Nodes (13): assess(), _conn(), ensure_table(), evaluate_and_send(), _last_severity(), Automatic over-budget alerts for subscriptions. The hard part isn't emailing at…, Check every opted-in subscription and email escalations. Returns a report:…, Start the periodic budget checker as a daemon thread (idempotent). Runs only… (+5 more)

### Community 15 - "optimize.py"
Cohesion: 0.14
Nodes (24): _arg(), configured(), _disk_month(), _f(), _headers(), list_subscriptions(), _metric_stats(), _pip_month() (+16 more)

### Community 16 - "create_spoke_vnet"
Cohesion: 0.07
Nodes (32): carve_subnets(), check_storage_name_availability(), create_aks_disk_encryption(), create_object_replication_policy(), create_route_table(), create_spoke_vnet(), create_storage_account(), create_storage_private_endpoint() (+24 more)

### Community 17 - "_compute_client"
Cohesion: 0.08
Nodes (28): check_vm_quota(), _compute_client(), delete_vm(), _extract_taken_indexes(), list_aks_versions(), list_existing_vm_indexes(), list_marketplace_images(), list_vm_images() (+20 more)

### Community 18 - "netdiag.py"
Cohesion: 0.14
Nodes (21): _addr_in(), _clean_llm(), diagnose(), _has_cjk(), _hub_fw_ip(), _is_ip(), _is_private(), _is_private_domain() (+13 more)

### Community 19 - "approvals.py"
Cohesion: 0.15
Nodes (19): enabled(), has_valid_trigger_approval(), manager_seen(), _mgr_state(), needs_trigger_approval(), open_submission_gate(), policy_for(), preflight() (+11 more)

### Community 20 - "api_advisor_chat"
Cohesion: 0.25
Nodes (17): get_catalog(), Process-wide cached catalog — static config, safe to cache per the module…, detect_public_access_request(), _conn(), create_session(), ensure_table(), get_session(), _now() (+9 more)

### Community 21 - "require_subnet_access"
Cohesion: 0.18
Nodes (19): all_available(), allocate(), allocate_subnet(), available_base_route(), candidates_from_free(), compute_free_blocks(), free_summary(), get_pool_from_request() (+11 more)

### Community 22 - "_chat_owner"
Cohesion: 0.20
Nodes (22): agent_chat(), agent_chat_delete(), agent_chat_get(), agent_chats_list(), _chat_owner(), Stable per-user key that owns persistent agent chats. Uses the Keycloak…, requester_chat(), requester_chat_delete() (+14 more)

### Community 23 - "Expected output"
Cohesion: 0.12
Nodes (16): Before you start, Build sequence, Components, Expected output, Hub integration, Input, Negative test — must also pass, Network plan (+8 more)

### Community 24 - "auth_oidc.py"
Cohesion: 0.13
Nodes (18): client(), _decode_jwt_payload(), end_session_url(), groups_from_token(), init_oidc(), manager_from_token(), _metadata_url(), Keycloak (OIDC) integration — Authlib. Kept deliberately thin: the OIDC… (+10 more)

### Community 25 - "Architecture"
Cohesion: 0.07
Nodes (26): AI agents are tool-callers, not free-form SQL/Azure access, AI Architecture Advisor: rules decide, LLM explains — not a `RequestType`, Approval flow: relationship-based routing with a dependency gate, Architecture, Auth: local password or Keycloak SSO, switched live, Azure changes: imperative SDK calls, not IaC, Budget alerts: forecast-gated, not raw-threshold, CIDR pool allocator (the app's namesake feature) (+18 more)

### Community 26 - "settings_store.py"
Cohesion: 0.23
Nodes (16): all_overrides(), _conn(), _decrypt(), delete_override(), _encrypt(), ensure_table(), _fernet(), get_override() (+8 more)

### Community 27 - "recommendation.py"
Cohesion: 0.09
Nodes (27): load_text_file(), call_llm(), _get_client(), get_recommendation_template(), get_system_prompts(), Loads the 3 system prompts verbatim from…, {"classification": "...", "explanation": "...", "question": "..."}, One completion, no tools, no loop. Raises on failure — callers (the advisor… (+19 more)

### Community 28 - "db_utils.py"
Cohesion: 0.19
Nodes (15): allocated(), allocate_subnet_db(), _conn(), count_used_subnets_db(), deallocate_subnet_db(), get_allocated_subnets_db(), get_used_subnets_db(), get_vnet_info() (+7 more)

### Community 29 - "auth_callback"
Cohesion: 0.13
Nodes (15): admin_login(), admin_logout(), _approvals_nav_ctx(), auth_callback(), auth_login(), auth_logout(), _home_endpoint(), inject_globals() (+7 more)

### Community 30 - "2026-08-01"
Cohesion: 0.07
Nodes (27): 2026-08-01, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions (+19 more)

### Community 31 - "Approval"
Cohesion: 0.19
Nodes (12): decide(), open_trigger_gate(), Pick the approver for a new checkpoint. The requester's line manager if we have…, Recompute the cached approval_state on the request from its checkpoints., Admin-initiated: send a specific request for approval (discretion mode)., Raise a pending trigger checkpoint (called when a blocked deploy is attempted)., Record an approve/reject decision and reconcile the request status., request_discretion_approval() (+4 more)

### Community 32 - "RequestType"
Cohesion: 0.20
Nodes (4): policy_matrix(), View model for the settings matrix: one row per request type., Request kinds available in the requester portal, each with its own workflow., RequestType

### Community 33 - "RequestProxy"
Cohesion: 0.25
Nodes (3): Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field., RequestProxy, RequestStatus

### Community 34 - "_SkipMigration"
Cohesion: 0.22
Nodes (6): Exception, Sentinel to short-circuit the SQLite-only column backfill on Postgres., _SkipMigration, FwCollection, Admin-defined firewall rule collection group / rule collection pairs (one-time…, VnetInfo

### Community 35 - "resourcegraph.py"
Cohesion: 0.06
Nodes (48): _coerce(), Config, Central config — every value resolves live as: DB override → env var → default.…, Attribute access resolves live: DB override → env → default., _add_edge(), _arg(), build_graph(), _category_for_type() (+40 more)

### Community 37 - "_import_inventory"
Cohesion: 0.25
Nodes (8): _auto_migrate_excel(), _import_inventory(), Bulk-load current allocations. rows = [[subnet, purpose, requested_by,…, One-time migration: if subnets.xlsx exists and subnet_records table is empty,…, get_pool_key(), Return the pool key ('10.110' / '10.119') for a subnet, or None., Persistent record of every allocated (used) or reserved subnet. Free space is…, SubnetRecord

### Community 38 - "record"
Cohesion: 0.09
Nodes (31): admin_change_revert(), admin_settings_reset(), admin_settings_save(), admin_vm_preview(), api_approval_decide(), api_budget_email_send(), api_request_send_approval(), api_subscription_inventory_save() (+23 more)

### Community 39 - "zpa-networkuser-wrapper.sh"
Cohesion: 0.83
Nodes (3): _allow(), _deny(), zpa-networkuser-wrapper.sh script

### Community 40 - "Keycloak (OIDC) Integration Guide"
Cohesion: 0.10
Nodes (19): 3a. OIDC client registration (new `auth_oidc.py`), 3b. Routes (in app.py), 3c. Switch on `AUTH_PROVIDER`, 3d. Audit actor, How a user's team is determined, How it behaves once active, Keycloak (OIDC) Integration Guide, Keycloak-side prerequisite: the `groups` claim (+11 more)

### Community 42 - "require_itadmin"
Cohesion: 0.25
Nodes (8): it_connector_health(), it_connector_status(), it_reachability(), it_reachability_run(), Health dashboard: are the connector VMs (primary + secondary) up?, Richer per-VM diagnostics for the dashboard's 'More status'., Guards the Reachability Tester — IT-admins OR super-admins. Open when SSO is…, require_itadmin()

### Community 44 - "Architecture Decisions"
Cohesion: 0.08
Nodes (23): 2026-03-26 — AI agents are tool-callers only, never raw SQL/Azure access, 2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent, 2026-07-15 → 2026-07-29 — Rebrand: Subnet Manager → Network Copilot → Presight AlMadar 360, 2026-07-18 — PostgreSQL as an optional backend, SQLite stays default, 2026-07-26 — Budget alerts are forecast-gated, not raw-threshold, 2026-07-30 — Line-manager approval routing, dependency-gated, 2026-07-31 (approx) — Password never persisted for VM auth, 2026-07-31 — VM(s) deployment: plan resolved once, persisted, resumable (+15 more)

### Community 45 - "can_decide"
Cohesion: 0.50
Nodes (4): can_decide(), pending_for(), Is this actor allowed to approve/reject this checkpoint?, Approvals awaiting this actor's decision (their reports' requests, plus…

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
Cohesion: 0.17
Nodes (11): Active Priorities, AI Architecture Advisor — storage-only V1, then expanded to six services, Current Blockers, Current State, Development Status, Features Completed (committed, on `main`), Features In Progress, Pending Work (+3 more)

### Community 51 - "Project Overview"
Cohesion: 0.22
Nodes (8): High-Level Architecture, Important Technologies, Important Workflows, Key Capabilities, Main Modules, Project Overview, Purpose, See Also

### Community 52 - "test_storage_validation.py"
Cohesion: 0.39
Nodes (7): Validation for a Storage Account request at submission time. Offline checks…, _validate_storage_request(), base_details(), check(), main(), Assert-based coverage of Storage Account request validation…, run_validate()

### Community 53 - "request_terminate"
Cohesion: 0.22
Nodes (10): _deployed_changes(), What has actually been deployed for this request, derived from the audit trail:…, Undo one deployed change. Returns the azure_tools-style result dict., Undo a list of deployed changes in the given (Azure-dependency-safe) order.…, Aggregated revert for a VNET request: tear down EVERYTHING deployed for it —…, Cancel or reject a request, reverting every deployed Azure change first (unless…, request_revert_deployment(), request_terminate() (+2 more)

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

### Community 66 - "datetime"
Cohesion: 0.20
Nodes (10): datetime, ensure_table(), get_pool_key(), One-time migration script: imports subnet inventory from subnets.xlsx into the…, run(), AppSetting, AuditLog, SQLAlchemy models for Spoke Request workflow and subnet inventory. (+2 more)

### Community 68 - "_containerservice_client"
Cohesion: 0.22
Nodes (10): _aks_dns_prefix(), _aks_pool_summary(), _containerservice_client(), create_aks_cluster(), delete_aks_cluster(), get_aks_cluster_status(), A valid dnsPrefix: alphanumerics/hyphens, start+end alphanumeric, ≤ 54 chars., Read-only: does the cluster exist and what is its provisioningState? Runs for… (+2 more)

### Community 69 - "subinventory.py"
Cohesion: 0.36
Nodes (9): all_records(), _conn(), ensure_table(), Subscription inventory — the manually-owned metadata that Azure doesn't hold:…, All stored inventory rows keyed by subscription id., Create or update the owner/budget metadata for a subscription., Toggle scheduled over-budget alerts for one subscription (updates only that…, set_auto_alerts() (+1 more)

### Community 70 - "2026-08-02"
Cohesion: 0.08
Nodes (24): 2026-08-02, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Design Decisions, Design Decisions (+16 more)

### Community 71 - "audit.py"
Cohesion: 0.43
Nodes (7): _conn(), distinct_actions(), ensure_table(), list_entries(), Audit trail — durable record of who did what, when, on which request. Raw…, Latest-first audit entries with optional filters., Distinct action slugs, for the filter dropdown.

### Community 73 - "request_detail"
Cohesion: 0.29
Nodes (7): _deploy_spoke_route_table(), _deploy_tags(), Mandatory resource tags applied to everything deployed for a request: owner —…, Parse the SPOKE_DEFAULT_ROUTES setting: 'name=prefix, name=prefix, …'., Create the spoke route table (default + hub-firewall routes) and assign it to…, request_detail(), _spoke_default_routes()

### Community 74 - "search.py"
Cohesion: 0.47
Nodes (5): _conn(), global_search(), Global keyword search across requests, VNET info, subnet inventory and the…, Return {'requests': [...], 'vnets': [...], 'subnets': [...], 'audit': [...]}., _rows()

### Community 75 - "Environment recommendation template"
Cohesion: 0.40
Nodes (4): Environment recommendation template, Rendered shape, Rules specific to environment output, System prompt — environment composition

### Community 76 - "api_budget_email_preview"
Cohesion: 0.50
Nodes (4): api_budget_email_preview(), Assemble {id, name, spend, inventory} for one subscription — the stored…, Draft (do NOT send) the over-budget email to the financial owner, so the admin…, _sub_for_budget()

### Community 94 - "rules_engine.py"
Cohesion: 0.12
Nodes (29): get_composer_file(), get_rules(), A composer/*.yaml file (e.g. infosec_gate.yaml), referenced by a decision…, apply_set(), evaluate(), evaluate_safe(), _quote_bare_enums(), Shared restricted-expression evaluator for the small condition language used… (+21 more)

### Community 96 - "diagram_builder.py"
Cohesion: 0.29
Nodes (11): _backend_label(), _engine_confirmed_uses_blob(), _escape(), _illustrative_base_name(), _placeholder_values(), Renders a pattern's Mermaid diagram template (advisor_kb/diagrams/*.mmd) by…, Diagram-only illustration, never the real prefilled vm_base_name field (which…, render() (+3 more)

### Community 104 - "catalog_loader.py"
Cohesion: 0.19
Nodes (17): AdvisorKBError, get_platform_constants(), get_questions(), load_catalog(), _load_yaml(), load_yaml_file(), Exception, Loads and validates the AI Architecture Advisor knowledge base (advisor_kb/).… (+9 more)

### Community 109 - "prefill.py"
Cohesion: 0.17
Nodes (24): get_mapping(), AttrDict, Dict that also supports attribute access (derived.lifecycle_to_archive,…, build_prefill(), build_prefill_aks(), build_prefill_appgw(), build_prefill_postgres(), build_prefill_vm() (+16 more)

### Community 112 - "test_advisor_validation.py"
Cohesion: 0.16
Nodes (18): _coerce_value(), find_question(), is_complete(), next_question(), _normalize_options(), _normalize_question(), _normalize_skip_if(), _questions_in_order() (+10 more)

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
Cohesion: 0.15
Nodes (12): 2026-08-03, Blockers, Blockers, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions, Files Changed, Files Changed (six-service expansion, on top of Session 1's advisor files), Next Steps (+4 more)

### Community 143 - "Diagram templates"
Cohesion: 0.29
Nodes (6): Adding a diagram for a new pattern, Diagram templates, Legend, Placeholders, Rendering, Rules

### Community 151 - "Catalog schema"
Cohesion: 0.40
Nodes (4): Catalog schema, `match` scoring, Rules that apply to every pattern, `status` semantics

## Knowledge Gaps
- **248 isolated node(s):** `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent`, `2026-07-30 — Line-manager approval routing, dependency-gated`, `2026-07-26 — Budget alerts are forecast-gated, not raw-threshold` (+243 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_name()` connect `render_name` to `notifications.py`, `app.py`, `route`, `azure_tools.py`, `request_detail`, `request_terminate`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Why does `sanitize()` connect `render_name` to `_is_not_found`, `notifications.py`, `app.py`, `azure_tools.py`, `request_detail`, `request_terminate`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Why does `SpokeRequest` connect `SpokeRequest` to `RequestType`, `RequestProxy`, `_SkipMigration`, `datetime`, `app.py`, `approvals.py`, `Approval`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent` to the rest of the system?**
  _248 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `_is_not_found` be split into smaller, more focused modules?**
  _Cohesion score 0.0967741935483871 - nodes in this community are weakly interconnected._
- **Should `costmgmt.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10606060606060606 - nodes in this community are weakly interconnected._
- **Should `notifications.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08065458796025717 - nodes in this community are weakly interconnected._