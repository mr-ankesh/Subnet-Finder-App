# Feature Roadmap

> Planned / future / deferred work. Not actionable-task-tracking (see
> `next-actions.md` for that) — this is the longer-horizon list.
> Last updated: 2026-08-01.

## In Flight (see `current-state.md` for detail)

- VM(s) deployment request type — implemented, uncommitted, pending
  verification + commit.

## Planned / Designed but Not Built

- **GPU Utilization Dashboard** (`docs/GPU_UTILIZATION.md`) — full
  prerequisites and design doc exists; **nothing implemented**. Vision: GPU
  compute%/memory/temp/power across N-series VMs and AKS GPU node pools, for
  reclaiming idle/underused GPUs.
  - Needs: DCGM Exporter on every GPU host, a metrics pipeline (Azure
    Managed Prometheus recommended), and an optimizer finding modeled after
    the existing "Underutilized VM" (CPU) scan.
  - Key open decision: which metrics pipeline to standardize on (doc lists
    Azure Managed Prometheus as recommended option A).
  - Treat this doc as a spec to build toward, not existing behavior — per
    `CLAUDE.md`.

## Future Enhancements (no design doc yet — ideas surfaced during past work)

- Extend the resource optimizer's usage-pattern scanning beyond CPU (the
  GPU dashboard is one instance of this; memory-based underutilization
  scanning is another candidate given `fix(optimize): CPU usage scan was
  400ing on the memory metric — decouple it` suggests memory-metric support
  was scoped out, not abandoned).
- Broaden the multi-VM plan model (`build_vm_plan`/`create_vm`) patterns to
  other multi-resource request types, if a future request type needs N
  independent Azure mutations per action the way `VM_CREATE` does.

## Deferred Work

- Renaming legacy identifiers (Helm release name `subnet-manager`, repo name
  `Subnet-Finder-App`, module name `subinventory.py`) to match the
  Presight AlMadar 360 brand — explicitly deferred per `docs/BRANDING.md`;
  not planned unless the churn becomes worth it.
