"""
Central config — every value resolves live as: DB override → env var → default.

SETTINGS_SPEC is the single source of truth for the admin Settings UI
(/admin/settings): categories become tabs, and each entry carries its label,
help text, type and secret flag. Editing a value in the UI writes a row to
the app_settings table (see settings_store.py) and takes effect immediately —
no restart needed, because Config resolves attributes on every access.

Security-critical bootstrap values (FLASK_SECRET_KEY, ADMIN_PASSWORD, DEBUG,
AI provider keys) stay env-only and are NOT editable from the UI.
"""
import os
from dotenv import load_dotenv

import settings_store

load_dotenv()


# Fallback lists for the AKS form before Azure is queried — the live
# "Fetch from Azure" lookups are the source of truth for versions/sizes.
AKS_FALLBACK_VERSIONS = ["1.34.8", "1.33.4", "1.32.8"]
AKS_FALLBACK_SIZES = ["Standard_D8ds_v5", "Standard_D4ds_v5",
                      "Standard_D16ds_v5", "Standard_D2ds_v5"]
# The organisation-standard region shown to requesters (UAE North).
AKS_STANDARD_REGION = "uaenorth"

# Managed-disk SKUs legal as a VM's OS disk (Azure rejects PremiumV2_LRS/
# UltraSSD_LRS there — those are data-disk-only). Data disks get the full set,
# filtered further to what the chosen VM SKU actually supports (list_disk_skus).
VM_OS_DISK_TYPES = ["Standard_LRS", "StandardSSD_LRS", "Premium_LRS"]
VM_DATA_DISK_TYPES = VM_OS_DISK_TYPES + ["PremiumV2_LRS", "UltraSSD_LRS"]

# Storage account kind/SKU/access-tier option sets (fixed by Azure, not fetched live).
STORAGE_KINDS = ["StorageV2", "BlobStorage"]
STORAGE_SKUS = ["Standard_LRS", "Standard_GRS", "Standard_ZRS", "Standard_RAGRS", "Premium_LRS", "Premium_ZRS"]
STORAGE_ACCESS_TIERS = ["Hot", "Cool"]


# ── UI categories (tab order) ───────────────────────────────────────────────

CATEGORIES = {
    "credentials":   {"title": "Azure Credentials",  "desc": "Identity used for all Azure operations. Needs Network Contributor on hub & spoke scopes."},
    "cost":          {"title": "Cost / Billing",      "desc": "A SEPARATE service principal used only for the subscription cost dashboard — isolated from the network-automation credentials. It needs Cost Management Reader (and Reader to list subscriptions) on the scopes you want reported."},
    "optimize":      {"title": "Resource Optimizer",  "desc": "A SEPARATE, read-only service principal used only to scan for idle / orphaned Azure resources (unattached disks, unassociated public IPs, stopped VMs, old snapshots, orphaned NSGs/route tables, empty resource groups). It needs just Reader on the scopes you want scanned — isolated from automation and cost. Findings are advisory; the platform never deletes anything."},
    "resourcegraph": {"title": "Resource Graph",      "desc": "A SEPARATE, read-only service principal used only by the Resource Relationship Graph (visual map of resource dependencies for troubleshooting/governance). It needs just Reader on the scopes you want graphed — isolated from automation, cost and the optimizer. This module never mutates anything."},
    "hub":           {"title": "Hub & Subscriptions", "desc": "Hub VNET topology and default subscriptions/region for new spokes."},
    "firewall":      {"title": "Firewall",            "desc": "Azure Firewall policy that receives spoke egress rules."},
    "routing":       {"title": "Routing / UDRs",      "desc": "Hub route tables updated when a spoke is onboarded."},
    "nmo":           {"title": "ZPA NMO Integration", "desc": "Targets used by 'Routing from NMO ZPA' requests: the NMO routing table, connector subnet, the NSG outbound allow rule and the firewall allow/deny rules that carry per-spoke CIDR lists."},
    "connectors":    {"title": "ZPA Connector VMs", "desc": "SSH access to the R&D and NMO ZPA connector VMs (primary + optional secondary/HA). Powers the ZPA Analyzer Portal: the health dashboard and the ping/telnet/curl analyzer run from these VMs. Keys are encrypted at rest."},
    "peering":       {"title": "Peering Defaults",    "desc": "Defaults applied to hub↔spoke peerings (overridable per action)."},
    "naming":        {"title": "Naming Conventions",  "desc": "Templates for generated resource names. Placeholders: {vnet} {request_id} {region} {cidr_mask} {purpose} {date}. Global prefix/suffix are joined with '-'."},
    "notifications": {"title": "Notifications",       "desc": "Teams and email notifications for request lifecycle events."},
    "aks":           {"title": "AKS Defaults",         "desc": "Defaults applied to 'AKS Cluster' deployment requests. The requester only chooses VNET/subnet, Kubernetes version and node pool sizing — everything else (network profile, CIDRs, security, upgrade channels) comes from here and can be tuned per environment."},
    "vm":            {"title": "VM Defaults",          "desc": "Defaults and guard rails applied to 'Virtual Machine(s)' deployment requests — max VMs per request, curated image list, disk/naming defaults, and whether password auth is offered at all (SSH keys are the default and recommended everywhere)."},
    "storage":       {"title": "Storage Defaults",     "desc": "Defaults and guard rails applied to 'Storage Account' deployment requests — allowed SKUs, container cap, CMK default. Security defaults (TLS 1.2, HTTPS-only, shared-key access disabled, blob public access disabled, infrastructure encryption, default-deny network rules) are NOT settings-editable — they're fixed in the deploy code, not guard rails an admin can loosen here."},
    "teams":         {"title": "Teams",                "desc": "The requester teams. A team is mandatory when raising a request, and a requester can see every ticket raised by their team."},
    "approvals":     {"title": "Approvals",            "desc": "Optional approval flow: hold selected request types until the requester's line manager approves. Approval routing relies on the manager attribute flowing from Azure Entra ID into Keycloak and out as a token claim — enabling the feature runs a dependency check and auto-disables if the prerequisites aren't met."},
    "agent":         {"title": "AI Agent / LLM",      "desc": "Provider and model used by the requester & admin chat agents. Changes apply to the next conversation turn — no restart needed."},
    "advisor":       {"title": "Advisor",              "desc": "The AI Architecture Advisor's persistent conversational chat — history, free-form Q&A, resume. Uses the same AI Agent / LLM provider configured above; these settings only control the chat's own limits and lifecycle."},
    "auth":          {"title": "Authentication",      "desc": "Keycloak-ready SSO configuration. Values are stored now; enforcement ships with the integration (see docs/KEYCLOAK.md for the step-by-step guide) — login stays password-based until then."},
    "safety":        {"title": "Safety",              "desc": "Guard rails for Azure execution."},
}


