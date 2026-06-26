"""
Central config — reads from .env (or real env vars injected by Docker/systemd).
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("true", "1", "yes")


class Config:
    # ── Teams ──────────────────────────────────────────────
    TEAMS_WEBHOOK_URL: str = _get("TEAMS_WEBHOOK_URL")

    # ── Email (SMTP) — notifies the individual requester on status updates ──
    SMTP_HOST: str     = _get("SMTP_HOST")
    SMTP_PORT: int     = int(_get("SMTP_PORT", "587") or 587)
    SMTP_USER: str     = _get("SMTP_USER")
    SMTP_PASSWORD: str = _get("SMTP_PASSWORD")
    SMTP_FROM: str     = _get("SMTP_FROM") or _get("SMTP_USER")
    SMTP_USE_TLS: bool = _bool("SMTP_USE_TLS", True)

    # ── AI Agent provider ──────────────────────────────────
    # "anthropic" or "openai" (Azure OpenAI, LM Studio, Ollama, etc.)
    AGENT_PROVIDER: str   = _get("AGENT_PROVIDER", "anthropic")

    # Anthropic
    ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL: str   = _get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # OpenAI / Azure OpenAI / compatible (incl. self-hosted: vLLM, LM Studio, Ollama)
    # For a self-hosted model, set AGENT_PROVIDER=openai, OPENAI_BASE_URL to the
    # server's /v1 endpoint, and OPENAI_MODEL to the served model name.
    OPENAI_API_KEY: str     = _get("OPENAI_API_KEY")
    OPENAI_BASE_URL: str    = _get("OPENAI_BASE_URL")
    OPENAI_API_VERSION: str = _get("OPENAI_API_VERSION", "2024-02-15-preview")
    OPENAI_MODEL: str       = _get("OPENAI_MODEL", "gpt-4o")

    # ── Admin auth ─────────────────────────────────────────
    # Password to access admin pages (/requests, /agent)
    ADMIN_PASSWORD: str = _get("ADMIN_PASSWORD", "changeme")

    # ── Azure Service Principal ────────────────────────────
    AZURE_CLIENT_ID:     str = _get("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET: str = _get("AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID:     str = _get("AZURE_TENANT_ID")

    # ── Azure Hub / Spoke topology ─────────────────────────
    HUB_SUBSCRIPTION_ID:   str = _get("HUB_SUBSCRIPTION_ID")
    HUB_RESOURCE_GROUP:    str = _get("HUB_RESOURCE_GROUP")
    HUB_VNET_NAME:         str = _get("HUB_VNET_NAME")
    SPOKE_SUBSCRIPTION_ID: str = _get("SPOKE_SUBSCRIPTION_ID")

    # ── VNET Peering defaults (applied to all spokes unless overridden) ────
    PEERING_ALLOW_VNET_ACCESS:      bool = _bool("PEERING_ALLOW_VNET_ACCESS",      True)
    PEERING_ALLOW_FORWARDED_TRAFFIC: bool = _bool("PEERING_ALLOW_FORWARDED_TRAFFIC", True)
    PEERING_ALLOW_GATEWAY_TRANSIT:  bool = _bool("PEERING_ALLOW_GATEWAY_TRANSIT",  False)
    PEERING_USE_REMOTE_GATEWAYS:    bool = _bool("PEERING_USE_REMOTE_GATEWAYS",    False)

    # ── UDR tables (hub UDRs that get spoke route updates) ────────────────
    UDR_NAME_1:        str = _get("UDR_NAME_1")
    UDR_NAME_2:        str = _get("UDR_NAME_2")
    UDR_RESOURCE_GROUP: str = _get("UDR_RESOURCE_GROUP")

    # ── Default region for new Azure resources ─────────────
    DEFAULT_AZURE_REGION: str = _get("DEFAULT_AZURE_REGION", "uaenorth")

    # Hub Azure Firewall private IP — next hop for spoke routes (default route).
    HUB_FIREWALL_PRIVATE_IP: str = _get("HUB_FIREWALL_PRIVATE_IP", "10.110.2.4")

    # Hub routing tables that get a route to each new spoke during onboarding.
    UDR_GATEWAY_NAME: str = _get("UDR_GATEWAY_NAME")   # gateway routing table
    UDR_ZPA_NAME:     str = _get("UDR_ZPA_NAME")       # ZPA routing table

    # ── Azure Firewall Policy ──────────────────────────────
    FIREWALL_POLICY_NAME:           str = _get("FIREWALL_POLICY_NAME")
    FIREWALL_POLICY_RG:             str = _get("FIREWALL_POLICY_RG")
    FIREWALL_RULE_COLLECTION_GROUP: str = _get("FIREWALL_RULE_COLLECTION_GROUP")

    # ── Flask ──────────────────────────────────────────────
    SECRET_KEY: str = _get("FLASK_SECRET_KEY", "change-me-in-production")
    # Werkzeug debugger / auto-reloader. Handy in dev; never enable when the
    # app is reachable by anyone else (the debugger allows remote code execution).
    DEBUG: bool = _bool("FLASK_DEBUG", False)

    # ── App base URL (used in Teams notification deep-links) ──
    SUBNET_FINDER_BASE_URL: str = _get("SUBNET_FINDER_BASE_URL", "http://localhost:8080")


cfg = Config()
