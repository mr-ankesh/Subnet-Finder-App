"""
Keycloak (OIDC) integration — Authlib.

Kept deliberately thin: the OIDC callback only has to populate the two
session keys the rest of the app already uses for authorization —
`session["is_admin"]` (guarded by require_admin) and `session["admin_name"]`
(read by current_actor for the audit/change ledger). Everything else works
unchanged.

Config resolves live from settings (DB → env → default), so the OIDC client
is rebuilt whenever the Keycloak settings change — no restart needed.
"""
import base64
import json
import logging

from authlib.integrations.flask_client import OAuth
from config import cfg

log = logging.getLogger(__name__)

_app = None
_oauth = None
_fingerprint = None


def init_oidc(app):
    """Remember the Flask app; the client is built lazily on first use."""
    global _app
    _app = app


def enabled() -> bool:
    """True only when Keycloak is selected AND minimally configured."""
    return bool(cfg.AUTH_PROVIDER == "keycloak"
                and cfg.KEYCLOAK_SERVER_URL and cfg.KEYCLOAK_REALM
                and cfg.KEYCLOAK_CLIENT_ID)


def _metadata_url() -> str:
    base = (cfg.KEYCLOAK_SERVER_URL or "").rstrip("/")
    return f"{base}/realms/{cfg.KEYCLOAK_REALM}/.well-known/openid-configuration"


def client():
    """The registered Keycloak OAuth client, rebuilt when settings change."""
    global _oauth, _fingerprint
    fp = (cfg.KEYCLOAK_SERVER_URL, cfg.KEYCLOAK_REALM,
          cfg.KEYCLOAK_CLIENT_ID, cfg.KEYCLOAK_CLIENT_SECRET)
    if _oauth is None or _fingerprint != fp:
        _fingerprint = fp
        _oauth = OAuth()
        _oauth.init_app(_app)
        _oauth.register(
            name="keycloak",
            client_id=cfg.KEYCLOAK_CLIENT_ID,
            client_secret=cfg.KEYCLOAK_CLIENT_SECRET,
            server_metadata_url=_metadata_url(),
            client_kwargs={"scope": "openid profile email"},
        )
    return _oauth.keycloak


def _decode_jwt_payload(jwt: str) -> dict:
    """Read a JWT's claims WITHOUT verifying — used only on tokens we just
    obtained ourselves over TLS via the code flow (roles often live in the
    access token rather than the ID token)."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def roles_from_token(token: dict) -> set:
    """Union of realm roles and this client's roles, from ID + access token."""
    roles = set()
    for src in (token.get("userinfo") or {},
                _decode_jwt_payload(token.get("access_token", "") or "")):
        roles |= set((src.get("realm_access") or {}).get("roles") or [])
        roles |= set(((src.get("resource_access") or {})
                      .get(cfg.KEYCLOAK_CLIENT_ID, {})).get("roles") or [])
    return roles


def end_session_url(post_logout_uri: str, id_token: str = None) -> str:
    """Keycloak RP-initiated logout URL."""
    base = (cfg.KEYCLOAK_SERVER_URL or "").rstrip("/")
    url = (f"{base}/realms/{cfg.KEYCLOAK_REALM}/protocol/openid-connect/logout"
           f"?client_id={cfg.KEYCLOAK_CLIENT_ID}"
           f"&post_logout_redirect_uri={post_logout_uri}")
    if id_token:
        url += f"&id_token_hint={id_token}"
    return url