def _f(category, label, default="", type="str", secret=False, help="", options=None,
       multiline=False):
    return {"category": category, "label": label, "default": default,
            "type": type, "secret": secret, "help": help, "options": options,
            "multiline": multiline}


# key → field spec (env var name == key)
SETTINGS_SPEC = {
    # ── Azure Credentials ──
    "AZURE_AUTH_MODE":      _f("credentials", "Authentication mode", "service_principal",
                               options=["service_principal", "managed_identity"],
                               help="Service Principal uses tenant/client/secret below. Managed Identity is used when hosted on Azure (AKS, App Service, VM)."),
    "AZURE_TENANT_ID":      _f("credentials", "Tenant ID", help="Entra ID tenant GUID (Service Principal mode)."),
    "AZURE_CLIENT_ID":      _f("credentials", "Client ID", help="App registration (client) GUID (Service Principal mode)."),
    "AZURE_CLIENT_SECRET":  _f("credentials", "Client secret", secret=True,
                               help="Stored encrypted. Leave blank on save to keep the current value."),
    "AZURE_MI_CLIENT_ID":   _f("credentials", "Managed Identity client ID",
                               help="Only for user-assigned Managed Identity; leave blank for system-assigned."),
    "AZURE_SP_OBJECT_ID":   _f("credentials", "Automation SP object ID",
                               help="Object (principal) ID of the automation SP/identity — GUID from Entra. "
                                    "Used to grant the SP key permissions when creating a Key Vault for AKS "
                                    "customer-managed-key disk encryption. Optional but recommended for CMK."),

    # ── Cost / Billing (separate service principal) ──
    "COST_TENANT_ID":       _f("cost", "Cost SP tenant ID",
                               help="Entra tenant of the cost service principal (may equal the main tenant)."),
    "COST_CLIENT_ID":       _f("cost", "Cost SP client ID",
                               help="App registration (client) GUID of the SEPARATE cost service principal."),
    "COST_CLIENT_SECRET":   _f("cost", "Cost SP client secret", secret=True,
                               help="Stored encrypted. Leave blank on save to keep the current value."),
    "COST_SUBSCRIPTIONS":   _f("cost", "Subscriptions to report",
                               help="Comma-separated subscription IDs to include. Blank = auto-discover every "
                                    "subscription the cost SP can see."),
    "COST_MANAGEMENT_GROUP": _f("cost", "Management group ID (fast spend)",
                               help="Optional. Blank = auto-discover the management group the cost SP can read "
                                    "and fetch all subscription spend in ONE grouped query (far faster; avoids "
                                    "Cost Management throttling). Set an ID only to pin a specific management "
                                    "group and skip discovery. If neither auto-discovery nor this works, spend "
                                    "falls back to slower per-subscription queries."),
    "COST_CURRENCY":        _f("cost", "Currency symbol", "$",
                               help="Symbol shown in the dashboard (e.g. $, €, AED). Actual currency comes from Azure."),

    # ── Resource Optimizer (separate read-only SP) ──
    "OPT_TENANT_ID":        _f("optimize", "Optimizer SP tenant ID",
                               help="Entra tenant of the read-only optimizer service principal (may equal the main tenant)."),
    "OPT_CLIENT_ID":        _f("optimize", "Optimizer SP client ID",
                               help="App registration (client) GUID of the SEPARATE, read-only optimizer service principal."),
    "OPT_CLIENT_SECRET":    _f("optimize", "Optimizer SP client secret", secret=True,
                               help="Stored encrypted. Leave blank on save to keep the current value."),
    "OPT_SUBSCRIPTIONS":    _f("optimize", "Subscriptions to scan",
                               help="Comma-separated subscription IDs to scan. Blank = every subscription the "
                                    "optimizer SP can see (Reader)."),
    "OPT_SNAPSHOT_AGE_DAYS": _f("optimize", "Flag snapshots older than (days)", "90", type="int",
                               help="Managed-disk snapshots older than this are reported as stale."),
    "OPT_USAGE_SCAN":       _f("optimize", "Scan usage patterns (CPU)", "true", type="bool",
                               help="Also flag running VMs that were under-utilised over the last 30 days, "
                                    "using Azure Monitor CPU metrics (Reader covers metrics read). Adds one "
                                    "metric query per running VM — slightly slower; results are cached."),
    "OPT_LOW_CPU_AVG":      _f("optimize", "Low-CPU average threshold (%)", "5", type="int",
                               help="A running VM whose 30-day AVERAGE CPU is below this is a downsize/"
                                    "deallocate candidate."),
    "OPT_LOW_CPU_MAX":      _f("optimize", "Low-CPU peak threshold (%)", "20", type="int",
                               help="…and only if its 30-day PEAK CPU also stayed below this (so bursty but "
                                    "mostly-idle VMs aren't wrongly flagged)."),

    # ── Resource Relationship Graph (separate read-only SP) ──
    "RESGRAPH_TENANT_ID":     _f("resourcegraph", "Resource Graph SP tenant ID",
                               help="Entra tenant of the read-only Resource Graph service principal (may equal the main tenant)."),
    "RESGRAPH_CLIENT_ID":     _f("resourcegraph", "Resource Graph SP client ID",
                               help="App registration (client) GUID of the SEPARATE, read-only Resource Graph service principal."),
    "RESGRAPH_CLIENT_SECRET": _f("resourcegraph", "Resource Graph SP client secret", secret=True,
                               help="Stored encrypted. Leave blank on save to keep the current value."),
    "RESGRAPH_MAX_NODES":     _f("resourcegraph", "Max nodes per graph", "300", type="int",
                               help="Safety cap on graph size — a whole-subscription or high-fan-out query stops "
                                    "expanding once this many nodes are included, breadth-first by hop level "
                                    "(so which nodes are kept is deterministic, not iteration-order dependent)."),
    "RESGRAPH_MAX_HOPS":      _f("resourcegraph", "Max hops from root", "3", type="int",
                               help="How far the graph expands from the selected resource (or from every resource "
                                    "in scope, if none is selected) before stopping, regardless of node count."),

    # ── Hub & Subscriptions ──
    "HUB_SUBSCRIPTION_ID":   _f("hub", "Hub subscription ID"),
    "HUB_RESOURCE_GROUP":    _f("hub", "Hub resource group"),
    "HUB_VNET_NAME":         _f("hub", "Hub VNET name"),
    "SPOKE_SUBSCRIPTION_ID": _f("hub", "Default spoke subscription ID",
                                help="Used when a request doesn't specify its own subscription."),
    "DEFAULT_AZURE_REGION":  _f("hub", "Default region", "uaenorth"),
    "DNS_ZONE_RG":           _f("hub", "Private DNS zones resource group",
                                help="Hub resource group holding the private DNS zones — used by DNS "
                                     "requests to check zone availability."),
    "DNS_ZONE_SUBSCRIPTION_ID": _f("hub", "Private DNS zones subscription ID",
                                   help="Blank = hub subscription."),
    "PRIVATE_DNS_SUFFIXES":  _f("hub", "Private domain suffixes", "presight.ai,privatelink,internal",
                                help="Comma-separated domain suffixes treated as PRIVATE by the network "
                                     "diagnosis (resolved via private DNS, then traced internally). Any name "
                                     "containing 'privatelink' is always treated as private."),

    # ── Firewall ──
    "FIREWALL_POLICY_NAME":           _f("firewall", "Firewall policy name"),
    "FIREWALL_POLICY_RG":             _f("firewall", "Firewall policy resource group"),
    "FIREWALL_RULE_COLLECTION_GROUP": _f("firewall", "Rule collection group",
                                         help="Rule collection group receiving spoke network/application rules."),
    "HUB_FIREWALL_PRIVATE_IP":        _f("firewall", "Firewall private IP", "10.110.2.4",
                                         help="Next hop for spoke default routes (0.0.0.0/0)."),

    # ── Routing / UDRs ──
    "UDR_RESOURCE_GROUP": _f("routing", "UDR resource group", help="Resource group holding the hub route tables below."),
    "UDR_GATEWAY_NAME":   _f("routing", "Gateway route table", help="Gets a route to each new spoke."),
    "UDR_ZPA_NAME":       _f("routing", "ZPA route table", help="Gets a route to each new spoke."),
    "ZPA_CONNECTION_SUBNET": _f("routing", "ZPA connection subnet (CIDR)",
                                help="ZPA R&D connector subnet — routed into a spoke's UDR when ZPA routing is requested."),
    "UDR_NAME_1":         _f("routing", "Hub UDR #1", help="Legacy pair updated by 'add routes to both hub UDRs'."),
    "UDR_NAME_2":         _f("routing", "Hub UDR #2"),
    "SPOKE_DEFAULT_ROUTES": _f("routing", "Spoke default routes",
                               "udr-to-azurevpn1=10.108.201.0/25, udr-to-azurevpn2=10.108.201.128/25, "
                               "udr-to-default=0.0.0.0/0, udr-to-zpa-rnd=10.110.5.32/27",
                               help="name=prefix pairs (comma-separated) added to every new spoke route "
                                    "table. Next hop is always the firewall private IP above."),

    # ── ZPA NMO integration ──
    "UDR_ZPA_NMO_NAME":        _f("nmo", "ZPA NMO route table",
                                  help="Hub routing table for the NMO connector — gets a route to each NMO-routed spoke."),
    "ZPA_NMO_CONNECTION_SUBNET": _f("nmo", "NMO connector subnet (CIDR)",
                                    help="Routed into the spoke's UDR so return traffic reaches the NMO connector."),
    "NMO_SUBSCRIPTION_ID":     _f("nmo", "NMO subscription ID",
                                  help="Where the NMO NSG lives. Blank = hub subscription."),
    "NMO_NSG_NAME":            _f("nmo", "NMO NSG name"),
    "NMO_NSG_RG":              _f("nmo", "NMO NSG resource group"),
    "NMO_NSG_ALLOW_RULE":      _f("nmo", "NSG outbound allow rule",
                                  help="Security rule whose destination list receives each spoke CIDR."),
    "NMO_FW_ALLOW_RULE":       _f("nmo", "Firewall ALLOW rule name",
                                  help="Network rule (searched across all rule collection groups) whose destination list receives each spoke CIDR."),
    "NMO_FW_DENY_RULE":        _f("nmo", "Firewall DENY rule name",
                                  help="Deny rule whose destination list receives each spoke CIDR."),

    # ── ZPA connector VMs (Reachability Tester) ──
    "ZPA_RND_VM_HOST": _f("connectors", "R&D connector VM host/IP (primary)",
                          help="Reachable address of the primary R&D ZPA connector VM the checks run from."),
    "ZPA_RND_VM_HOST_2": _f("connectors", "R&D connector VM host/IP (secondary)",
                          help="Optional second R&D ZPA connector VM (HA pair). Shares the SSH user/port/key below."),
    "ZPA_RND_VM_USER": _f("connectors", "R&D connector SSH user", "azureuser"),
    "ZPA_RND_VM_PORT": _f("connectors", "R&D connector SSH port", "22", type="int"),
    "ZPA_RND_VM_KEY":  _f("connectors", "R&D connector SSH private key", secret=True, multiline=True,
                          help="Paste the FULL private key incl. the BEGIN/END lines (PEM or OpenSSH). Stored encrypted. Leave blank on save to keep the current value."),
    "ZPA_NMO_VM_HOST": _f("connectors", "NMO connector VM host/IP (primary)",
                          help="Reachable address of the primary NMO ZPA connector VM the checks run from."),
    "ZPA_NMO_VM_HOST_2": _f("connectors", "NMO connector VM host/IP (secondary)",
                          help="Optional second NMO ZPA connector VM (HA pair). Shares the SSH user/port/key below."),
    "ZPA_NMO_VM_USER": _f("connectors", "NMO connector SSH user", "azureuser"),
    "ZPA_NMO_VM_PORT": _f("connectors", "NMO connector SSH port", "22", type="int"),
    "ZPA_NMO_VM_KEY":  _f("connectors", "NMO connector SSH private key", secret=True, multiline=True,
                          help="Paste the FULL private key incl. the BEGIN/END lines (PEM or OpenSSH). Stored encrypted. Leave blank on save to keep the current value."),
    "ZPA_CONNECTOR_SERVICE": _f("connectors", "ZPA connector service name", "zpa-connector",
                          help="systemd service inspected by the health dashboard's 'More status' (e.g. zpa-connector). Needs the SSH user to be able to run 'systemctl is-active/is-enabled/status <service>'."),

    # ── Peering defaults ──
    "PEERING_ALLOW_VNET_ACCESS":       _f("peering", "Allow virtual network access", "true",  type="bool"),
    "PEERING_ALLOW_FORWARDED_TRAFFIC": _f("peering", "Allow forwarded traffic",      "true",  type="bool"),
    "PEERING_ALLOW_GATEWAY_TRANSIT":   _f("peering", "Allow gateway transit (hub side)", "false", type="bool"),
    "PEERING_USE_REMOTE_GATEWAYS":     _f("peering", "Use remote gateways (spoke side)", "false", type="bool"),

    # ── Naming conventions ──
    "NAME_PREFIX":              _f("naming", "Global prefix", help="Prepended to every generated name (e.g. 'corp'). Blank = none."),
    "NAME_SUFFIX":              _f("naming", "Global suffix", help="Appended to every generated name (e.g. 'prd'). Blank = none."),
    "TPL_PEERING_SPOKE_TO_HUB": _f("naming", "Peering: spoke → hub", "spoke-to-hub"),
    "TPL_PEERING_HUB_TO_SPOKE": _f("naming", "Peering: hub → spoke", "hub-to-{vnet}"),
    "TPL_ROUTE_NAME":           _f("naming", "Route name (hub UDRs)", "to-{vnet}"),
    "TPL_ROUTE_TABLE_NAME":     _f("naming", "Route table name (spoke)", "rt-{vnet}"),
    "TPL_FW_RULE_NAME":         _f("naming", "Firewall rule name", "{vnet}-allow-internet"),

    # ── Teams (requester teams) ──
    "TEAMS":                 _f("teams", "Teams", multiline=True,
                                help="One team per line (or comma-separated). Requesters pick their team when "
                                     "raising a request (mandatory), and can view every ticket raised by that "
                                     "team. Leave blank to disable team selection."),

    # ── Notifications ──
    "TEAMS_WEBHOOK_URL":      _f("notifications", "Teams webhook URL", secret=True,
                                 help="Incoming-webhook URL (treated as a secret)."),
    "SMTP_HOST":              _f("notifications", "SMTP host"),
    "SMTP_PORT":              _f("notifications", "SMTP port", "587", type="int"),
    "SMTP_USER":              _f("notifications", "SMTP user"),
    "SMTP_PASSWORD":          _f("notifications", "SMTP password", secret=True,
                                 help="Stored encrypted. Leave blank on save to keep the current value."),
    "SMTP_FROM":              _f("notifications", "From address", help="Defaults to SMTP user when blank."),
    "SMTP_USE_TLS":           _f("notifications", "Use STARTTLS", "true", type="bool"),
    "NOTIFY_EMAILS":          _f("notifications", "Notification recipients (corporate)",
                                 help="Comma-separated corporate email addresses (e.g. your network-ops "
                                      "distribution list) that receive every request notification, in "
                                      "addition to the requester. Blank = only the requester is emailed."),
    "NOTIFY_AI_DRAFT":        _f("notifications", "AI-draft notification emails", "true", type="bool",
                                 help="When on, notification emails are written by the configured LLM based "
                                      "on the specific case (falls back to templates if the LLM is "
                                      "unavailable). Requires an agent provider under AI Agent settings."),
    "BUDGET_ALERTS_ENABLED":  _f("notifications", "Automatic budget alerts", "false", type="bool",
                                 help="Master switch for the scheduled over-budget checker. When on, "
                                      "subscriptions with auto-alerts enabled (toggle on the Subscriptions "
                                      "page) are checked periodically and their financial owner is emailed "
                                      "at 70% (notify), 80% (warning) and 90%/over (critical) of budget — "
                                      "unless the month-end run-rate forecast lands under budget."),
    "BUDGET_ALERT_INTERVAL_HOURS": _f("notifications", "Budget check interval (hours)", "24", type="int",
                                 help="How often the automatic budget checker runs. Default daily."),
    "SUBNET_FINDER_BASE_URL": _f("notifications", "App base URL", "http://localhost:8080",
                                 help="Used for deep-links in Teams/email notifications."),

    # ── AKS Defaults ──
    # Kubernetes versions, node sizes and the subscription are fetched live from
    # Azure per request, so they are no longer configured here.
    "AKS_DEFAULT_TIER":         _f("aks", "Default cluster tier (pricing)", "Free",
                                   options=["Free", "Standard", "Premium"],
                                   help="Control-plane SKU tier. Free = no uptime SLA (dev/test); "
                                        "Standard ≈ $73/mo with a 99.95% SLA (production); Premium ≈ $438/mo "
                                        "incl. long-term support. Node VMs are billed separately."),
    "AKS_DEFAULT_ZONES":        _f("aks", "Default availability zones", "default",
                                   options=["default", "1", "2", "3", "1,2,3"],
                                   help="Availability zones the node pool spreads across. "
                                        "'default' = no zone pinning (Azure default); '1,2,3' = zone-redundant. "
                                        "Requires the region and node size to support zones."),
    "AKS_DEFAULT_NODE_COUNT":   _f("aks", "Default node count", "2", type="int",
                                   help="Fixed node count when autoscaling is off."),
    "AKS_DEFAULT_MIN_COUNT":    _f("aks", "Autoscale min nodes", "2", type="int"),
    "AKS_DEFAULT_MAX_COUNT":    _f("aks", "Autoscale max nodes", "5", type="int"),
    "AKS_NETWORK_PLUGIN":       _f("aks", "Network plugin", "azure",
                                   options=["azure", "kubenet"], help="Azure CNI. Overlay mode set below."),
    "AKS_NETWORK_PLUGIN_MODE":  _f("aks", "Network plugin mode", "overlay",
                                   options=["overlay", ""], help="'overlay' = Azure CNI Overlay (pods from the pod CIDR)."),
    "AKS_NETWORK_POLICY":       _f("aks", "Network policy engine", "calico",
                                   options=["calico", "azure", "cilium", "none"]),
    "AKS_POD_CIDR":             _f("aks", "Pod CIDR", "10.244.0.0/16"),
    "AKS_SERVICE_CIDR":         _f("aks", "Service CIDR", "10.0.0.0/16"),
    "AKS_DNS_SERVICE_IP":       _f("aks", "DNS service IP", "10.0.0.10",
                                   help="Must sit inside the service CIDR."),
    "AKS_OUTBOUND_TYPE":        _f("aks", "Outbound type", "loadBalancer",
                                   options=["loadBalancer", "userDefinedRouting", "managedNATGateway"]),
    "AKS_LB_SKU":               _f("aks", "Load balancer SKU", "standard",
                                   options=["standard", "basic"]),
    "AKS_PRIVATE_CLUSTER":      _f("aks", "Private cluster", "true", type="bool",
                                   help="Disable public access to the API server (private API endpoint)."),
    "AKS_ENABLE_AAD":           _f("aks", "Microsoft Entra ID auth", "true", type="bool"),
    "AKS_ENABLE_AZURE_RBAC":    _f("aks", "Azure RBAC for Kubernetes", "true", type="bool"),
    "AKS_DISABLE_LOCAL_ACCOUNTS": _f("aks", "Disable local accounts", "true", type="bool"),
    "AKS_CMK_ENCRYPTION":       _f("aks", "Host disk encryption (CMK) by default", "false", type="bool",
                                   help="When on, AKS deployments create a Key Vault + key + Disk Encryption "
                                        "Set for customer-managed-key host disk encryption unless the deploy "
                                        "opts out. Needs azure-mgmt-keyvault/azure-keyvault-keys installed and "
                                        "the automation SP granted Key Vault Contributor (+ AZURE_SP_OBJECT_ID)."),
    "AKS_UPGRADE_CHANNEL":      _f("aks", "Cluster auto-upgrade channel", "patch",
                                   options=["patch", "stable", "rapid", "node-image", "none"]),
    "AKS_NODE_OS_UPGRADE_CHANNEL": _f("aks", "Node OS upgrade channel", "SecurityPatch",
                                      options=["SecurityPatch", "NodeImage", "None", "Unmanaged"]),

    # ── VM Defaults ──
    # SKUs, images, disk types and zones are fetched live from Azure per request
    # (subscription + region scoped), so only guard rails and fallbacks live here.
    "VM_MAX_PER_REQUEST":       _f("vm", "Max VMs per request", "10", type="int",
                                   help="Upper bound on the VM count field in the request form."),
    "VM_DEFAULT_REGION":        _f("vm", "Default region", AKS_STANDARD_REGION,
                                   help="Region pre-selected for new VM requests. A justification is required "
                                        "to deploy outside this region."),
    "VM_ALLOWED_SKU_FAMILIES":  _f("vm", "Allowed SKU families",
                                   help="Comma-separated SKU family prefixes to allow in the size picker "
                                        "(e.g. 'Standard_D,Standard_E'). Blank = every family Azure returns "
                                        "for the chosen subscription/region."),
    "VM_DEFAULT_IMAGES":        _f("vm", "Curated image list", multiline=True,
                                   default="Canonical:ubuntu-24_04-lts:server\n"
                                           "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2\n"
                                           "MicrosoftWindowsServer:WindowsServer:2025-datacenter-azure-edition\n"
                                           "MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition",
                                   help="One 'publisher:offer:sku' per line (or comma-separated), shown as "
                                        "quick picks in the image dropdown — each resolves to its latest "
                                        "version at deploy time. The requester can also type an explicit "
                                        "'publisher:offer:sku:version' to override."),
    "VM_DEFAULT_OS_DISK_TYPE":  _f("vm", "Default OS disk type", "Premium_LRS",
                                   options=VM_OS_DISK_TYPES,
                                   help="PremiumV2_LRS/UltraSSD_LRS are data-disk-only in Azure, so they're not "
                                        "offered here."),
    "VM_DEFAULT_OS_DISK_SIZE_GB": _f("vm", "Default OS disk size (GB)", "128", type="int"),
    "VM_NAME_SUFFIX_DIGITS":    _f("vm", "Name suffix digits", "3", type="int",
                                   help="Zero-padded numeric suffix width for multi-VM names, e.g. 3 -> '-001'."),
    "VM_NAME_ALWAYS_SUFFIXED":  _f("vm", "Always suffix VM names", "true", type="bool",
                                   help="When on (recommended), even a single VM gets the '-001' suffix so a "
                                        "later request against the same base name is predictable. When off, a "
                                        "count-of-1 request is left unsuffixed."),
    "VM_REQUIRE_SSH_KEY":       _f("vm", "Require SSH key (no password auth)", "true", type="bool",
                                   help="When on, password auth isn't offered at all — every VM request uses an "
                                        "SSH public key. When off, requesters may choose password auth, but the "
                                        "password itself is still never captured from them: the admin sets it "
                                        "once at deploy time, and it is never written to the request, the audit "
                                        "trail or the change ledger."),

    # ── Storage Defaults ──
    # Networking/identity/Key Vault options are fetched live from Azure per
    # request (subscription/region scoped), so only guard rails and fallbacks
    # live here. Security posture (TLS, HTTPS-only, shared-key/blob-public-access,
    # infra encryption, default-deny network rules) is hardcoded in the deploy
    # call, not exposed as a setting — see azure_tools.create_storage_account.
    "STORAGE_MAX_CONTAINERS_PER_REQUEST": _f("storage", "Max containers per request", "10", type="int",
                                   help="Upper bound on the container list in the request form."),
    "STORAGE_DEFAULT_REGION":   _f("storage", "Default region", AKS_STANDARD_REGION,
                                   help="Region pre-selected for new storage requests. A justification is "
                                        "required to deploy outside this region."),
    "STORAGE_ALLOWED_SKUS":     _f("storage", "Allowed SKUs",
                                   default=", ".join(STORAGE_SKUS),
                                   help="Comma-separated SKUs offered in the picker. Blank = every SKU "
                                        "Azure supports for the chosen kind."),
    "STORAGE_DEFAULT_KIND":     _f("storage", "Default storage kind", "StorageV2", options=STORAGE_KINDS),
    "STORAGE_DEFAULT_SKU":      _f("storage", "Default SKU", "Standard_LRS", options=STORAGE_SKUS),
    "STORAGE_DEFAULT_ACCESS_TIER": _f("storage", "Default access tier", "Hot", options=STORAGE_ACCESS_TIERS),
    "STORAGE_DEFAULT_PUBLIC_NETWORK_ACCESS": _f("storage", "Default public network access", "Disabled",
                                   options=["Disabled", "Enabled"],
                                   help="Secure-by-default — Private Endpoint is the recommended access path. "
                                        "Requesters can still choose Enabled with an allowed-IP/VNet list."),
    "STORAGE_DEFAULT_CMK_ENCRYPTION": _f("storage", "Customer-managed key (CMK) by default", "false", type="bool",
                                   help="When on, new requests default to CMK encryption (an existing Key "
                                        "Vault/key the requester picks) instead of Microsoft-managed keys."),
    "STORAGE_BLOB_SOFT_DELETE_DAYS": _f("storage", "Default blob soft-delete retention (days)", "30", type="int"),
    "STORAGE_REQUIRE_PRIVATE_ENDPOINT": _f("storage", "Nudge requesters toward Private Endpoint", "true", type="bool",
                                   help="Informational only — shows a recommendation in the form when public "
                                        "network access is enabled. Does not block submission."),

    # ── AI Agent / LLM ──
    "AGENT_PROVIDER":     _f("agent", "Provider", "anthropic",
                             options=["openai", "anthropic", "byom"],
                             help="Pick ONE — only that provider's fields below need to be filled. "
                                  "'Bring your own model' is any on-premise / self-hosted "
                                  "OpenAI-compatible endpoint (Ollama, vLLM, LM Studio…)."),
    "ANTHROPIC_API_KEY":  _f("agent", "Anthropic API key", secret=True,
                             help="Stored encrypted. Leave blank on save to keep the current value."),
    "ANTHROPIC_MODEL":    _f("agent", "Anthropic model", "claude-sonnet-4-6",
                             help="e.g. claude-sonnet-4-6, claude-opus-4-8, claude-haiku-4-5-20251001."),
    "OPENAI_API_KEY":     _f("agent", "OpenAI API key", secret=True,
                             help="Stored encrypted. Optional for on-premise endpoints without auth."),
    "OPENAI_BASE_URL":    _f("agent", "Endpoint / base URL",
                             help="OpenAI: leave blank (or an *.azure.com URL for Azure OpenAI). "
                                  "Bring your own model: REQUIRED — e.g. http://llm.internal:8000/v1."),
    "OPENAI_API_VERSION": _f("agent", "Azure OpenAI API version", "2024-02-15-preview",
                             help="Only used when the endpoint is Azure OpenAI (*.azure.com)."),
    "OPENAI_MODEL":       _f("agent", "Model / deployment name", "gpt-4o",
                             help="OpenAI model, Azure deployment name, or your on-premise model tag (e.g. qwen3:30b)."),

    # ── Advisor (persistent chat) ──
    "ADVISOR_CHAT_HISTORY_ENABLED": _f("advisor", "Enable persistent chat history", "true", type="bool",
                                       help="On: /advisor is the conversation-list-and-resume chat. Off: the "
                                            "original single-shot guided flow, unchanged, no history."),
    "ADVISOR_MAX_MESSAGES_PER_CONVERSATION": _f("advisor", "Max messages per conversation", "200", type="int",
                                                help="Once reached, the conversation is marked complete and the "
                                                     "user is prompted to start a new one — keeps a single "
                                                     "conversation from growing unbounded."),
    "ADVISOR_CONTEXT_WINDOW_TURNS": _f("advisor", "LLM context window (turns)", "20", type="int",
                                       help="How many recent turns are sent to the LLM for classification/"
                                            "narration. Older turns are summarised, not truncated mid-conversation."),
    "ADVISOR_RETENTION_DAYS": _f("advisor", "Conversation retention (days)", "0", type="int",
                                 help="Conversations older than this are no longer listed. 0 = keep indefinitely."),

    # ── Advisor (knowledge base management) ──
    "ADVISOR_KB_MANAGEMENT_ENABLED": _f("advisor", "Enable Knowledge Base management", "true", type="bool",
                                        help="Gates the Settings -> Knowledge Base page and its routes "
                                             "(super-admin only either way). Off hides the tab entirely; "
                                             "the Advisor keeps using whatever KB is currently active."),
    "ADVISOR_KB_STALE_DAYS": _f("advisor", "Pattern staleness threshold (days)", "180", type="int",
                                help="A recommendation citing a pattern whose last_verified date is older "
                                     "than this (or missing) gets a quiet staleness note appended."),
    "ADVISOR_KB_DRIFT_CHECK_ENABLED": _f("advisor", "Enable KB drift check", "true", type="bool",
                                         help="On: Settings -> Knowledge Base can run the LOCAL (KB vs. "
                                              "config.py) and AZURE (live SKU/image checks) drift report."),
    "ADVISOR_KB_DRIFT_SUBSCRIPTION_ID": _f("advisor", "Drift check subscription ID",
                                           help="Subscription the AZURE-source drift checks (SKU/image "
                                                "existence) run against, using the main Azure Credentials "
                                                "already configured above — read-only lookups only."),

    # ── Authentication (Keycloak-ready; not enforced yet) ──
    "AUTH_PROVIDER":          _f("auth", "Auth provider", "local", options=["local", "keycloak"],
                                 help="'keycloak' only stores configuration for now — password login remains active until the OIDC flow is implemented."),
    "KEYCLOAK_SERVER_URL":    _f("auth", "Keycloak server URL", help="e.g. https://keycloak.example.com (base URL, no /auth suffix on modern Keycloak)."),
    "KEYCLOAK_REALM":         _f("auth", "Realm", help="e.g. presight-rnd"),
    "KEYCLOAK_CLIENT_ID":     _f("auth", "Client ID", help="Confidential client for this app, e.g. subnet-manager"),
    "KEYCLOAK_CLIENT_SECRET": _f("auth", "Client secret", secret=True,
                                 help="Stored encrypted. Leave blank on save to keep the current value."),
    "KEYCLOAK_SUPERADMIN_ROLE": _f("auth", "Super-admin role", "subnet-superadmin",
                                   help="Full portal access, including Settings and the Audit trail. Super-admins are also admins."),
    "KEYCLOAK_ADMIN_ROLE":    _f("auth", "Admin role", "subnet-admin",
                                 help="Operational admin — process requests, run Azure actions, revert changes. NO access to Settings or Audit (those need the super-admin role)."),
    "KEYCLOAK_REQUESTER_ROLE": _f("auth", "Requester role", "subnet-requester",
                                  help="Role for the requester portal (or leave open to all authenticated users)."),
    "KEYCLOAK_ALLOCATOR_ROLE": _f("auth", "Subnet-allocator role", "subnet-allocator",
                                  help="Access to the subnet allocator ONLY — find/allocate/release subnets. No request processing, Azure actions, Settings or Audit. Admins already have this."),
    "KEYCLOAK_ITADMIN_ROLE":  _f("auth", "IT-admin role", "it-admin",
                                 help="IT team access to the Reachability Tester (run ping/telnet/curl from the ZPA connector VMs). No other portal access. Super-admins already have this."),

    # ── Approvals (line-manager approval flow) ──
    "APPROVALS_ENABLED":       _f("approvals", "Enable approval flow", "false", type="bool",
                                  help="Master switch. Turning this on runs a dependency check first — if Entra→Keycloak "
                                       "manager mapping isn't detected, it auto-disables and shows what's missing."),
    "APPROVAL_MANAGER_CLAIM":  _f("approvals", "Manager token claim", "manager",
                                  help="Name of the OIDC token claim that carries the requester's line manager "
                                       "(preferably the manager's email). Mapped in Keycloak from the Entra 'manager' attribute."),
    "APPROVAL_FALLBACK_EMAIL": _f("approvals", "Fallback approver email",
                                  help="Who approves when a requester has no line manager on file (or in non-SSO mode). "
                                       "Leave blank to route unresolved approvals to any super-admin."),
    "APPROVAL_PREVENT_SELF":   _f("approvals", "Prevent self-approval", "true", type="bool",
                                  help="Stops the requester (and the deploying admin) from approving their own request, "
                                       "even if they would otherwise be the assigned approver."),
    "APPROVAL_DEFAULT_TIMING": _f("approvals", "Default gate timing", "submission",
                                  options=["submission", "trigger", "both"],
                                  help="When approval is enforced for a 'Required' type unless overridden per type. "
                                       "submission = held right after it's raised; trigger = the actual Azure deploy is "
                                       "blocked until approved; both = held at submission and re-checked at deploy."),
    # Per-request-type policy, stored as JSON: {type: {mode, timing}}. Edited via the
    # matrix on the settings page — mode ∈ none|discretion|required, timing ∈ submission|trigger|both.
    "APPROVAL_POLICY":         _f("approvals", "Per-type policy (JSON)", "", multiline=True,
                                  help="Advanced: raw JSON of the per-type approval policy. Normally edited with the "
                                       "matrix above — every type defaults to 'Not required'."),

    # ── Safety ──
    "AZURE_DRY_RUN": _f("safety", "Dry-run mode (simulate Azure changes)", "true", type="bool",
                        help="ON: every mutating Azure call is simulated. Turn OFF only when ready to make real changes."),
}

