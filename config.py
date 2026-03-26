"""
Central config — reads from .env (or real env vars injected by Docker/systemd).
Import `cfg` everywhere instead of calling os.environ directly.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Required env var '{key}' is not set. Check your .env file.")
    return val


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Config:
    # ── Teams ──────────────────────────────────────────────
    TEAMS_WEBHOOK_URL: str = _get("TEAMS_WEBHOOK_URL")

    # ── Subnet Finder (basic-auth) ─────────────────────────
    SUBNET_FINDER_BASE_URL: str = _get("SUBNET_FINDER_BASE_URL", "https://azsubnetfinder.presight.ai")
    SUBNET_FINDER_USER: str     = _get("SUBNET_FINDER_USER")
    SUBNET_FINDER_PASS: str     = _get("SUBNET_FINDER_PASS")

    # ── AI Agent provider ──────────────────────────────────
    # Set AGENT_PROVIDER to either "anthropic" or "openai"
    # "openai" works for: Azure OpenAI, any OpenAI-compatible endpoint (LM Studio, Ollama, etc.)
    AGENT_PROVIDER: str    = _get("AGENT_PROVIDER", "anthropic")

    # Anthropic
    ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")

    # OpenAI / Azure OpenAI / compatible
    OPENAI_API_KEY: str      = _get("OPENAI_API_KEY")
    OPENAI_BASE_URL: str     = _get("OPENAI_BASE_URL")        # e.g. https://YOUR.openai.azure.com/openai/deployments/gpt-4o
    OPENAI_API_VERSION: str  = _get("OPENAI_API_VERSION", "2024-02-15-preview")  # Azure only
    OPENAI_MODEL: str        = _get("OPENAI_MODEL", "gpt-4o") # model / deployment name

    # ── Azure Service Principal ────────────────────────────
    AZURE_CLIENT_ID:     str = _get("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET: str = _get("AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID:     str = _get("AZURE_TENANT_ID")

    # ── Azure Hub / Spoke topology ─────────────────────────
    HUB_SUBSCRIPTION_ID:   str = _get("HUB_SUBSCRIPTION_ID")
    HUB_RESOURCE_GROUP:    str = _get("HUB_RESOURCE_GROUP")
    HUB_VNET_NAME:         str = _get("HUB_VNET_NAME")
    SPOKE_SUBSCRIPTION_ID: str = _get("SPOKE_SUBSCRIPTION_ID")

    # ── UDR tables (two that get route updates) ────────────
    UDR_NAME_1:        str = _get("UDR_NAME_1")
    UDR_NAME_2:        str = _get("UDR_NAME_2")
    UDR_RESOURCE_GROUP: str = _get("UDR_RESOURCE_GROUP")

    # ── Azure Firewall Policy ──────────────────────────────
    FIREWALL_POLICY_NAME:            str = _get("FIREWALL_POLICY_NAME")
    FIREWALL_POLICY_RG:              str = _get("FIREWALL_POLICY_RG")
    FIREWALL_RULE_COLLECTION_GROUP:  str = _get("FIREWALL_RULE_COLLECTION_GROUP")

    # ── Flask ──────────────────────────────────────────────
    SECRET_KEY: str = _get("FLASK_SECRET_KEY", "change-me-in-production")


cfg = Config()
