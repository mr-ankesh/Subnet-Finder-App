"""
Loads the 3 system prompts verbatim from
advisor_kb/templates/recommendation_template.md, and provides a single-turn
LLM call for the advisor's narrow needs (classify / explain / ask).

Unlike agent_requester.py / agent_admin.py, there is no tool-calling loop
here — the advisor's LLM usage is always "given this fixed data, produce
this text," never "go look things up." _get_client() mirrors those modules'
provider branching exactly (same cfg.AGENT_PROVIDER settings, no new
settings added), but call_llm() is a single completion, not a loop.
"""
import functools
import logging
import re

from config import cfg
from advisor.catalog_loader import load_text_file

log = logging.getLogger(__name__)

_STAGE_RE = re.compile(
    r"## System prompt — (\w+) stage\s*\n+```text\n(.*?)```", re.DOTALL)


@functools.lru_cache(maxsize=1)
def get_system_prompts() -> dict:
    """{"classification": "...", "explanation": "...", "question": "..."}"""
    text = load_text_file("templates/recommendation_template.md")
    prompts = {m.group(1): m.group(2).strip() for m in _STAGE_RE.finditer(text)}
    expected = {"classification", "explanation", "question"}
    missing = expected - prompts.keys()
    if missing:
        raise ValueError(f"recommendation_template.md is missing system prompt(s): {missing}")
    return prompts


@functools.lru_cache(maxsize=1)
def get_recommendation_template() -> str:
    return load_text_file("templates/recommendation_template.md")


_client = None
_client_fingerprint = None


def _get_client():
    global _client, _client_fingerprint
    fingerprint = (cfg.AGENT_PROVIDER, cfg.ANTHROPIC_API_KEY, cfg.OPENAI_API_KEY,
                   cfg.OPENAI_BASE_URL, cfg.OPENAI_API_VERSION)
    if _client is not None and _client_fingerprint == fingerprint:
        return _client
    _client_fingerprint = fingerprint
    provider = cfg.AGENT_PROVIDER.lower()
    if provider == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    elif provider in ("openai", "byom"):
        from openai import AzureOpenAI, OpenAI
        if provider == "byom" and not cfg.OPENAI_BASE_URL:
            raise RuntimeError("Bring-your-own-model needs an endpoint URL — "
                               "set it in Settings → AI Agent / LLM.")
        if cfg.OPENAI_BASE_URL and "azure.com" in cfg.OPENAI_BASE_URL:
            _client = AzureOpenAI(azure_endpoint=cfg.OPENAI_BASE_URL, api_key=cfg.OPENAI_API_KEY,
                                   api_version=cfg.OPENAI_API_VERSION)
        else:
            kwargs = {"api_key": cfg.OPENAI_API_KEY or "not-needed", "timeout": 60}
            if cfg.OPENAI_BASE_URL:
                kwargs["base_url"] = cfg.OPENAI_BASE_URL
            _client = OpenAI(**kwargs)
    else:
        raise RuntimeError(f"Unknown AGENT_PROVIDER '{provider}'.")
    return _client


def call_llm(system_prompt: str, user_content: str) -> str:
    """One completion, no tools, no loop. Raises on failure — callers (the
    advisor routes) are responsible for catching and falling back; this
    function doesn't swallow errors itself so a real misconfiguration is
    still visible in logs."""
    provider = cfg.AGENT_PROVIDER.lower()
    client = _get_client()
    if provider == "anthropic":
        response = client.messages.create(
            model=cfg.ANTHROPIC_MODEL, max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(b.text for b in response.content if hasattr(b, "text"))
    response = client.chat.completions.create(
        model=cfg.OPENAI_MODEL,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_content}],
    )
    return response.choices[0].message.content or ""