# Env-only values — never DB-overridable, never shown in the settings UI.
_ENV_ONLY = {
    "ADMIN_PASSWORD":     ("ADMIN_PASSWORD", "changeme"),
    "SECRET_KEY":         ("FLASK_SECRET_KEY", "change-me-in-production"),
    "DEBUG":              ("FLASK_DEBUG", "false"),
}
_ENV_ONLY_BOOLS = {"DEBUG"}


def _coerce(raw: str, type_: str):
    if type_ == "bool":
        return str(raw).lower() in ("true", "1", "yes")
    if type_ == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    return raw if raw is not None else ""


def resolve(key: str):
    """Effective raw string value + its source: ('override'|'env'|'default')."""
    spec = SETTINGS_SPEC[key]
    override = settings_store.get_override(key)
    if override is not None:
        return override, "override"
    env_val = os.environ.get(key)
    if env_val not in (None, ""):
        return env_val, "env"
    return spec["default"], "default"


class Config:
    """Attribute access resolves live: DB override → env → default."""

    def __getattr__(self, name):
        if name in SETTINGS_SPEC:
            raw, _src = resolve(name)
            val = _coerce(raw, SETTINGS_SPEC[name]["type"])
            if name == "SMTP_FROM" and not val:      # legacy fallback
                val = self.SMTP_USER
            return val
        if name in _ENV_ONLY:
            env, default = _ENV_ONLY[name]
            raw = os.environ.get(env, default)
            return _coerce(raw, "bool") if name in _ENV_ONLY_BOOLS else raw
        raise AttributeError(name)


cfg = Config()


# ── Settings UI view model ──────────────────────────────────────────────────

def settings_view():
    """
    Per-category field list for the settings page. Secret values are never
    included — only whether one is set and its last 4 characters.
    """
    cats = {k: {"key": k, **v, "fields": []} for k, v in CATEGORIES.items()}
    for key, spec in SETTINGS_SPEC.items():
        raw, source = resolve(key)
        field = {
            "key": key, "label": spec["label"], "help": spec["help"],
            "type": spec["type"], "options": spec["options"],
            "secret": spec["secret"], "source": source,
            "default": spec["default"], "multiline": spec.get("multiline", False),
        }
        if spec["secret"]:
            field["value"] = ""
            field["is_set"] = bool(raw)
            field["last4"] = raw[-4:] if raw and len(raw) >= 8 else ""
        else:
            field["value"] = raw
        cats[spec["category"]]["fields"].append(field)
    return list(cats.values())
