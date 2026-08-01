# Next Actions

> Prioritized, actionable only. Completed items are removed (not archived —
> history lives in `daily/`/`weekly`/`monthly` and `architecture-decisions.md`).
> Last updated: 2026-08-01.

## P0 — Ship-blocking

1. **Commit the Storage Account Request & Deploy feature** — implemented
   and verified (dry-run) end-to-end this session, currently only in the
   working tree. Touches `models.py`, `config.py`, `app.py`, `azure_tools.py`,
   `changes.py`, `requirements.txt`, `templates/requester.html`,
   `templates/request_detail.html`, `templates/help_admin.html`,
   `templates/help_requester.html`, `scripts/test_storage_validation.py`,
   `docs/HOW_IT_WORKS.md`, `CLAUDE.md`.
2. Run a full live-Azure verification of both `VM_CREATE` and
   `STORAGE_ACCOUNT_CREATE` against a real subscription (real SP credentials
   needed — this sandbox has none configured) before either reaches prod.
   See `current-state.md` "Verification Notes."

## P1 — Follow-up

3. Confirm `helm/subnet-manager/values.yaml`'s `existingSecretName:
   "almadar-db"` change is correct for the target cluster (real secret
   exists with that name, key `DATABASE_URL`) before this reaches prod via
   Helm upgrade. (Still uncommitted, unrelated to VM/Storage work.)
4. Resolve `static/page-bg-original.jpg` (untracked) — decide keep-as-backup
   vs. delete.
5. Run `scripts/test_storage_validation.py` in CI or as a pre-commit habit
   if any of `_validate_storage_request`, the Storage form's client-side
   checks, or the SKU/container/IP rules change — nothing currently runs it
   automatically (no CI configured in this repo).

## P2 — Ongoing / standing

6. Keep `.memory/` updated as part of "definition of done" for every future
   feature (standing process requirement — see `CLAUDE.md` → "Session Memory
   Protocol").
7. Run `graphify update .` after any further code changes to keep the
   knowledge graph current.

## Backlog (not yet actionable — needs scoping)

- GPU utilization dashboard — prerequisites documented in
  `docs/GPU_UTILIZATION.md`, nothing implemented yet. Needs a decision on
  metrics pipeline (Azure Managed Prometheus recommended) before work starts.
