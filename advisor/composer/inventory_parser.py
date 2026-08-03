"""
Parses a free-text environment inventory ("10 VMs, 1 AKS cluster and a
managed PostgreSQL") into structured counts.

This is inherently fuzzy — a regex pass over natural language — so it is
NEVER trusted silently. environment_questions.yaml's `resource_inventory`
question sets `confirm_back: true` for exactly this reason: the parse is
always shown back to the user before anything is designed from it. A wrong
number here is caught by the human, not baked into the arithmetic.
"""
import re

# Up to 2 intervening descriptive words are tolerated between the count and
# the noun ("1 managed PostgreSQL", "a small AKS cluster") — non-greedy, and
# restricted to letters-only tokens immediately followed by whitespace, so a
# comma or another digit naturally stops the skip rather than bleeding into
# the next clause ("10 VMs, 1 AKS cluster" never lets "10" reach "cluster").
_GAP = r"(?:[a-zA-Z]+\s+){0,2}?"

_NOUNS = {
    "vm_count": r"(?:virtual machines?|vms?)",
    "aks_count": r"(?:aks|kubernetes)(?:\s+clusters?)?",
    "postgres_count": r"(?:postgres(?:ql)?|databases?)",
    "storage_count": r"(?:storage accounts?|blob storage|file shares?)",
    "appgw_count": r"(?:app(?:lication)?\s*gateways?|appgw|waf)",
}
# A bare "cluster"/"clusters" with no AKS/Kubernetes qualifier still means
# one AKS cluster in this app's vocabulary — there's no other cluster type a
# requester would mean here.
_BARE_CLUSTER_NOUN = r"clusters?"

_KNOWN_NOUN_RE = re.compile(
    r"\b(?:virtual machines?|vms?|aks|kubernetes|clusters?|postgres(?:ql)?|databases?|"
    r"storage accounts?|blob storage|file shares?|app(?:lication)?\s*gateways?|appgw|waf)\b",
    re.IGNORECASE)


def _counted_pattern(noun: str) -> re.Pattern:
    return re.compile(rf"(\d+)\s*(?:x\s*)?{_GAP}{noun}\b", re.IGNORECASE)


def _singular_pattern(noun: str) -> re.Pattern:
    return re.compile(rf"\ban?\s+{_GAP}{noun}\b", re.IGNORECASE)


_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
}
_WORD_NUMBER_RE = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", re.IGNORECASE)


def _normalize_word_numbers(text: str) -> str:
    return _WORD_NUMBER_RE.sub(lambda m: _WORD_NUMBERS[m.group(1).lower()], text)


def _count_for(text: str, noun: str) -> int:
    total = 0
    matched_any = False
    for m in _counted_pattern(noun).finditer(text):
        matched_any = True
        total += int(m.group(1))
    if matched_any:
        return total
    return 1 if _singular_pattern(noun).search(text) else 0


def parse_inventory(text: str) -> dict:
    """Best-effort structured parse. Always 0 for anything not mentioned —
    never guesses a nonzero count. `other_services` collects noun phrases
    that don't match any known service keyword, surfaced so the confirm-back
    step can show the user what was NOT understood, not just what was."""
    text = _normalize_word_numbers(text or "")

    parsed = {key: _count_for(text, noun) for key, noun in _NOUNS.items()}
    parsed["aks_count"] = max(parsed["aks_count"], _count_for(text, _BARE_CLUSTER_NOUN))

    other = []
    for chunk in re.split(r",|;|\band\b", text):
        chunk = chunk.strip(" .")
        if not chunk or re.search(r"\d", chunk) is None:
            continue
        if not _KNOWN_NOUN_RE.search(chunk):
            other.append(chunk)
    parsed["other_services"] = other

    return parsed


def format_confirmation(parsed: dict, template: str) -> str:
    """Fills environment_questions.yaml's `confirm_template`'s
    {parsed_inventory} placeholder with a human-readable summary."""
    labels = [
        ("vm_count", "VM", "VMs"),
        ("aks_count", "AKS cluster", "AKS clusters"),
        ("postgres_count", "PostgreSQL Flexible Server", "PostgreSQL Flexible Servers"),
        ("storage_count", "Storage Account", "Storage Accounts"),
        ("appgw_count", "Application Gateway", "Application Gateways"),
    ]
    parts = []
    for key, singular, plural in labels:
        n = parsed.get(key, 0)
        if n > 0:
            parts.append(f"{n} {singular if n == 1 else plural}")
    for extra in parsed.get("other_services") or []:
        parts.append(extra)

    summary = ", ".join(parts) if parts else "nothing yet — could you list what you need?"
    return template.replace("{parsed_inventory}", summary)
