# Known Issues

> No open bug tracker/issue tool is wired into this repo — this file *is*
> the bug tracker. No `TODO`/`FIXME`/`XXX` markers exist in the Python source
> as of 2026-08-01 (checked via grep) — open items below come from
> reasoning about the code and CLAUDE.md, not code comments.
> Last updated: 2026-08-01.

## Open Bugs

- None currently known/reported.

## Technical Debt

- **`app.py` is a ~240KB, 110-route monolith.** Deliberate per `CLAUDE.md`
  (feature modules are lazy-imported so each feature's logic stays in its
  own module), but route/view logic for shared dispatchers like
  `admin_azure_action()` keeps growing in-place (VM actions were just added
  there) — worth watching for it becoming unwieldy to navigate without
  `graphify`.
- **No automated test suite** (no pytest/ruff/flake8 config in the repo).
  Every change is verified by running the app and checking in-browser. This
  is a standing constraint, not a regression — but it means SSO/Keycloak-
  gated logic (approvals, group-based team routing) is **only** verifiable
  by reasoning through the code or an isolated/mocked check, never by
  running locally (no Keycloak in local dev).
- **Raw-SQL tables outside `models.py`** (`subscription_inventory`,
  `budget_alert_state`, `agent_chats`) each need their own `ensure_table()`
  and their own explicit coverage in `scripts/sqlite_to_postgres.py` — this
  has already caused two fix commits (`fix(db): subinventory.ensure_table
  crashed on Postgres`, `fix(migrate): sqlite_to_postgres covers
  agent_chats/...`). Any *new* raw-SQL table needs the same two things done
  up front, or it will silently misbehave on Postgres / be missed by
  migration.
- **Untracked `static/page-bg-original.jpg`** sitting alongside the replaced
  `static/page-bg.jpg` — likely a manual backup, not yet resolved (either
  commit intentionally with a clear name, or delete).

## Investigations Needed

- **VM(s) deployment feature has not yet been run end-to-end in this
  session** (or confirmed run in a prior one) — needs a dry-run pass at
  minimum before commit. See `current-state.md` / `next-actions.md`.
- **`helm/subnet-manager/values.yaml`'s `existingSecretName: "almadar-db"`**
  — uncommitted change from empty string to a specific secret name. Needs
  confirmation this matches a real secret in the target AKS cluster before
  it ships; if it doesn't, the next Helm upgrade will fail to find
  `DATABASE_URL`.
- **`docs/GPU_UTILIZATION.md`** documents a full prerequisites/design plan
  for a GPU utilization dashboard that is **not implemented at all** — flagged
  here so it isn't mistaken for partially-built. See `feature-roadmap.md`.
