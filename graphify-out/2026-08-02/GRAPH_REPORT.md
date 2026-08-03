# Graph Report - Subnet-finder-app  (2026-08-02)

## Corpus Check
- 55 files · ~194,911 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1240 nodes · 2549 edges · 72 communities (67 shown, 5 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `4d9add5c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- insert_returning_id
- _guard
- costmgmt.py
- notifications.py
- add_firewall_network_rule
- reachability.py
- route
- record
- azure_tools.py
- require_login
- _network_client
- app.py
- admin_azure_action
- agent_admin.py
- datetime
- optimize.py
- create_spoke_vnet
- _compute_client
- netdiag.py
- approvals.py
- diagnose
- require_subnet_access
- chats.py
- models.py
- auth_oidc.py
- Architecture
- settings_store.py
- changes.py
- auth_callback
- SpokeRequest
- 2026-08-01
- RequestType
- RequestProxy
- require_itadmin
- requester_get_status
- resourcegraph.py
- brand.js
- _create_service_request
- Approval
- zpa-networkuser-wrapper.sh
- Keycloak (OIDC) Integration Guide
- deploy.sh
- list_existing_vm_indexes
- _get_credential
- Architecture Decisions
- db_backend.py
- subinventory.py
- audit.py
- Azure access required by AlMadar 360
- How Network Copilot Works — High-Level Architecture
- Current State
- Project Overview
- test_storage_validation.py
- _Conn
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
- search.py
- Known Issues
- migrate_excel_to_db.py
- README.md
- db_utils.py
- agent_requester.py
- 2026-08-02
- update_spoke_request

## God Nodes (most connected - your core abstractions)
1. `_guard()` - 45 edges
2. `record()` - 43 edges
3. `_network_client()` - 40 edges
4. `require_login()` - 35 edges
5. `_is_not_found()` - 35 edges
6. `require_admin()` - 34 edges
7. `require_superadmin()` - 33 edges
8. `current_actor()` - 27 edges
9. `2026-08-01` - 27 edges
10. `admin_azure_action()` - 22 edges

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

## Communities (72 total, 5 thin omitted)

### Community 0 - "insert_returning_id"
Cohesion: 0.20
Nodes (7): _EmptyCursor, insert_returning_id(), _norm(), INSERT that returns the new row id on both backends., Normalise a cell so raw callers see the SQLite-era shape (str timestamps)., Eager, backend-neutral result: dict rows materialised at fetch time., _Result

### Community 1 - "_guard"
Cohesion: 0.07
Nodes (45): add_cidr_to_firewall_rule(), add_route_to_table(), create_dns_zone_in_hub(), decommission_check(), delete_dns_record(), delete_dns_zone(), delete_dns_zone_link(), delete_hub_spoke_peerings() (+37 more)

### Community 2 - "costmgmt.py"
Cohesion: 0.11
Nodes (32): _cols(), _compute_summary(), configured(), cost_by_dimension(), cost_by_resource(), cost_daily(), _descendants(), _headers() (+24 more)

### Community 3 - "notifications.py"
Cohesion: 0.11
Nodes (44): _adaptive_card(), _compose_email(), draft_budget_alert(), _draft_email(), draft_threshold_alert(), _email_case(), _email_requester(), _looks_unusable() (+36 more)

### Community 4 - "add_firewall_network_rule"
Cohesion: 0.10
Nodes (27): add_firewall_application_rule(), add_firewall_network_rule(), allow_internet_rule(), _build_fw_rule(), _collection_kind_conflict(), _describe_fw_rule(), find_firewall_rules_for_address(), find_firewall_rules_for_pair() (+19 more)

### Community 5 - "reachability.py"
Cohesion: 0.06
Nodes (47): _coerce(), Config, Central config — every value resolves live as: DB override → env var → default.…, Attribute access resolves live: DB override → env → default., _classify(), configured(), _cpu_line(), health_all() (+39 more)

### Community 6 - "route"
Cohesion: 0.08
Nodes (43): admin_approvals_health(), admin_settings(), admin_settings_preview_name(), admin_settings_test_azure(), admin_settings_test_connector(), admin_settings_test_cost(), admin_settings_test_keycloak(), admin_settings_test_optimize() (+35 more)

### Community 7 - "record"
Cohesion: 0.08
Nodes (35): admin_settings_reset(), admin_settings_save(), api_approval_decide(), api_request_send_approval(), current_actor(), _deployed_changes(), _import_inventory(), Bulk-load current allocations. rows = [[subnet, purpose, requested_by,… (+27 more)

### Community 8 - "azure_tools.py"
Cohesion: 0.09
Nodes (31): _addr_covers(), _aks_dns_prefix(), _aks_pool_summary(), aks_tiers(), _analyze_coverage(), _containerservice_client(), create_aks_cluster(), delete_aks_cluster() (+23 more)

### Community 9 - "require_login"
Cohesion: 0.07
Nodes (35): api_approvals_pending(), api_request_approvals(), approvals_page(), azure_aks_options(), azure_disk_skus(), azure_regions(), azure_storage_identities(), azure_storage_keys() (+27 more)

### Community 10 - "_network_client"
Cohesion: 0.08
Nodes (27): add_cidr_to_nsg_rule(), add_udr_routes(), assign_route_table_to_subnet(), check_udr(), get_nsg_rule_status(), list_subnets(), list_vnet_subnets(), list_vnets() (+19 more)

### Community 11 - "app.py"
Cohesion: 0.12
Nodes (28): admin_assign_cidr_api(), admin_change_revert(), admin_changes(), admin_deallocate_api(), admin_find_subnets_api(), admin_firewall_lookup(), admin_inventory(), admin_list_requests_api() (+20 more)

### Community 12 - "admin_azure_action"
Cohesion: 0.09
Nodes (32): admin_azure_action(), _auto_advance(), _deploy_one(), _deploy_spoke_route_table(), _deploy_tags(), _done_actions(), _pending_deploy_actions(), Run a single Azure onboarding action for a request: vnet -> create the spoke… (+24 more)

### Community 13 - "agent_admin.py"
Cohesion: 0.14
Nodes (27): _actor(), build_system_prompt(), chat(), _chat_anthropic(), _chat_openai(), _compute_free(), _execute_tool(), _get_client() (+19 more)

### Community 14 - "datetime"
Cohesion: 0.22
Nodes (14): assess(), _conn(), ensure_table(), evaluate_and_send(), _last_severity(), Automatic over-budget alerts for subscriptions. The hard part isn't emailing at…, Check every opted-in subscription and email escalations. Returns a report:…, Start the periodic budget checker as a daemon thread (idempotent). Runs only… (+6 more)

### Community 15 - "optimize.py"
Cohesion: 0.14
Nodes (24): _arg(), configured(), _disk_month(), _f(), _headers(), list_subscriptions(), _metric_stats(), _pip_month() (+16 more)

### Community 16 - "create_spoke_vnet"
Cohesion: 0.10
Nodes (23): carve_subnets(), create_aks_disk_encryption(), create_route_table(), create_spoke_vnet(), create_storage_account(), create_storage_private_endpoint(), create_vm(), ensure_resource_group() (+15 more)

### Community 17 - "_compute_client"
Cohesion: 0.09
Nodes (24): check_vm_quota(), _compute_client(), delete_vm(), list_aks_versions(), list_marketplace_images(), list_vm_images(), list_vm_sizes(), list_vm_skus() (+16 more)

### Community 18 - "netdiag.py"
Cohesion: 0.11
Nodes (20): _addr_in(), _clean_llm(), _has_cjk(), _hub_fw_ip(), _is_ip(), _is_private(), _is_private_domain(), _live() (+12 more)

### Community 19 - "approvals.py"
Cohesion: 0.13
Nodes (21): enabled(), has_valid_trigger_approval(), manager_seen(), _mgr_state(), needs_trigger_approval(), open_submission_gate(), policy_for(), policy_matrix() (+13 more)

### Community 20 - "diagnose"
Cohesion: 0.10
Nodes (26): aks_source_subnet(), check_private_dns_zone(), _diag_subs(), _hub_vnet_id(), _is_cidr(), link_aks_private_dns_to_hub(), list_keyvaults(), locate_ip() (+18 more)

### Community 21 - "require_subnet_access"
Cohesion: 0.17
Nodes (20): all_available(), allocate(), allocated(), allocator(), available_base_route(), candidates_from_free(), compute_free_blocks(), deallocate() (+12 more)

### Community 22 - "chats.py"
Cohesion: 0.20
Nodes (22): agent_chat(), agent_chat_delete(), agent_chat_get(), agent_chats_list(), _chat_owner(), Stable per-user key that owns persistent agent chats. Uses the Keycloak…, requester_chat(), requester_chat_delete() (+14 more)

### Community 23 - "models.py"
Cohesion: 0.10
Nodes (17): _auto_migrate_excel(), Sentinel to short-circuit the SQLite-only column backfill on Postgres., One-time migration: if subnets.xlsx exists and subnet_records table is empty,…, _SkipMigration, get_pool_key(), Return the pool key ('10.110' / '10.119') for a subnet, or None., Exception, AppSetting (+9 more)

### Community 24 - "auth_oidc.py"
Cohesion: 0.13
Nodes (18): client(), _decode_jwt_payload(), end_session_url(), groups_from_token(), init_oidc(), manager_from_token(), _metadata_url(), Keycloak (OIDC) integration — Authlib. Kept deliberately thin: the OIDC… (+10 more)

### Community 25 - "Architecture"
Cohesion: 0.08
Nodes (24): AI agents are tool-callers, not free-form SQL/Azure access, Approval flow: relationship-based routing with a dependency gate, Architecture, Auth: local password or Keycloak SSO, switched live, Azure changes: imperative SDK calls, not IaC, Budget alerts: forecast-gated, not raw-threshold, CIDR pool allocator (the app's namesake feature), Commands (+16 more)

### Community 26 - "settings_store.py"
Cohesion: 0.23
Nodes (16): all_overrides(), _conn(), _decrypt(), delete_override(), _encrypt(), ensure_table(), _fernet(), get_override() (+8 more)

### Community 27 - "changes.py"
Cohesion: 0.33
Nodes (12): _conn(), ensure_table(), execute_revert(), get_change(), list_changes(), _mark(), Change ledger — the platform's undo history. Every mutating operation records…, Restore the earlier state recorded in change #cid. Reason is mandatory and… (+4 more)

### Community 28 - "auth_callback"
Cohesion: 0.13
Nodes (15): admin_login(), admin_logout(), _approvals_nav_ctx(), auth_callback(), auth_login(), auth_logout(), _home_endpoint(), inject_globals() (+7 more)

### Community 29 - "SpokeRequest"
Cohesion: 0.19
Nodes (9): open_trigger_gate(), Pick the approver for a new checkpoint. The requester's line manager if we have…, Recompute the cached approval_state on the request from its checkpoints., Admin-initiated: send a specific request for approval (discretion mode)., Raise a pending trigger checkpoint (called when a blocked deploy is attempted)., request_discretion_approval(), resolve_approver(), _sync_request_state() (+1 more)

### Community 30 - "2026-08-01"
Cohesion: 0.07
Nodes (27): 2026-08-01, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions (+19 more)

### Community 32 - "RequestProxy"
Cohesion: 0.25
Nodes (3): Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field., RequestProxy, RequestStatus

### Community 33 - "require_itadmin"
Cohesion: 0.25
Nodes (8): it_connector_health(), it_connector_status(), it_reachability(), it_reachability_run(), Health dashboard: are the connector VMs (primary + secondary) up?, Richer per-VM diagnostics for the dashboard's 'More status'., Guards the Reachability Tester — IT-admins OR super-admins. Open when SSO is…, require_itadmin()

### Community 34 - "requester_get_status"
Cohesion: 0.29
Nodes (7): _owns_request(), The signed-in requester's own requests (SSO only). Empty in open mode., (name, email) identifying the signed-in requester's requests, or None when SSO…, True if the current user may view this request. Admins: all. SSO requesters:…, requester_get_status(), requester_my_requests(), _requester_owner()

### Community 35 - "resourcegraph.py"
Cohesion: 0.10
Nodes (37): _add_edge(), _arg(), build_graph(), _category_for_type(), configured(), _credential(), _expand_aks_node_rg(), _expand_pe_dns_zone_group() (+29 more)

### Community 37 - "_create_service_request"
Cohesion: 0.11
Nodes (22): _apply_submission_gate(), _available_teams(), _cached_vm_skus(), _create_service_request(), _create_vnet_request(), _fqdn_errors(), _fw_params(), Request-scoped memoization of list_vm_skus (a 1000+ entry resourceSkus scan) —… (+14 more)

### Community 38 - "Approval"
Cohesion: 0.22
Nodes (8): can_decide(), decide(), pending_for(), Is this actor allowed to approve/reject this checkpoint?, Record an approve/reject decision and reconcile the request status., Approvals awaiting this actor's decision (their reports' requests, plus…, Approval, A single approval checkpoint on a request — routed to the requester's line…

### Community 39 - "zpa-networkuser-wrapper.sh"
Cohesion: 0.83
Nodes (3): _allow(), _deny(), zpa-networkuser-wrapper.sh script

### Community 40 - "Keycloak (OIDC) Integration Guide"
Cohesion: 0.10
Nodes (19): 3a. OIDC client registration (new `auth_oidc.py`), 3b. Routes (in app.py), 3c. Switch on `AUTH_PROVIDER`, 3d. Audit actor, How a user's team is determined, How it behaves once active, Keycloak (OIDC) Integration Guide, Keycloak-side prerequisite: the `groups` claim (+11 more)

### Community 42 - "list_existing_vm_indexes"
Cohesion: 0.17
Nodes (12): assign_vm_zones(), build_vm_plan(), derive_vm_resource_names(), derive_windows_computer_name(), _extract_taken_indexes(), list_existing_vm_indexes(), Windows osProfile.computerName from a resolved VM RESOURCE name (base + -NNN…, NIC/OS-disk names for one resolved VM name (data disk names are numbered per-VM… (+4 more)

### Community 43 - "_get_credential"
Cohesion: 0.11
Nodes (19): check_storage_name_availability(), create_object_replication_policy(), delete_storage_account(), _get_credential(), list_keyvault_keys(), list_locations(), list_storage_skus(), list_subscriptions() (+11 more)

### Community 44 - "Architecture Decisions"
Cohesion: 0.12
Nodes (16): 2026-03-26 — AI agents are tool-callers only, never raw SQL/Azure access, 2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent, 2026-07-15 → 2026-07-29 — Rebrand: Subnet Manager → Network Copilot → Presight AlMadar 360, 2026-07-18 — PostgreSQL as an optional backend, SQLite stays default, 2026-07-26 — Budget alerts are forecast-gated, not raw-threshold, 2026-07-30 — Line-manager approval routing, dependency-gated, 2026-07-31 (approx) — Password never persisted for VM auth, 2026-07-31 — VM(s) deployment: plan resolved once, persisted, resumable (+8 more)

### Community 45 - "db_backend.py"
Cohesion: 0.25
Nodes (9): connect(), _database_url(), Backend-agnostic database access for the raw-SQL modules (db_utils, audit,…, A wrapped connection. `with connect() as conn: conn.execute(...)`., URI for Flask-SQLAlchemy — Postgres (psycopg3 driver) or the SQLite file., URI with any password masked — for /health output., safe_uri(), sqlalchemy_uri() (+1 more)

### Community 46 - "subinventory.py"
Cohesion: 0.36
Nodes (9): all_records(), _conn(), ensure_table(), Subscription inventory — the manually-owned metadata that Azure doesn't hold:…, All stored inventory rows keyed by subscription id., Create or update the owner/budget metadata for a subscription., Toggle scheduled over-budget alerts for one subscription (updates only that…, set_auto_alerts() (+1 more)

### Community 47 - "audit.py"
Cohesion: 0.39
Nodes (8): admin_audit(), _conn(), distinct_actions(), ensure_table(), list_entries(), Audit trail — durable record of who did what, when, on which request. Raw…, Latest-first audit entries with optional filters., Distinct action slugs, for the filter dropdown.

### Community 48 - "Azure access required by AlMadar 360"
Cohesion: 0.22
Nodes (8): 1. Automation service principal, 2. Cost service principal (separate), 3. Optimizer service principal (separate), Azure access required by AlMadar 360, By feature, Least-privilege custom role (data actions it performs), Setup checklist, Simplest grant

### Community 49 - "How Network Copilot Works — High-Level Architecture"
Cohesion: 0.22
Nodes (8): 1. The one-paragraph answer, 2. Why not ARM templates / Bicep / Terraform?, 3. What each admin action does in Azure, 4. Identity & permissions, 5. Request lifecycle (end to end), 6. Data & configuration, 7. Deploying the app itself, How Network Copilot Works — High-Level Architecture

### Community 50 - "Current State"
Cohesion: 0.18
Nodes (10): Active Priorities, Current Blockers, Current State, Development Status, Features Completed (committed, on `main`), Features In Progress, Pending Work, Resource Relationship Graph — built + real-Azure verified 2026-08-02, uncommitted (+2 more)

### Community 51 - "Project Overview"
Cohesion: 0.22
Nodes (8): High-Level Architecture, Important Technologies, Important Workflows, Key Capabilities, Main Modules, Project Overview, Purpose, See Also

### Community 52 - "test_storage_validation.py"
Cohesion: 0.39
Nodes (7): Validation for a Storage Account request at submission time. Offline checks…, _validate_storage_request(), base_details(), check(), main(), Assert-based coverage of Storage Account request validation…, run_validate()

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

### Community 64 - "search.py"
Cohesion: 0.47
Nodes (5): _conn(), global_search(), Global keyword search across requests, VNET info, subnet inventory and the…, Return {'requests': [...], 'vnets': [...], 'subnets': [...], 'audit': [...]}., _rows()

### Community 65 - "Known Issues"
Cohesion: 0.40
Nodes (4): Investigations Needed, Known Issues, Open Bugs, Technical Debt

### Community 66 - "migrate_excel_to_db.py"
Cohesion: 0.60
Nodes (4): ensure_table(), get_pool_key(), One-time migration script: imports subnet inventory from subnets.xlsx into the…, run()

### Community 68 - "db_utils.py"
Cohesion: 0.19
Nodes (16): requester_vnet_created(), allocate_subnet_db(), _conn(), count_used_subnets_db(), create_spoke_request(), deallocate_subnet_db(), get_allocated_subnets_db(), get_vnet_info() (+8 more)

### Community 69 - "agent_requester.py"
Cohesion: 0.26
Nodes (15): chat(), _chat_anthropic(), _chat_openai(), _execute_tool(), _get_client(), Requester Agent — helps requesters submit CIDR requests, update statuses, and…, Create a VNET request via the same validated path as the form API., Create a non-VNET request via the same validated path as the form API. (+7 more)

### Community 70 - "2026-08-02"
Cohesion: 0.25
Nodes (7): 2026-08-02, Blockers, Bugs Fixed, Design Decisions, Files Changed, Next Steps, Work Completed

### Community 71 - "update_spoke_request"
Cohesion: 0.50
Nodes (4): _tool_deallocate_cidr(), deallocate_subnet(), Remove a subnet allocation from the DB., update_spoke_request()

## Knowledge Gaps
- **166 isolated node(s):** `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent`, `2026-07-30 — Line-manager approval routing, dependency-gated`, `2026-07-26 — Budget alerts are forecast-gated, not raw-threshold` (+161 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_name()` connect `admin_azure_action` to `_guard`, `route`, `azure_tools.py`, `_network_client`, `app.py`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Why does `sanitize()` connect `admin_azure_action` to `azure_tools.py`, `_guard`, `app.py`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Why does `_Conn` connect `_Conn` to `insert_returning_id`, `db_backend.py`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent` to the rest of the system?**
  _166 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `_guard` be split into smaller, more focused modules?**
  _Cohesion score 0.0707070707070707 - nodes in this community are weakly interconnected._
- **Should `costmgmt.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10606060606060606 - nodes in this community are weakly interconnected._
- **Should `notifications.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11414141414141414 - nodes in this community are weakly interconnected._