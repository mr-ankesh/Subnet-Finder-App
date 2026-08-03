# Graph Report - .  (2026-08-01)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 948 nodes · 2188 edges · 43 communities (39 shown, 4 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a09bd18f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- db_backend.py
- _is_not_found
- costmgmt.py
- notifications.py
- _guard
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
- agent_requester.py
- require_subnet_access
- chats.py
- models.py
- auth_oidc.py
- _create_service_request
- settings_store.py
- db_utils.py
- auth_callback
- Approval
- _containerservice_client
- RequestType
- RequestProxy
- require_itadmin
- requester_get_status
- SpokeRequest
- brand.js
- get_pool_key
- can_decide
- zpa-networkuser-wrapper.sh
- get_peering_defaults
- deploy.sh
- list_existing_vm_indexes

## God Nodes (most connected - your core abstractions)
1. `record()` - 43 edges
2. `_guard()` - 41 edges
3. `_network_client()` - 39 edges
4. `_is_not_found()` - 34 edges
5. `require_admin()` - 33 edges
6. `require_login()` - 31 edges
7. `require_superadmin()` - 29 edges
8. `current_actor()` - 27 edges
9. `admin_azure_action()` - 21 edges
10. `get_spoke_request()` - 20 edges

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

## Communities (43 total, 4 thin omitted)

### Community 0 - "db_backend.py"
Cohesion: 0.05
Nodes (46): _inventory_data(), Fast path: just the subscription list + stored owner/budget metadata.…, _conn(), ensure_table(), execute_revert(), get_change(), list_changes(), _mark() (+38 more)

### Community 1 - "_is_not_found"
Cohesion: 0.05
Nodes (50): add_route_to_table(), check_private_dns_zone(), create_dns_zone_in_hub(), decommission_check(), delete_dns_record(), delete_dns_zone(), delete_dns_zone_link(), delete_hub_spoke_peerings() (+42 more)

### Community 2 - "costmgmt.py"
Cohesion: 0.06
Nodes (41): _coerce(), Config, Central config — every value resolves live as: DB override → env var → default.…, Attribute access resolves live: DB override → env → default., _cols(), _compute_summary(), configured(), cost_by_dimension() (+33 more)

### Community 3 - "notifications.py"
Cohesion: 0.11
Nodes (44): _adaptive_card(), _compose_email(), draft_budget_alert(), _draft_email(), draft_threshold_alert(), _email_case(), _email_requester(), _looks_unusable() (+36 more)

### Community 4 - "_guard"
Cohesion: 0.09
Nodes (31): add_cidr_to_firewall_rule(), add_firewall_application_rule(), add_firewall_network_rule(), add_udr_routes(), allow_internet_rule(), assign_route_table_to_subnet(), _build_fw_rule(), _collection_kind_conflict() (+23 more)

### Community 5 - "reachability.py"
Cohesion: 0.10
Nodes (38): _classify(), configured(), _cpu_line(), health_all(), _is_ip(), _load_key(), _net_ifaces(), _num() (+30 more)

### Community 6 - "route"
Cohesion: 0.09
Nodes (37): admin_approvals_health(), admin_settings(), admin_settings_preview_name(), admin_settings_test_azure(), admin_settings_test_connector(), admin_settings_test_cost(), admin_settings_test_keycloak(), admin_settings_test_optimize() (+29 more)

### Community 7 - "record"
Cohesion: 0.08
Nodes (35): admin_settings_reset(), admin_settings_save(), api_approval_decide(), api_request_send_approval(), api_subscription_auto_alerts(), current_actor(), fw_collections(), _import_inventory() (+27 more)

### Community 8 - "azure_tools.py"
Cohesion: 0.09
Nodes (29): _addr_covers(), aks_tiers(), _analyze_coverage(), _describe_fw_rule(), find_firewall_rules_for_address(), find_firewall_rules_for_pair(), _fqdn_covers(), get_firewall_policy_status() (+21 more)

### Community 9 - "require_login"
Cohesion: 0.07
Nodes (33): api_approvals_pending(), api_request_approvals(), approvals_page(), azure_aks_options(), azure_disk_skus(), azure_regions(), azure_subnets(), azure_vm_images() (+25 more)

### Community 10 - "_network_client"
Cohesion: 0.08
Nodes (33): add_cidr_to_nsg_rule(), aks_source_subnet(), check_udr(), _diag_subs(), get_nsg_rule_status(), _is_cidr(), list_subnets(), list_vnet_subnets() (+25 more)

