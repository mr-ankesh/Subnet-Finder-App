# Next Actions

> Prioritized, actionable only. Completed items are removed (not archived —
> history lives in `daily/`/`weekly`/`monthly` and `architecture-decisions.md`).
> Last updated: 2026-08-01.

## P0 — Ship-blocking

None currently — VM_CREATE and STORAGE_ACCOUNT_CREATE were both verified
end-to-end against real Azure on 2026-08-01 (real deploy → independently
confirmed via `az` CLI → revert → confirmed cleanup). See
`current-state.md` "Verification Notes" for exactly what was and wasn't
covered (failure paths, replication/private-endpoint, CMK/UAMI weren't
exercised against real Azure).

## P1 — Follow-up

1. Delete the leftover empty `rg-claude-e2e-qa-test` resource group in
   subscription `845e564b-31a3-44b0-b030-226798b31574` ("Sandbox
   Connectivity") — created for the 2026-08-01 real E2E test; storage
   account + VM were reverted, but resource-group deletion itself was
   blocked by the session's permission classifier, so it needs a human (or
   a future session with that permission) to remove it.
2. Confirm `helm/subnet-manager/values.yaml`'s `existingSecretName:
   "almadar-db"` change is correct for the target cluster (real secret
   exists with that name, key `DATABASE_URL`) before this reaches prod via
   Helm upgrade. (Still uncommitted, unrelated to VM/Storage work.)
3. Resolve `static/page-bg-original.jpg` (untracked) — decide keep-as-backup
   vs. delete.
4. Run `scripts/test_storage_validation.py` in CI or as a pre-commit habit
   if any of `_validate_storage_request`, the Storage form's client-side
   checks, or the SKU/container/IP rules change — nothing currently runs it
   automatically (no CI configured in this repo).
5. Consider exercising the failure/partial-failure paths against real Azure
   too, if worth the cost/time: a mid-loop VM failure, a container-creation
   failure after the storage account exists, CMK/user-assigned-identity
   deploys, and the object-replication/private-endpoint best-effort steps —
   none of these were covered by the 2026-08-01 real E2E pass.

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
