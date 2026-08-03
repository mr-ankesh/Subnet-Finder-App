"""
Turns advisor_kb/composer/request_sequence.yaml into the ordered, wave-based
build plan: which requests to raise, in what order, and — the requirement
that matters most here — under what LABEL.

Two of the KB's request `type` values (`postgres_create`, `app_gateway`) and
one more (`private_endpoint`) are not real models.RequestType values; the
app has no dedicated request type for them yet. A wave table that rendered
these as bare "Other" rows would be useless exactly when it needs to be
actionable, so every request keeps its KB-authored semantic label as the
PRIMARY label, with the real submittable type as secondary detail.
"""
import functools

from advisor.catalog_loader import get_composer_file
from advisor.condition_eval import evaluate_safe, AttrDict
from models import RequestType

_REAL_REQUEST_TYPES = set(RequestType.ALL)

# request `type` values used in request_sequence.yaml that aren't real
# RequestTypes — each maps to the real submittable type plus a short note
# explaining why, shown as secondary detail alongside the KB's own label.
_TYPE_FALLBACK_NOTE = {
    "postgres_create": "submitted as: Other — no dedicated request type yet",
    "app_gateway": "submitted as: Other — no dedicated request type yet",
    "private_endpoint": "created as part of its parent resource's own deployment, not a standalone request",
}


@functools.lru_cache(maxsize=1)
def _sequence() -> dict:
    return get_composer_file("request_sequence.yaml")


def _resolve_request_type(raw_type: str) -> tuple:
    """Returns (submittable_request_type, secondary_note|None)."""
    if raw_type in _REAL_REQUEST_TYPES:
        return raw_type, None
    return RequestType.OTHER, _TYPE_FALLBACK_NOTE.get(
        raw_type, "submitted as: Other — no dedicated request type yet")


def _request_included(req: dict, ns: AttrDict) -> bool:
    if req.get("always"):
        return True
    if "include_if" in req:
        return evaluate_safe(req["include_if"], ns)
    return True  # no gate at all -> always included (e.g. wave 1's New VNET)


def build_waves(answers: dict) -> list:
    """Ordered list of {wave, name, parallel, depends_on, requests: [...]}
    — only waves/requests whose conditions are met for this environment.
    Every request dict carries `label` (KB semantic label, always shown
    first), `type` (raw KB type, for internal reference),
    `submittable_request_type` (a real models.RequestType — RequestType.OTHER
    when no dedicated type exists), and `secondary_note` (None for the 14
    real types, an explanatory string otherwise)."""
    ns = AttrDict(answers)
    waves = []
    for wave in _sequence()["waves"]:
        if "include_if" in wave and not evaluate_safe(wave["include_if"], ns):
            continue
        requests = []
        for req in wave["requests"]:
            if not _request_included(req, ns):
                continue
            submittable, note = _resolve_request_type(req["type"])
            requests.append({
                "label": req["label"],
                "type": req["type"],
                "submittable_request_type": submittable,
                "secondary_note": note,
                "note": req.get("note"),
                "gated_by": req.get("gated_by"),
            })
        if not requests:
            continue
        waves.append({
            "wave": wave["wave"],
            "name": wave["name"],
            "parallel": wave.get("parallel", False),
            "depends_on": wave.get("depends_on"),
            "blocking_for": wave.get("blocking_for"),
            "requests": requests,
        })
    return waves


def critical_path(answers: dict) -> str:
    if answers.get("exposure") == "public_internet":
        return ("InfoSec onboarding → Cloudflare DNS record. That's why it's "
                "raised first in wave 0.")
    return "VNET → shared services → compute. Nothing in this environment is public-facing."


def parallelism_message() -> str:
    return _sequence()["parallelism_summary"]["message_to_user"].strip()
