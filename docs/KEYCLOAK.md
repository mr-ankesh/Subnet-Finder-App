# Keycloak (OIDC) Integration Guide

Status: **implemented and live.** SSO login, role-based admin/requester access, and
group-based team visibility (below) are all working in production. Sections 1–6
below are kept as the original implementation recipe/reference; the **Team &
group-based visibility** section describes the actual current behavior.

## Team & group-based visibility (live behavior)

Requesters can belong to a **team** (Keycloak group). Team members see every ticket
raised by anyone in their team, not just their own — no separate per-request sharing
needed.

### How a user's team is determined

- On login (`/auth/callback`), the app reads the Keycloak `groups` claim from the
  ID token / access token / userinfo (`auth_oidc.groups_from_token()`) and stores the
  normalized group names in `session["sso_groups"]`. Full group paths
  (`/Parent/Alpha Team`) are reduced to the leaf name (`Alpha Team`); duplicates are
  de-duplicated case-insensitively.
- `_available_teams()` (`app.py`) is the single enforcement point used everywhere a
  team list is needed (request form, "My/Team Requests" panel, `/api/requester/teams`,
  `/api/requester/team-requests`):
  - **SSO mode** → returns `session["sso_groups"]` exactly. A user can only file
    under, or view, a team they are actually a member of in Keycloak.
  - **Local/open mode** (no SSO) → returns the admin-configured `Settings → Teams`
    list. This is selection-based, not enforced, since open mode has no per-user
    identity.

### Visibility rules

| Situation | What the user sees |
|---|---|
| SSO user, in ≥1 Keycloak group | Can select any of their groups as "team" when filing a request, and can view **all tickets raised by that team** via `/api/requester/team-requests?team=<name>` (server rejects any team not in their own `sso_groups`). |
| SSO user, in **no** Keycloak group | Falls back to **individual mode**: `/api/requester/my-requests` returns only tickets matching their own signed-in email/name. They see nobody else's tickets, and nobody else sees theirs. The UI shows an amber notice explaining this and pointing them to their administrator to be added to a team group. |
| Local/open mode (no SSO) | Team is a plain dropdown from `Settings → Teams`; any user can pick any configured team — not identity-enforced, by design (no login = no identity to enforce against). |

Verified by test: two different no-group SSO users cannot see each other's tickets;
a user attempting to query a team they don't belong to gets zero results.

### Keycloak-side prerequisite: the `groups` claim

Group membership is **not** included in tokens by default — it requires a client
mapper:

1. Keycloak admin console → your realm → **Client scopes** (or directly on the
   `subnet-manager` client) → **Mappers → Add mapper → By configuration → Group
   Membership**.
2. Token Claim Name: `groups`.
3. Enable **Add to ID token**, **Add to access token**, and **Add to userinfo**
   (the app reads all three, first match wins).
4. Full group path vs. name: either works — the app strips any leading path and
   keeps only the leaf name, so `/Presight/Alpha Team` and `Alpha Team` both resolve
   to the same team `Alpha Team`.
5. Create the groups under **Groups** in Keycloak (e.g. `Alpha Team`, `Bravo Team`)
   and assign users to them. A user assigned to zero groups falls back to individual
   mode (see table above).
6. Users must **sign out and sign in again** after being added to a group — the
   group list is captured once, at login, into the session.

## Why Keycloak here

- Real identities instead of a shared admin password — the audit trail's `actor`
  becomes the SSO username automatically.
- Requesters stop typing their name/email; both come from the token.
- Role-based access: `subnet-admin` (full admin) vs `subnet-requester` (portal only).

---

## Step 1 — Keycloak side (admin console)

1. **Realm**: use your existing org realm (e.g. `presight-rnd`) or create one.
2. **Client**: *Clients → Create client*
   - Client ID: `subnet-manager` (must match the `KEYCLOAK_CLIENT_ID` setting)
   - Client type: **OpenID Connect**, Client authentication: **On** (confidential)
   - Valid redirect URIs: `https://<app-host>/auth/callback` (and `http://localhost:8080/auth/callback` for dev)
   - Valid post-logout redirect URIs: `https://<app-host>/`
   - Web origins: `https://<app-host>`
3. **Roles**: under the client (or realm), create roles `subnet-admin` and
   `subnet-requester` (names must match the `KEYCLOAK_ADMIN_ROLE` / `KEYCLOAK_REQUESTER_ROLE` settings).
4. **Assign roles**: map AD/LDAP groups → roles via *Groups → Role mapping*, or assign
   per-user. Network team → `subnet-admin`; everyone else who may request → `subnet-requester`.
5. **Credentials tab**: copy the client secret → paste into Settings → Authentication →
   Client secret (stored encrypted).
6. Confirm the discovery document resolves:
   `https://<keycloak>/realms/<realm>/.well-known/openid-configuration`

## Step 2 — App dependencies

```
# requirements.txt
authlib>=1.3.0
```

## Step 3 — App changes (implementation sketch)

