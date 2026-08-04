# Graph Report - Subnet-finder-app  (2026-08-04)

## Corpus Check
- 95 files · ~290,809 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1886 nodes · 3965 edges · 100 communities (95 shown, 5 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 16 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `29940b43`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- _Result
- _is_not_found
- costmgmt.py
- notifications.py
- azure_tools.py
- reachability.py
- app.py
- route
- list_existing_vm_indexes
- record
- _guard
- _rg_from_id
- render_name
- agent_admin.py
- budgetalerts.py
- optimize.py
- create_spoke_vnet
- _compute_client
- netdiag.py
- approvals.py
- render.py
- require_subnet_access
- session_store.py
- Expected output
- auth_oidc.py
- Architecture
- settings_store.py
- prompts.py
- composition_engine.py
- network_planner.py
- 2026-08-01
- Approval
- SpokeRequest
- RequestProxy
- _SkipMigration
- config.py
- brand.js
- intake.py
- admin_settings_save
- zpa-networkuser-wrapper.sh
- Keycloak (OIDC) Integration Guide
- deploy.sh
- require_itadmin
- kb_diff.py
- Architecture Decisions
- can_decide
- kb_store.py
- Migration notes — adding services to the existing advisor
- Azure access required by AlMadar 360
- How Network Copilot Works — High-Level Architecture
- Current State
- Project Overview
- test_storage_validation.py
- require_admin
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
- inventory_parser.py
- _Conn
- kb_validate.py
- search.py
- Environment recommendation template
- composer/__init__.py
- orchestrator.py
- db_utils.py
- _chat_owner
- _get_credential
- agent_requester.py
- diagram_builder.py
- recommendation.py
- auth_callback
- chats.py
- _import_inventory
- env_prefill.py
- 2026-08-04
- rules_engine.py
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
1. `require_login()` - 47 edges
2. `_guard()` - 45 edges
3. `record()` - 44 edges
4. `require_superadmin()` - 40 edges
5. `_network_client()` - 40 edges
6. `_is_not_found()` - 35 edges
7. `require_admin()` - 34 edges
8. `Architecture Decisions` - 33 edges
9. `current_actor()` - 29 edges
10. `2026-08-01` - 27 edges

## Surprising Connections (you probably didn't know these)
- `_stage_i_canonical_examples()` --calls--> `_num()`  [INFERRED]
  advisor/kb_validate.py → reachability.py
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

## Communities (100 total, 5 thin omitted)

### Community 0 - "_Result"
Cohesion: 0.19
Nodes (6): _EmptyCursor, _norm(), Normalise a cell so raw callers see the SQLite-era shape (str timestamps)., Eager, backend-neutral result: dict rows materialised at fetch time., Rows affected by the last UPDATE/DELETE — verified identical on both psycopg…, _Result

### Community 1 - "_is_not_found"
Cohesion: 0.10
Nodes (32): check_private_dns_zone(), create_dns_zone_in_hub(), decommission_check(), delete_dns_record(), delete_dns_zone(), delete_dns_zone_link(), _dns_record_dict(), get_dns_record_status() (+24 more)

### Community 2 - "costmgmt.py"
Cohesion: 0.11
Nodes (32): _cols(), _compute_summary(), configured(), cost_by_dimension(), cost_by_resource(), cost_daily(), _descendants(), _headers() (+24 more)

### Community 3 - "notifications.py"
Cohesion: 0.11
Nodes (44): _adaptive_card(), _compose_email(), draft_budget_alert(), _draft_email(), draft_threshold_alert(), _email_case(), _email_requester(), _looks_unusable() (+36 more)

### Community 4 - "azure_tools.py"
Cohesion: 0.06
Nodes (52): add_cidr_to_firewall_rule(), add_firewall_application_rule(), add_firewall_network_rule(), _addr_covers(), aks_tiers(), allow_internet_rule(), _analyze_coverage(), _build_fw_rule() (+44 more)

### Community 5 - "reachability.py"
Cohesion: 0.10
Nodes (38): _classify(), configured(), _cpu_line(), health_all(), _is_ip(), _load_key(), _net_ifaces(), _num() (+30 more)

### Community 6 - "app.py"
Cohesion: 0.08
Nodes (48): advisor_page(), api_advisor_conversations_delete(), api_approval_decide(), api_approvals_pending(), api_request_approvals(), approvals_page(), azure_aks_options(), azure_disk_skus() (+40 more)

### Community 7 - "route"
Cohesion: 0.06
Nodes (54): admin_advisor_kb(), admin_approvals_health(), admin_settings(), admin_settings_preview_name(), admin_settings_test_azure(), admin_settings_test_connector(), admin_settings_test_cost(), admin_settings_test_keycloak() (+46 more)

### Community 8 - "list_existing_vm_indexes"
Cohesion: 0.17
Nodes (12): assign_vm_zones(), build_vm_plan(), derive_vm_resource_names(), derive_windows_computer_name(), _extract_taken_indexes(), list_existing_vm_indexes(), Windows osProfile.computerName from a resolved VM RESOURCE name (base + -NNN…, NIC/OS-disk names for one resolved VM name (data disk names are numbered per-VM… (+4 more)

### Community 9 - "record"
Cohesion: 0.06
Nodes (59): admin_audit(), admin_azure_action(), admin_change_revert(), admin_vm_preview(), api_request_send_approval(), api_subscription_auto_alerts(), _auto_advance(), current_actor() (+51 more)

### Community 10 - "_guard"
Cohesion: 0.07
Nodes (39): add_cidr_to_nsg_rule(), add_route_to_table(), add_udr_routes(), assign_route_table_to_subnet(), check_udr(), delete_route_from_table(), delete_spoke_route_table(), delete_spoke_vnet() (+31 more)

### Community 11 - "_rg_from_id"
Cohesion: 0.15
Nodes (16): aks_source_subnet(), _diag_subs(), _is_cidr(), list_keyvaults(), list_vnets(), locate_ip(), All VNets visible in a subscription (name, RG, region, address space)., Find the VNet/subnet a (private) IP belongs to, across known subscriptions. (+8 more)

### Community 12 - "render_name"
Cohesion: 0.18
Nodes (13): Undo one deployed change. Returns the azure_tools-style result dict., _revert_change(), delete_hub_spoke_peerings(), peer_hub_vnet(), Delete both peering directions (spoke→hub and hub→spoke)., Creates VNET peering in both directions (spoke→hub, hub→spoke). If peering…, Resource-name rendering from the admin-configurable naming templates. Templates…, Lowercase, alnum + dash — safe inside an Azure resource name. (+5 more)

### Community 13 - "agent_admin.py"
Cohesion: 0.12
Nodes (30): _actor(), build_system_prompt(), chat(), _chat_anthropic(), _chat_openai(), _compute_free(), _execute_tool(), _get_client() (+22 more)

### Community 14 - "budgetalerts.py"
Cohesion: 0.23
Nodes (13): assess(), _conn(), ensure_table(), evaluate_and_send(), _last_severity(), Automatic over-budget alerts for subscriptions. The hard part isn't emailing at…, Check every opted-in subscription and email escalations. Returns a report:…, Start the periodic budget checker as a daemon thread (idempotent). Runs only… (+5 more)

### Community 15 - "optimize.py"
Cohesion: 0.14
Nodes (24): _arg(), configured(), _disk_month(), _f(), _headers(), list_subscriptions(), _metric_stats(), _pip_month() (+16 more)

### Community 16 - "create_spoke_vnet"
Cohesion: 0.09
Nodes (23): carve_subnets(), check_storage_name_availability(), create_object_replication_policy(), create_route_table(), create_spoke_vnet(), create_storage_account(), create_storage_private_endpoint(), delete_storage_account() (+15 more)

### Community 17 - "_compute_client"
Cohesion: 0.09
Nodes (24): check_vm_quota(), _compute_client(), create_vm(), delete_vm(), list_marketplace_images(), list_vm_images(), list_vm_sizes(), list_vm_skus() (+16 more)

### Community 18 - "netdiag.py"
Cohesion: 0.12
Nodes (23): List a VNet's peerings (name, state, remote VNet)., vnet_peerings(), _addr_in(), _clean_llm(), diagnose(), _has_cjk(), _hub_fw_ip(), _is_ip() (+15 more)

### Community 19 - "approvals.py"
Cohesion: 0.15
Nodes (19): enabled(), has_valid_trigger_approval(), manager_seen(), _mgr_state(), needs_trigger_approval(), open_submission_gate(), policy_for(), preflight() (+11 more)

### Community 20 - "render.py"
Cohesion: 0.16
Nodes (19): _env_label(), _fallback_summary(), Deterministic renderer for environment_recommendation_template.md's shape. This…, Verbatim from infosec_gate.yaml's user_message — never LLM-touched., Normalizes to {step, detail} only. One entry in network_sizing.yaml's…, Everything the frontend/template needs, fully structured — never a single…, The one paragraph an LLM narration pass may rewrite; this is the deterministic…, Never renders the Pod CIDR as a subnet row — it's returned as a separate… (+11 more)

### Community 21 - "require_subnet_access"
Cohesion: 0.17
Nodes (20): all_available(), allocate(), allocated(), allocator(), available_base_route(), candidates_from_free(), compute_free_blocks(), deallocate() (+12 more)

### Community 22 - "session_store.py"
Cohesion: 0.50
Nodes (8): _conn(), create_session(), ensure_table(), _now(), Persistent advisor conversation state — raw sqlite3/db_backend, same pattern as…, `mode` distinguishes a single-service advisor conversation from an environment-…, save_prefill(), save_state()

### Community 23 - "Expected output"
Cohesion: 0.11
Nodes (17): Before you start, Build sequence, Changelog — 2.0.0 → 2.1.0, Components, Expected output, Hub integration, Input, Negative test — must also pass (+9 more)

### Community 24 - "auth_oidc.py"
Cohesion: 0.13
Nodes (18): client(), _decode_jwt_payload(), end_session_url(), groups_from_token(), init_oidc(), manager_from_token(), _metadata_url(), Keycloak (OIDC) integration — Authlib. Kept deliberately thin: the OIDC… (+10 more)

### Community 25 - "Architecture"
Cohesion: 0.06
Nodes (29): AI agents are tool-callers, not free-form SQL/Azure access, AI Architecture Advisor: rules decide, LLM explains — not a `RequestType`, Approval flow: relationship-based routing with a dependency gate, Architecture, Auth: local password or Keycloak SSO, switched live, Azure changes: imperative SDK calls, not IaC, Budget alerts: forecast-gated, not raw-threshold, CIDR pool allocator (the app's namesake feature) (+21 more)

### Community 26 - "settings_store.py"
Cohesion: 0.23
Nodes (16): all_overrides(), _conn(), _decrypt(), delete_override(), _encrypt(), ensure_table(), _fernet(), get_override() (+8 more)

### Community 27 - "prompts.py"
Cohesion: 0.19
Nodes (14): load_text_file(), call_llm(), _get_client(), get_environment_recommendation_template(), get_environment_system_prompt(), get_recommendation_template(), get_system_prompts(), Loads the 3 system prompts verbatim from… (+6 more)

### Community 28 - "composition_engine.py"
Cohesion: 0.13
Nodes (29): get_composer_file(), A composer/*.yaml file (e.g. infosec_gate.yaml), referenced by a decision…, dependency_graph(), environment_deviations(), environment_warnings(), evaluate_environment_blockers(), evaluate_full(), exposure_analysis() (+21 more)

### Community 29 - "network_planner.py"
Cohesion: 0.07
Nodes (50): get_platform_constants(), Shared, service-agnostic reference facts (naming pattern, DNS zones, encryption…, aks_private_zone_note(), _approx_capacity_nodes(), _bucket_lookup(), build_network_plan(), compute_vnet_plan(), mandatory_spoke_wiring() (+42 more)

### Community 30 - "2026-08-01"
Cohesion: 0.07
Nodes (27): 2026-08-01, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Bugs Fixed (caught during this session's own implementation, before commit), Design Decisions (+19 more)

### Community 31 - "Approval"
Cohesion: 0.19
Nodes (12): decide(), open_trigger_gate(), Pick the approver for a new checkpoint. The requester's line manager if we have…, Recompute the cached approval_state on the request from its checkpoints., Admin-initiated: send a specific request for approval (discretion mode)., Raise a pending trigger checkpoint (called when a blocked deploy is attempted)., Record an approve/reject decision and reconcile the request status., request_discretion_approval() (+4 more)

### Community 32 - "SpokeRequest"
Cohesion: 0.15
Nodes (5): policy_matrix(), View model for the settings matrix: one row per request type., Request kinds available in the requester portal, each with its own workflow., RequestType, SpokeRequest

### Community 33 - "RequestProxy"
Cohesion: 0.25
Nodes (3): Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field., RequestProxy, RequestStatus

### Community 34 - "_SkipMigration"
Cohesion: 0.22
Nodes (6): Exception, Sentinel to short-circuit the SQLite-only column backfill on Postgres., _SkipMigration, FwCollection, Admin-defined firewall rule collection group / rule collection pairs (one-time…, VnetInfo

### Community 35 - "config.py"
Cohesion: 0.06
Nodes (48): _coerce(), Config, Central config — every value resolves live as: DB override → env var → default.…, Attribute access resolves live: DB override → env → default., _add_edge(), _arg(), build_graph(), _category_for_type() (+40 more)

### Community 37 - "intake.py"
Cohesion: 0.26
Nodes (12): find_question(), is_complete(), new_state(), next_question(), _normalize_question(), _questions(), _questions_in_order(), Environment-mode intake flow controller —… (+4 more)

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

### Community 43 - "kb_diff.py"
Cohesion: 0.09
Nodes (31): _catalog(), _condition_strings(), _diff_canonical_examples(), _diff_conditions(), _diff_glossary(), diff_kb(), _diff_locked_fields(), _diff_patterns() (+23 more)

### Community 44 - "Architecture Decisions"
Cohesion: 0.06
Nodes (33): 2026-03-26 — AI agents are tool-callers only, never raw SQL/Azure access, 2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent, 2026-07-15 → 2026-07-29 — Rebrand: Subnet Manager → Network Copilot → Presight AlMadar 360, 2026-07-18 — PostgreSQL as an optional backend, SQLite stays default, 2026-07-26 — Budget alerts are forecast-gated, not raw-threshold, 2026-07-30 — Line-manager approval routing, dependency-gated, 2026-07-31 (approx) — Password never persisted for VM auth, 2026-07-31 — VM(s) deployment: plan resolved once, persisted, resumable (+25 more)

### Community 45 - "can_decide"
Cohesion: 0.50
Nodes (4): can_decide(), pending_for(), Is this actor allowed to approve/reject this checkpoint?, Approvals awaiting this actor's decision (their reports' requests, plus…

### Community 46 - "kb_store.py"
Cohesion: 0.10
Nodes (39): activate(), activate_and_audit(), _audit_activation(), _conn(), ensure_tables(), get_active_version(), get_files(), get_version() (+31 more)

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
Cohesion: 0.13
Nodes (14): Active Priorities, Advisor Knowledge Base management — fully committed (`c0b5fc1`/`1103f97`/`22390f6`/`84c5e1d`/`c9b5452`/`cb7340b`/`ca5a738`/`29940b4`), AI Architecture Advisor — storage-only V1, then expanded to six services, Current Blockers, Current State, Development Status, Environment composer — Phase 3, fully committed (`2298611`/`6fde2de`/`9da0fe1`/`5144094`/`7e67cae`), Features Completed (committed, on `main`) (+6 more)

### Community 51 - "Project Overview"
Cohesion: 0.22
Nodes (8): High-Level Architecture, Important Technologies, Important Workflows, Key Capabilities, Main Modules, Project Overview, Purpose, See Also

### Community 52 - "test_storage_validation.py"
Cohesion: 0.39
Nodes (7): Validation for a Storage Account request at submission time. Offline checks…, _validate_storage_request(), base_details(), check(), main(), Assert-based coverage of Storage Account request validation…, run_validate()

### Community 53 - "require_admin"
Cohesion: 0.10
Nodes (21): admin_assign_cidr_api(), admin_changes(), admin_deallocate_api(), admin_find_subnets_api(), admin_firewall_lookup(), admin_list_requests_api(), admin_search(), admin_storage_preview() (+13 more)

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
Cohesion: 0.15
Nodes (12): datetime, ensure_table(), get_pool_key(), One-time migration script: imports subnet inventory from subnets.xlsx into the…, run(), AppSetting, AuditLog, SQLAlchemy models for Spoke Request workflow and subnet inventory. (+4 more)

### Community 68 - "_containerservice_client"
Cohesion: 0.18
Nodes (12): _aks_dns_prefix(), _aks_pool_summary(), _containerservice_client(), create_aks_cluster(), delete_aks_cluster(), get_aks_cluster_status(), list_aks_versions(), A valid dnsPrefix: alphanumerics/hyphens, start+end alphanumeric, ≤ 54 chars. (+4 more)

### Community 69 - "subinventory.py"
Cohesion: 0.36
Nodes (9): all_records(), _conn(), ensure_table(), Subscription inventory — the manually-owned metadata that Azure doesn't hold:…, All stored inventory rows keyed by subscription id., Create or update the owner/budget metadata for a subscription., Toggle scheduled over-budget alerts for one subscription (updates only that…, set_auto_alerts() (+1 more)

### Community 70 - "2026-08-02"
Cohesion: 0.08
Nodes (24): 2026-08-02, Blockers, Blockers, Blockers, Blockers, Bugs Fixed, Design Decisions, Design Decisions (+16 more)

### Community 71 - "inventory_parser.py"
Cohesion: 0.21
Nodes (13): _coerce(), Records one answer and advances the flow. Returns the updated state., record_answer(), _count_for(), _counted_pattern(), format_confirmation(), _normalize_word_numbers(), parse_inventory() (+5 more)

### Community 73 - "kb_validate.py"
Cohesion: 0.18
Nodes (24): _err(), _normalize_term(), Atomic validation gate for an advisor_kb/ upload — stages a-i, run against the…, Returns {pattern_id: pattern_dict} for every catalog file that parsed and…, STRICT (hard reject) for the fields actually run through…, Same normalization as advisor/glossary.py's _normalize() — related: entries are…, Stage (a) for every YAML file not already parsed by a more specific stage above…, files: {relative_path: content}. Runs every stage regardless of earlier… (+16 more)

### Community 74 - "search.py"
Cohesion: 0.47
Nodes (5): _conn(), global_search(), Global keyword search across requests, VNET info, subnet inventory and the…, Return {'requests': [...], 'vnets': [...], 'subnets': [...], 'audit': [...]}., _rows()

### Community 75 - "Environment recommendation template"
Cohesion: 0.40
Nodes (4): Environment recommendation template, Rendered shape, Rules specific to environment output, System prompt — environment composition

### Community 77 - "orchestrator.py"
Cohesion: 0.05
Nodes (75): pinned_to(), Pin every catalog_loader read within this `with` block to a specific DB-stored…, _backend_description(), draft_brief(), _field_value(), _gate(), gate_fires(), get_message_ref() (+67 more)

### Community 78 - "db_utils.py"
Cohesion: 0.24
Nodes (13): allocate_subnet_db(), _conn(), count_used_subnets_db(), create_spoke_request(), get_allocated_subnets_db(), get_vnet_info(), list_spoke_requests(), Direct SQLite3 helpers for agent tool DB operations. Bypasses Flask-… (+5 more)

### Community 79 - "_chat_owner"
Cohesion: 0.16
Nodes (23): get_session(), owns(), _advisor_env_classify_free_text(), _advisor_env_question_payload(), _advisor_environment_session(), agent_chat(), agent_chat_delete(), agent_chat_get() (+15 more)

### Community 80 - "_get_credential"
Cohesion: 0.12
Nodes (17): create_aks_disk_encryption(), ensure_resource_group(), _get_credential(), _kv_name(), list_keyvault_keys(), list_locations(), list_subscriptions(), list_user_assigned_identities() (+9 more)

### Community 82 - "agent_requester.py"
Cohesion: 0.09
Nodes (38): chat(), _chat_anthropic(), _chat_openai(), _execute_tool(), _get_client(), Requester Agent — helps requesters submit CIDR requests, update statuses, and…, Create a VNET request via the same validated path as the form API., Create a non-VNET request via the same validated path as the form API. (+30 more)

### Community 83 - "diagram_builder.py"
Cohesion: 0.23
Nodes (14): _backend_label(), _engine_confirmed_uses_blob(), _escape(), _illustrative_base_name(), _placeholder_values(), Renders a pattern's Mermaid diagram template (advisor_kb/diagrams/*.mmd) by…, Diagram-only illustration, never the real prefilled vm_base_name field (which…, environment_full.mmd, for the whole-environment composer — NOT tied to a single… (+6 more)

### Community 84 - "recommendation.py"
Cohesion: 0.17
Nodes (16): build_recommendation(), build_recommendation_generic(), _fallback_why_generic(), Assembles the final recommendation per…, Generic (service-agnostic) version of build_recommendation()'s requests-list…, Same shape and spirit as build_recommendation() (storage), generalized for…, A quiet, non-alarming note when the recommended pattern's last_verified date is…, Same spirit as _fallback_why, generalized for AKS/VM/Postgres/AppGW — each… (+8 more)

### Community 85 - "auth_callback"
Cohesion: 0.13
Nodes (15): admin_login(), admin_logout(), _approvals_nav_ctx(), auth_callback(), auth_login(), auth_logout(), _home_endpoint(), inject_globals() (+7 more)

### Community 87 - "chats.py"
Cohesion: 0.38
Nodes (10): append_messages(), _conn(), create_chat(), delete_chat(), ensure_table(), list_chats(), _now(), Persistent agent chats — conversations survive across sessions and devices so… (+2 more)

### Community 89 - "_import_inventory"
Cohesion: 0.25
Nodes (8): admin_inventory(), _auto_migrate_excel(), _import_inventory(), Bulk-load current allocations. rows = [[subnet, purpose, requested_by,…, Post-deployment onboarding: the app ships with an EMPTY inventory — the admin…, One-time migration: if subnets.xlsx exists and subnet_records table is empty,…, get_pool_key(), Return the pool key ('10.110' / '10.119') for a subnet, or None.

### Community 91 - "env_prefill.py"
Cohesion: 0.47
Nodes (5): build_request_list(), _common_fields(), _known_fields_for(), Builds the ordered, prefilled request list for…, Flattens every wave's requests into one ordered list, each carrying {wave,…

### Community 92 - "2026-08-04"
Cohesion: 0.25
Nodes (7): 2026-08-04, Advisor Knowledge Base management (same day, second build), Key Decisions, Key Decisions (KB management), Open Items (KB management), Open Items (persistent-chat build, earlier today), Work Completed

### Community 94 - "rules_engine.py"
Cohesion: 0.14
Nodes (25): get_rules(), apply_set(), evaluate(), _quote_bare_enums(), Shared restricted-expression evaluator for the small condition language used…, Evaluate a KB condition string against `namespace` (field name -> value, values…, Static validity check for a KB condition string, used by advisor/kb_validate.py…, Parse and apply one or more `field = value` assignments from the KB's `set:`… (+17 more)

### Community 104 - "catalog_loader.py"
Cohesion: 0.12
Nodes (30): AdvisorKBError, _current_source(), get_catalog(), _get_catalog_impl(), _get_composer_file_impl(), _get_mapping_impl(), _get_platform_constants_impl(), get_questions() (+22 more)

### Community 109 - "prefill.py"
Cohesion: 0.17
Nodes (24): get_mapping(), AttrDict, Dict that also supports attribute access (derived.lifecycle_to_archive,…, build_prefill(), build_prefill_aks(), build_prefill_appgw(), build_prefill_postgres(), build_prefill_vm() (+16 more)

### Community 112 - "test_advisor_validation.py"
Cohesion: 0.13
Nodes (20): _coerce_value(), find_question(), is_complete(), next_question(), _questions_in_order(), Ask-order flow control for advisor_kb/questions/<service>_questions.yaml:…, Records a (already-normalized) answer, applying default_if_unknown, queuing any…, `service` may be None (only the service-selection question is findable before a… (+12 more)

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
- **274 isolated node(s):** `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent`, `2026-07-30 — Line-manager approval routing, dependency-gated`, `2026-07-26 — Budget alerts are forecast-gated, not raw-threshold` (+269 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_name()` connect `render_name` to `record`, `azure_tools.py`, `app.py`, `route`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `sanitize()` connect `render_name` to `record`, `azure_tools.py`, `app.py`, `_is_not_found`?**
  _High betweenness centrality (0.017) - this node is a cross-community bridge._
- **Why does `RequestStatus` connect `RequestProxy` to `_SkipMigration`, `datetime`, `app.py`, `record`, `agent_admin.py`, `db_utils.py`, `agent_requester.py`, `approvals.py`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `2026-07-18 — PostgreSQL as an optional backend, SQLite stays default`, `2026-03-26 — No IaC; Azure SDK calls are imperative and idempotent` to the rest of the system?**
  _274 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `_is_not_found` be split into smaller, more focused modules?**
  _Cohesion score 0.0967741935483871 - nodes in this community are weakly interconnected._
- **Should `costmgmt.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10606060606060606 - nodes in this community are weakly interconnected._
- **Should `notifications.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11414141414141414 - nodes in this community are weakly interconnected._