"""
The service-selection menu — the literal first question of every advisor
conversation, asked before any per-service question bank is even loaded
(question banks are only known once the service is chosen — see
catalog_loader.SERVICE_FILES).

Key Vault is deliberately excluded: the KB ships a `keyvault_premium_private`
catalog pattern but no question/rules/mapping files, so it's reference-only
this round (other patterns can cite it; it's not its own guided
conversation). "A whole environment" (advisor_kb/composer/) is Phase 3 —
also excluded.
"""

SERVICES = [
    {"id": "storage_account", "label": "Storage",
     "keywords": ("storage", "blob", "file share", "fileshare", "container", "bucket")},
    {"id": "aks_cluster", "label": "Kubernetes cluster",
     "keywords": ("cluster", "kubernetes", "aks", "k8s")},
    {"id": "vm_create", "label": "Virtual machines",
     "keywords": ("vm", "virtual machine", "server", "compute")},
    {"id": "postgres_create", "label": "Database",
     "keywords": ("database", "db", "postgres", "postgresql", "sql")},
    {"id": "app_gateway", "label": "Application gateway",
     "keywords": ("gateway", "appgw", "ingress", "waf", "load balancer")},
]

SERVICE_QUESTION_ID = "_service_select"

SERVICE_QUESTION = {
    "id": SERVICE_QUESTION_ID,
    "question": "What are you looking to set up?",
    "type": "single_choice",
    "options": [{"value": s["id"], "label": s["label"]} for s in SERVICES],
    "why_we_ask": "",
}

_VALID_IDS = {s["id"] for s in SERVICES}


def classify_free_text(text: str):
    """Deterministic keyword fallback for routing free text to a service —
    works with zero LLM configured, same guarantee already proven for the
    storage build's option classification."""
    low = (text or "").lower()
    for s in SERVICES:
        if any(k in low for k in s["keywords"]):
            return s["id"]
    return None


def is_valid(service_id) -> bool:
    return service_id in _VALID_IDS