### Community 11 - "app.py"
Cohesion: 0.11
Nodes (30): admin_assign_cidr_api(), admin_change_revert(), admin_changes(), admin_deallocate_api(), admin_find_subnets_api(), admin_firewall_lookup(), admin_inventory(), admin_list_requests_api() (+22 more)

### Community 12 - "admin_azure_action"
Cohesion: 0.10
Nodes (30): admin_azure_action(), _auto_advance(), _deploy_one(), _deploy_spoke_route_table(), _deploy_tags(), _done_actions(), _pending_deploy_actions(), Run a single Azure onboarding action for a request: vnet -> create the spoke… (+22 more)

### Community 13 - "agent_admin.py"
Cohesion: 0.14
Nodes (29): _actor(), build_system_prompt(), chat(), _chat_anthropic(), _chat_openai(), _compute_free(), _execute_tool(), _get_client() (+21 more)

### Community 14 - "datetime"
Cohesion: 0.11
Nodes (26): admin_audit(), _conn(), distinct_actions(), ensure_table(), list_entries(), Audit trail — durable record of who did what, when, on which request. Raw…, Latest-first audit entries with optional filters., Distinct action slugs, for the filter dropdown. (+18 more)

### Community 15 - "optimize.py"
Cohesion: 0.14
Nodes (24): _arg(), configured(), _disk_month(), _f(), _headers(), list_subscriptions(), _metric_stats(), _pip_month() (+16 more)

### Community 16 - "create_spoke_vnet"
Cohesion: 0.09
Nodes (24): carve_subnets(), create_aks_disk_encryption(), create_route_table(), create_spoke_vnet(), create_vm(), ensure_resource_group(), _get_credential(), _kv_name() (+16 more)

### Community 17 - "_compute_client"
Cohesion: 0.10
Nodes (22): check_vm_quota(), _compute_client(), delete_vm(), list_marketplace_images(), list_vm_images(), list_vm_sizes(), list_vm_skus(), list_vm_zones() (+14 more)

### Community 18 - "netdiag.py"
Cohesion: 0.14
Nodes (21): _addr_in(), _clean_llm(), diagnose(), _has_cjk(), _hub_fw_ip(), _is_ip(), _is_private(), _is_private_domain() (+13 more)

### Community 19 - "approvals.py"
Cohesion: 0.15
Nodes (19): enabled(), has_valid_trigger_approval(), manager_seen(), _mgr_state(), needs_trigger_approval(), open_submission_gate(), policy_for(), preflight() (+11 more)

### Community 20 - "agent_requester.py"
Cohesion: 0.19
Nodes (19): chat(), _chat_anthropic(), _chat_openai(), _execute_tool(), _get_client(), Requester Agent — helps requesters submit CIDR requests, update statuses, and…, Create a VNET request via the same validated path as the form API., Create a non-VNET request via the same validated path as the form API. (+11 more)

### Community 21 - "require_subnet_access"
Cohesion: 0.17
Nodes (20): all_available(), allocate(), allocated(), allocator(), available_base_route(), candidates_from_free(), compute_free_blocks(), deallocate() (+12 more)

### Community 22 - "chats.py"
Cohesion: 0.25
Nodes (18): agent_chat(), agent_chat_get(), _chat_owner(), Stable per-user key that owns persistent agent chats. Uses the Keycloak…, requester_chat(), requester_chat_get(), append_messages(), _conn() (+10 more)

