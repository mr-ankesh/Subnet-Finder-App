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

    # ── AI Agent provider ──────────────────────────────────
    # "anthropic" or "openai" (Azure OpenAI, LM Studio, Ollama, etc.)
    AGENT_PROVIDER: str   = _get("AGENT_PROVIDER", "anthropic")

    # Anthropic
    ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")

    # OpenAI / Azure OpenAI / compatible
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

    # ── Azure Firewall Policy ──────────────────────────────
    FIREWALL_POLICY_NAME:           str = _get("FIREWALL_POLICY_NAME")
    FIREWALL_POLICY_RG:             str = _get("FIREWALL_POLICY_RG")
    FIREWALL_RULE_COLLECTION_GROUP: str = _get("FIREWALL_RULE_COLLECTION_GROUP")

    # ── Flask ──────────────────────────────────────────────
    SECRET_KEY: str = _get("FLASK_SECRET_KEY", "change-me-in-production")


cfg = Config()
