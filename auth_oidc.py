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


def groups_from_token(token: dict) -> list:
    """Keycloak group names from the 'groups' claim (ID + access token + userinfo).
    A 'Group Membership' client mapper must add this claim. Handles both the leaf
    form ('Alpha Team') and the full-path form ('/Parent/Alpha Team') → 'Alpha Team'."""
    out, seen = [], set()
    for src in (token.get("userinfo") or {},
                _decode_jwt_payload(token.get("id_token", "") or ""),
                _decode_jwt_payload(token.get("access_token", "") or "")):
        for g in (src.get("groups") or []):
            name = str(g).strip().rstrip("/").split("/")[-1].strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


def test_connection() -> dict:
    """
    Diagnose SSO reachability by fetching the OIDC discovery document — the
    exact step /auth/login performs. Reports the specific failure so the
    admin can fix it from the Settings UI. Uses the currently SAVED settings.
    """
    if cfg.AUTH_PROVIDER != "keycloak":
        return {"success": False,
                "message": "Set 'Auth provider' to keycloak and Save first."}
    missing = [n for n, v in (("server URL", cfg.KEYCLOAK_SERVER_URL),
                              ("realm", cfg.KEYCLOAK_REALM),
                              ("client ID", cfg.KEYCLOAK_CLIENT_ID)) if not v]
    if missing:
        return {"success": False, "message": "Missing (save these first): " + ", ".join(missing)}

    url = _metadata_url()
    import requests
    try:
        r = requests.get(url, timeout=8)
    except requests.exceptions.SSLError as exc:
        return {"success": False, "url": url,
                "message": f"TLS/certificate error reaching Keycloak. If it uses a private or "
                           f"self-signed cert, the app container must trust it. ({exc})"}
    except requests.exceptions.ConnectionError as exc:
        return {"success": False, "url": url,
                "message": f"Cannot connect to {url}. Check the server URL and that the pod can "
                           f"reach Keycloak (DNS / NetworkPolicy / firewall). Is Keycloak "
                           f"internal-only? ({str(exc)[:160]})"}
    except requests.exceptions.Timeout:
        return {"success": False, "url": url,
                "message": f"Timed out reaching {url} — Keycloak is not reachable from the cluster."}
    except Exception as exc:
        return {"success": False, "url": url, "message": f"Error reaching {url}: {exc}"}

    if r.status_code == 404:
        return {"success": False, "url": url,
                "message": f"404 at {url}. Realm '{cfg.KEYCLOAK_REALM}' not found, OR the server "
                           f"URL has the wrong path: modern Keycloak has NO '/auth' suffix; "
                           f"legacy Keycloak (≤16) DOES need '/auth'."}
    if r.status_code != 200:
        return {"success": False, "url": url,
                "message": f"HTTP {r.status_code} from {url}."}
    try:
        meta = r.json()
    except Exception:
        return {"success": False, "url": url,
                "message": f"{url} did not return JSON — that URL is not a Keycloak realm "
                           f"discovery endpoint. Double-check the server URL and realm."}

    issuer = meta.get("issuer", "")
    n_ep = sum(1 for k in meta if k.endswith("_endpoint"))
    return {"success": True, "url": url, "issuer": issuer,
            "message": f"Reached Keycloak ✓  Realm issuer: {issuer}  ({n_ep} endpoints "
                       f"discovered). Client secret and redirect URIs are validated at "
                       f"actual sign-in."}


def end_session_url(post_logout_uri: str, id_token: str = None) -> str:
    """Keycloak RP-initiated logout URL."""
    base = (cfg.KEYCLOAK_SERVER_URL or "").rstrip("/")
    url = (f"{base}/realms/{cfg.KEYCLOAK_REALM}/protocol/openid-connect/logout"
           f"?client_id={cfg.KEYCLOAK_CLIENT_ID}"
           f"&post_logout_redirect_uri={post_logout_uri}")
    if id_token:
        url += f"&id_token_hint={id_token}"
    return url
