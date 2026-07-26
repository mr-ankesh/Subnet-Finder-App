"""
Preview AI-drafted notification emails WITHOUT sending anything (no SMTP needed).

Drafting (notifications._draft_email) is independent of sending
(notifications._send_email), so this renders the exact subject + body the LLM
would produce for representative cases and prints them to the terminal. It also
verifies the safety guard: a malformed / leaked-reasoning draft must fall back to
the plain template rather than emit garbage.

Usage:
    ./.venv/bin/python scripts/preview_notification_email.py

Requires an agent provider configured (AI Agent settings) for real drafts;
otherwise every case just shows its template fallback, which is still useful.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notifications as N          # noqa: E402
from config import cfg             # noqa: E402


class _Req:
    """Minimal stand-in for a SpokeRequest — only what the drafter reads."""
    def __init__(self, **kw):
        self.id = 1
        self.requester_name = "Ankesh Singh"
        self.requester_email = "ankesh@presight.ai"
        self.purpose = ""
        self._type = "Network request"
        self._status = "Submitted"
        self.__dict__.update(kw)

    def type_label(self):
        return self._type

    def status_label(self):
        return self._status


CASES = [
    ("New firewall request",
     _Req(id=101, _type="Firewall Policy", _status="Submitted",
          purpose="Allow G100 dev app to reach internal API"),
     "A new Firewall Policy request was submitted and awaits admin review",
     [{"title": "Source", "value": "10.20.4.0/24"},
      {"title": "Destination", "value": "api.internal.presight.ai:443"},
      {"title": "Action", "value": "Allow"}],
     "[AlMadar 360] Firewall Policy request received — #101",
     "Your Firewall Policy request #101 has been submitted and is awaiting admin review."),

    ("Hub integration requested",
     _Req(id=102, _type="Spoke VNET", _status="VNET Created", purpose="G100 dev landing zone"),
     "The requester provided VNET details and is requesting hub integration; admin action required",
     [{"title": "Subnet", "value": "10.20.4.0/24"},
      {"title": "VNET Name", "value": "vnet-g100-dev-prs-aen-001"},
      {"title": "VPN/ZPA Access", "value": "Yes"}],
     "[AlMadar 360] Hub integration requested — Request #102",
     "Request #102 has provided its VNET details and is requesting hub integration. Admin action is required."),

    ("Hub integration complete",
     _Req(id=102, _type="Spoke VNET", _status="Hub Integrated", purpose="G100 dev landing zone"),
     "Hub integration completed — the spoke VNET is fully onboarded",
     [{"title": "Subnet", "value": "10.20.4.0/24"},
      {"title": "Actions taken", "value": "Peered spoke↔hub; added UDR; linked private DNS"}],
     "[AlMadar 360] Request #102 complete — hub integrated",
     "The spoke VNET (request #102) is fully integrated with the hub. Onboarding is complete."),
]


def _show(label, req, event, facts, fb_subject, fb_body):
    subject, body = N._draft_email(event, req, facts, fb_subject, fb_body)
    is_template = body.startswith(fb_body[:25])
    print("=" * 72)
    print(f"CASE: {label}  (#{req.id} {req.type_label()} → {req.status_label()})")
    print("-" * 72)
    print("Subject:", subject)
    print()
    print(body)
    tag = "template fallback" if is_template else "AI-drafted"
    leak = " — WARNING: reasoning leaked!" if N._looks_unusable(body) else ""
    print(f"\n[{tag}]{leak}\n")


def main():
    cfg.NOTIFY_AI_DRAFT = True
    import netdiag
    print(f"LLM provider: {cfg.AGENT_PROVIDER or '(none)'}   "
          f"available: {netdiag._llm_available()}   "
          f"AI-draft: {cfg.NOTIFY_AI_DRAFT}\n")
    for c in CASES:
        _show(*c)


if __name__ == "__main__":
    main()
