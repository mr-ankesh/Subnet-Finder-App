"""
Scores the catalog against captured answers, per advisor_kb/catalog/_schema.md:

  Exclude any pattern whose match.disqualify matches, or whose match.required
  is unsatisfied. Score the remainder by counting satisfied match.preferred
  entries. Highest score wins. On a tie, prefer status approved over
  conditional, then catalog declaration order. If the winning score is 0
  (only reachable when the tie itself is at 0 — a lone survivor with score 0
  still wins outright, there's nothing to disambiguate), ask the
  disambiguating question under rules.tiebreak_questions instead of guessing.
"""


def _matches(value, criteria) -> bool:
    """True if `value` (a single answer, or a list for multi_choice questions)
    intersects the pattern's listed criteria values."""
    if value is None:
        return False
    if isinstance(value, list):
        return bool(set(value) & set(criteria))
    return value in criteria


def _passes_required(pattern: dict, answers: dict) -> bool:
    required = pattern.get("match", {}).get("required", {})
    return all(_matches(answers.get(field), criteria) for field, criteria in required.items())


def _is_disqualified(pattern: dict, answers: dict) -> bool:
    disqualify = pattern.get("match", {}).get("disqualify", {})
    return any(_matches(answers.get(field), criteria) for field, criteria in disqualify.items())


def _preferred_score(pattern: dict, answers: dict) -> int:
    preferred = pattern.get("match", {}).get("preferred", {})
    return sum(1 for field, criteria in preferred.items() if _matches(answers.get(field), criteria))


def score(catalog: dict, answers: dict) -> dict:
    """Returns one of:
      {"outcome": "no_match", "winner": None, "candidates": []}
        -- nothing passed required/disqualify at all.
      {"outcome": "tie_zero", "winner": None, "tied": [id, ...], "candidates": [...]}
        -- 2+ patterns tied at the top and that top score is 0; ask a
           tiebreak question rather than guess.
      {"outcome": "matched" | "matched_tiebreak_status" | "matched_tiebreak_order",
       "winner": id, "candidates": [...]}
    """
    candidates = []
    for pattern_id, pattern in catalog.items():  # dict preserves catalog file order
        if _is_disqualified(pattern, answers):
            continue
        if not _passes_required(pattern, answers):
            continue
        candidates.append({
            "id": pattern_id,
            "score": _preferred_score(pattern, answers),
            "status": pattern.get("status"),
        })

    if not candidates:
        return {"outcome": "no_match", "winner": None, "candidates": []}

    max_score = max(c["score"] for c in candidates)
    top = [c for c in candidates if c["score"] == max_score]

    if len(top) == 1:
        return {"outcome": "matched", "winner": top[0]["id"], "candidates": candidates}

    if max_score == 0:
        return {"outcome": "tie_zero", "winner": None,
                "tied": [t["id"] for t in top], "candidates": candidates}

    approved = [t for t in top if t["status"] == "approved"]
    if len(approved) == 1:
        return {"outcome": "matched_tiebreak_status", "winner": approved[0]["id"],
                "candidates": candidates}

    # Still tied (multiple approved, or none) -> first in catalog declaration
    # order among the tied set (`top` preserves that order already).
    return {"outcome": "matched_tiebreak_order", "winner": top[0]["id"], "candidates": candidates}
