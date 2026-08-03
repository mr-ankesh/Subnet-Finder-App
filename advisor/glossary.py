"""
Loads advisor_kb/glossary.yaml — the grounding source for "what does X
mean?" free-form questions. This module does no interpretation beyond
term/alias lookup; the definitions themselves (plain_english/at_presight/
why_it_matters) are never generated, only matched and returned verbatim
for advisor/freeform.py to narrate.
"""
import functools
import re

from advisor.catalog_loader import load_yaml_file

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


@functools.lru_cache(maxsize=1)
def _glossary() -> dict:
    return load_yaml_file("glossary.yaml")


@functools.lru_cache(maxsize=1)
def _index() -> dict:
    """normalized alias/term text -> term dict. Longer keys first isn't
    needed here since lookup is exact-normalized-match, not substring, on
    the primary path (see find_term)."""
    idx = {}
    for term in _glossary()["terms"]:
        idx[_normalize(term["term"])] = term
        for alias in term.get("aliases") or []:
            idx[_normalize(alias)] = term
    return idx


def all_terms() -> list:
    return list(_glossary()["terms"])


def find_term(query: str) -> dict:
    """Matches `query` against every term name and alias. Whole-phrase
    match on the normalized query first (so "what's an HSM?" resolves via
    the normalized token "hsm" to RSA_HSM); falls back to checking whether
    any known term/alias appears as a whole word inside the query, for
    questions phrased as full sentences ("what does a private endpoint
    do?"). Returns None — never a guess — when nothing matches, so the
    caller can honestly report "outside what I know"."""
    norm_query = _normalize(query)
    if not norm_query:
        return None

    index = _index()
    if norm_query in index:
        return index[norm_query]

    query_words = set(norm_query.split())
    best = None
    best_len = 0
    for key, term in index.items():
        key_words = key.split()
        if not key_words:
            continue
        if all(w in query_words for w in key_words) and len(key_words) > best_len:
            # Prefer the longest matching alias/term ("private endpoint" over
            # a shorter unrelated single-word overlap).
            best, best_len = term, len(key_words)
    return best