### 3a. OIDC client registration (new `auth_oidc.py`)

```python
from authlib.integrations.flask_client import OAuth
from config import cfg

oauth = OAuth()

def init_oidc(app):
    oauth.init_app(app)
    oauth.register(
        name="keycloak",
        client_id=cfg.KEYCLOAK_CLIENT_ID,
        client_secret=cfg.KEYCLOAK_CLIENT_SECRET,
        server_metadata_url=(f"{cfg.KEYCLOAK_SERVER_URL}/realms/"
                             f"{cfg.KEYCLOAK_REALM}/.well-known/openid-configuration"),
        client_kwargs={"scope": "openid profile email roles"},
    )
```

### 3b. Routes (in app.py)

```python
@app.route("/auth/login")
def auth_login():
    redirect_uri = url_for("auth_callback", _external=True)
    return oauth.keycloak.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def auth_callback():
    token = oauth.keycloak.authorize_access_token()
    claims = token["userinfo"]
    roles = set(claims.get("realm_access", {}).get("roles", [])) | \
            set(claims.get("resource_access", {})
                       .get(cfg.KEYCLOAK_CLIENT_ID, {}).get("roles", []))
    session["sso_user"]   = claims.get("preferred_username")
    session["sso_email"]  = claims.get("email")
    session["admin_name"] = claims.get("name") or session["sso_user"]   # audit actor
    session["is_admin"]   = cfg.KEYCLOAK_ADMIN_ROLE in roles
    audit.record("admin_login" if session["is_admin"] else "sso_login",
                 actor=session["admin_name"], actor_role="admin" if session["is_admin"] else "requester",
                 summary=f"Keycloak SSO login ({session['sso_user']})")
    return redirect(url_for("requests_list" if session["is_admin"] else "requester_page"))

@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(f"{cfg.KEYCLOAK_SERVER_URL}/realms/{cfg.KEYCLOAK_REALM}"
                    f"/protocol/openid-connect/logout?client_id={cfg.KEYCLOAK_CLIENT_ID}"
                    f"&post_logout_redirect_uri={cfg.SUBNET_FINDER_BASE_URL}")
```

### 3c. Switch on `AUTH_PROVIDER`

- `require_admin` (app.py): unchanged — it checks `session["is_admin"]`, which either
  flow sets.
- `admin_login` route: when `cfg.AUTH_PROVIDER == "keycloak"`, redirect `GET /admin/login`
  → `/auth/login` instead of rendering the password form.
- Requester portal: when Keycloak is active, prefill/lock `requester_name` and
  `requester_email` from `session["sso_user"]/["sso_email"]` (template context), and
  optionally gate `/requester` behind `KEYCLOAK_REQUESTER_ROLE`.
- Keep `init_oidc(app)` guarded: only register the OAuth client when
  `cfg.AUTH_PROVIDER == "keycloak"` and the server URL/realm/client are set.

### 3d. Audit actor

`current_actor()` already reads `session["admin_name"]`, so once 3b sets it from the
token, every audit entry carries the real SSO identity — no further changes.

## Step 4 — Config mapping

| Settings UI (DB override) | Env var (bootstrap) | Example |
|---|---|---|
| Auth provider | `AUTH_PROVIDER` | `keycloak` |
| Keycloak server URL | `KEYCLOAK_SERVER_URL` | `https://sso.presight.ai` |
| Realm | `KEYCLOAK_REALM` | `presight-rnd` |
| Client ID | `KEYCLOAK_CLIENT_ID` | `subnet-manager` |
| Client secret | `KEYCLOAK_CLIENT_SECRET` | *(encrypted at rest)* |
| Admin role | `KEYCLOAK_ADMIN_ROLE` | `subnet-admin` |
| Requester role | `KEYCLOAK_REQUESTER_ROLE` | `subnet-requester` |

K8s: prefer injecting `KEYCLOAK_CLIENT_SECRET` via a Secret-backed env var rather than
the DB override; the resolution order (DB → env → default) supports either.

## Step 5 — Gotchas

- **FLASK_SECRET_KEY** must be strong and stable — OIDC state/nonce live in the session,
  and rotating the key also invalidates DB-stored secrets.
- Behind a reverse proxy, set `ProxyFix` (or `PREFERRED_URL_SCHEME=https`) so
  `_external=True` URLs generate `https://` redirect URIs — mismatched redirect URIs are
  the most common Keycloak error.
- Token lifetime: the Flask session outlives the access token; for this app that's fine
  (we only need identity at login), so no refresh-token plumbing is required.
- Clock skew between app host and Keycloak breaks token validation — keep NTP on both.

## Step 6 — Rollout

1. Deploy with `AUTH_PROVIDER=local` (nothing changes), configure all Keycloak settings.
2. Test `/auth/login` manually while password login still works.
3. Flip `AUTH_PROVIDER=keycloak` in Settings (no restart needed).
4. Keep `ADMIN_PASSWORD` as a break-glass fallback route (e.g. `/admin/login?local=1`)
   or remove it once SSO is proven.
