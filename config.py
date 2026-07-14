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


# ── UI categories (tab order) ───────────────────────────────────────────────

CATEGORIES = {
    "credentials":   {"title": "Azure Credentials",  "desc": "Identity used for all Azure operations. Needs Network Contributor on hub & spoke scopes."},
    "hub":           {"title": "Hub & Subscriptions", "desc": "Hub VNET topology and default subscriptions/region for new spokes."},
    "firewall":      {"title": "Firewall",            "desc": "Azure Firewall policy that receives spoke egress rules."},
    "routing":       {"title": "Routing / UDRs",      "desc": "Hub route tables updated when a spoke is onboarded."},
    "peering":       {"title": "Peering Defaults",    "desc": "Defaults applied to hub↔spoke peerings (overridable per action)."},
    "naming":        {"title": "Naming Conventions",  "desc": "Templates for generated resource names. Placeholders: {vnet} {request_id} {region} {cidr_mask} {purpose} {date}. Global prefix/suffix are joined with '-'."},
    "notifications": {"title": "Notifications",       "desc": "Teams and email notifications for request lifecycle events."},
    "auth":          {"title": "Authentication",      "desc": "Keycloak-ready SSO configuration. Values are stored now; enforcement ships with the integration (see docs/KEYCLOAK.md for the step-by-step guide) — login stays password-based until then."},
    "safety":        {"title": "Safety",              "desc": "Guard rails for Azure execution."},
}


def _f(category, label, default="", type="str", secret=False, help="", options=None):
    return {"category": category, "label": label, "default": default,
            "type": type, "secret": secret, "help": help, "options": options}


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

    # ── Hub & Subscriptions ──
    "HUB_SUBSCRIPTION_ID":   _f("hub", "Hub subscription ID"),
    "HUB_RESOURCE_GROUP":    _f("hub", "Hub resource group"),
    "HUB_VNET_NAME":         _f("hub", "Hub VNET name"),
    "SPOKE_SUBSCRIPTION_ID": _f("hub", "Default spoke subscription ID",
                                help="Used when a request doesn't specify its own subscription."),
    "DEFAULT_AZURE_REGION":  _f("hub", "Default region", "uaenorth"),

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
    "SUBNET_FINDER_BASE_URL": _f("notifications", "App base URL", "http://localhost:8080",
                                 help="Used for deep-links in Teams/email notifications."),

    # ── Authentication (Keycloak-ready; not enforced yet) ──
    "AUTH_PROVIDER":          _f("auth", "Auth provider", "local", options=["local", "keycloak"],
                                 help="'keycloak' only stores configuration for now — password login remains active until the OIDC flow is implemented."),
    "KEYCLOAK_SERVER_URL":    _f("auth", "Keycloak server URL", help="e.g. https://keycloak.example.com (base URL, no /auth suffix on modern Keycloak)."),
    "KEYCLOAK_REALM":         _f("auth", "Realm", help="e.g. presight-rnd"),
    "KEYCLOAK_CLIENT_ID":     _f("auth", "Client ID", help="Confidential client for this app, e.g. subnet-manager"),
    "KEYCLOAK_CLIENT_SECRET": _f("auth", "Client secret", secret=True,
                                 help="Stored encrypted. Leave blank on save to keep the current value."),
    "KEYCLOAK_ADMIN_ROLE":    _f("auth", "Admin role", "subnet-admin",
                                 help="Realm/client role that maps to admin access."),
    "KEYCLOAK_REQUESTER_ROLE": _f("auth", "Requester role", "subnet-requester",
                                  help="Role for the requester portal (or leave open to all authenticated users)."),

    # ── Safety ──
    "AZURE_DRY_RUN": _f("safety", "Dry-run mode (simulate Azure changes)", "true", type="bool",
                        help="ON: every mutating Azure call is simulated. Turn OFF only when ready to make real changes."),
}

# Env-only values — never DB-overridable, never shown in the settings UI.
_ENV_ONLY = {
    "AGENT_PROVIDER":     ("AGENT_PROVIDER", "anthropic"),
    "ANTHROPIC_API_KEY":  ("ANTHROPIC_API_KEY", ""),
    "ANTHROPIC_MODEL":    ("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    "OPENAI_API_KEY":     ("OPENAI_API_KEY", ""),
    "OPENAI_BASE_URL":    ("OPENAI_BASE_URL", ""),
    "OPENAI_API_VERSION": ("OPENAI_API_VERSION", "2024-02-15-preview"),
    "OPENAI_MODEL":       ("OPENAI_MODEL", "gpt-4o"),
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
            "default": spec["default"],
        }
        if spec["secret"]:
            field["value"] = ""
            field["is_set"] = bool(raw)
            field["last4"] = raw[-4:] if raw and len(raw) >= 8 else ""
        else:
            field["value"] = raw
        cats[spec["category"]]["fields"].append(field)
    return list(cats.values())