### Community 23 - "models.py"
Cohesion: 0.12
Nodes (13): Sentinel to short-circuit the SQLite-only column backfill on Postgres., _SkipMigration, Exception, AppSetting, AuditLog, FwCollection, SQLAlchemy models for Spoke Request workflow and subnet inventory., Admin-editable config override (see settings_store.py, which reads/writes this… (+5 more)

### Community 24 - "auth_oidc.py"
Cohesion: 0.13
Nodes (18): client(), _decode_jwt_payload(), end_session_url(), groups_from_token(), init_oidc(), manager_from_token(), _metadata_url(), Keycloak (OIDC) integration — Authlib. Kept deliberately thin: the OIDC… (+10 more)

### Community 25 - "_create_service_request"
Cohesion: 0.14
Nodes (18): _apply_submission_gate(), _cached_vm_skus(), _create_service_request(), _create_vnet_request(), _fqdn_errors(), _fw_params(), Request-scoped memoization of list_vm_skus (a 1000+ entry resourceSkus scan) —…, If the approval flow holds this request type at submission, open the gate (sets… (+10 more)

### Community 26 - "settings_store.py"
Cohesion: 0.23
Nodes (16): all_overrides(), _conn(), _decrypt(), delete_override(), _encrypt(), ensure_table(), _fernet(), get_override() (+8 more)

### Community 27 - "db_utils.py"
Cohesion: 0.21
Nodes (15): allocate_subnet_db(), _conn(), count_used_subnets_db(), create_spoke_request(), deallocate_subnet_db(), get_allocated_subnets_db(), get_vnet_info(), list_spoke_requests() (+7 more)

### Community 28 - "auth_callback"
Cohesion: 0.13
Nodes (15): admin_login(), admin_logout(), _approvals_nav_ctx(), auth_callback(), auth_login(), auth_logout(), _home_endpoint(), inject_globals() (+7 more)

### Community 29 - "Approval"
Cohesion: 0.19
Nodes (12): decide(), open_trigger_gate(), Pick the approver for a new checkpoint. The requester's line manager if we have…, Recompute the cached approval_state on the request from its checkpoints., Admin-initiated: send a specific request for approval (discretion mode)., Raise a pending trigger checkpoint (called when a blocked deploy is attempted)., Record an approve/reject decision and reconcile the request status., request_discretion_approval() (+4 more)

### Community 30 - "_containerservice_client"
Cohesion: 0.18
Nodes (12): _aks_dns_prefix(), _aks_pool_summary(), _containerservice_client(), create_aks_cluster(), delete_aks_cluster(), get_aks_cluster_status(), list_aks_versions(), A valid dnsPrefix: alphanumerics/hyphens, start+end alphanumeric, ≤ 54 chars. (+4 more)

### Community 31 - "RequestType"
Cohesion: 0.20
Nodes (4): policy_matrix(), View model for the settings matrix: one row per request type., Request kinds available in the requester portal, each with its own workflow., RequestType

### Community 32 - "RequestProxy"
Cohesion: 0.25
Nodes (3): Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field., RequestProxy, RequestStatus

### Community 33 - "require_itadmin"
Cohesion: 0.25
Nodes (8): it_connector_health(), it_connector_status(), it_reachability(), it_reachability_run(), Health dashboard: are the connector VMs (primary + secondary) up?, Richer per-VM diagnostics for the dashboard's 'More status'., Guards the Reachability Tester — IT-admins OR super-admins. Open when SSO is…, require_itadmin()

### Community 34 - "requester_get_status"
Cohesion: 0.29
Nodes (7): _owns_request(), The signed-in requester's own requests (SSO only). Empty in open mode., (name, email) identifying the signed-in requester's requests, or None when SSO…, True if the current user may view this request. Admins: all. SSO requesters:…, requester_get_status(), requester_my_requests(), _requester_owner()

### Community 37 - "get_pool_key"
Cohesion: 0.50
Nodes (4): _auto_migrate_excel(), One-time migration: if subnets.xlsx exists and subnet_records table is empty,…, get_pool_key(), Return the pool key ('10.110' / '10.119') for a subnet, or None.

### Community 38 - "can_decide"
Cohesion: 0.50
Nodes (4): can_decide(), pending_for(), Is this actor allowed to approve/reject this checkpoint?, Approvals awaiting this actor's decision (their reports' requests, plus…

### Community 39 - "zpa-networkuser-wrapper.sh"
Cohesion: 0.83
Nodes (3): _allow(), _deny(), zpa-networkuser-wrapper.sh script

### Community 42 - "list_existing_vm_indexes"
Cohesion: 0.17
Nodes (12): assign_vm_zones(), build_vm_plan(), derive_vm_resource_names(), derive_windows_computer_name(), _extract_taken_indexes(), list_existing_vm_indexes(), Windows osProfile.computerName from a resolved VM RESOURCE name (base + -NNN…, NIC/OS-disk names for one resolved VM name (data disk names are numbered per-VM… (+4 more)

## Knowledge Gaps
- **1 isolated node(s):** `deploy.sh script`
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_name()` connect `admin_azure_action` to `azure_tools.py`, `_is_not_found`, `app.py`, `route`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `sanitize()` connect `admin_azure_action` to `azure_tools.py`, `_is_not_found`, `app.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Why does `SpokeRequest` connect `SpokeRequest` to `RequestProxy`, `app.py`, `approvals.py`, `models.py`, `Approval`, `RequestType`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **What connects `deploy.sh script` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `db_backend.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05357142857142857 - nodes in this community are weakly interconnected._
- **Should `_is_not_found` be split into smaller, more focused modules?**
  _Cohesion score 0.053877551020408164 - nodes in this community are weakly interconnected._
- **Should `costmgmt.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06462585034013606 - nodes in this community are weakly interconnected._