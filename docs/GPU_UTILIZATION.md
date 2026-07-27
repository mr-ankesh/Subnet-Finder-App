# GPU Utilisation Dashboard — Prerequisites & Plan

**Vision:** a dashboard in AlMadar 360 showing GPU utilisation (compute %, memory,
temperature, power) across N-series VMs and AKS GPU node pools, so idle/under-used
GPUs — the most expensive resources in the estate — can be reclaimed or right-sized.

> **Why this is different from CPU:** Azure Monitor exposes **CPU as a platform
> metric** for every VM out of the box (that's what the "Underutilized VM" scan
> uses). **GPU utilisation is NOT a platform metric** — the hypervisor can't see
> inside the GPU. It must be collected from **inside the guest** with NVIDIA
> tooling and shipped to a metrics store. That collection pipeline is the bulk of
> the prerequisites below.

---

## 1. On every GPU host (VM or AKS node)

| Prerequisite | Detail |
|---|---|
| **N-series SKUs** | NC / ND / NV (NVIDIA) — AMD MI-series uses ROCm instead |
| **NVIDIA driver** | VM: **NVIDIA GPU Driver extension** (or manual driver). AKS: the **NVIDIA device plugin / GPU Operator** |
| **DCGM exporter** | NVIDIA **DCGM Exporter** running on each host — exposes `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED/FREE` (frame-buffer memory), `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE` as Prometheus metrics |

## 2. A metrics pipeline (pick one)

- **A — Azure Managed Prometheus (recommended).** Create an **Azure Monitor
  workspace**; enable **Managed Prometheus**; scrape the DCGM exporter. On **AKS**
  this is a checkbox (`az aks update --enable-azure-monitor-metrics`) plus the GPU
  Operator's ServiceMonitor. On **VMs/VMSS**, run the Azure Monitor Agent with a
  Prometheus scrape Data Collection Rule pointing at the DCGM endpoint. Query with
  **PromQL** at the workspace's query endpoint.
- **B — Custom metrics via AMA + DCR.** Push DCGM counters as **custom metrics**
  under a namespace (e.g. `gpu`), then read them through the normal Azure Monitor
  Metrics API (same path the CPU scan uses). Simpler to query, more setup per host.
- **C — Container Insights (AKS only).** Enables GPU metrics in the Insights
  experience; queryable via Log Analytics (KQL) rather than PromQL.

## 3. Access (a read-only identity)

| Need | Grant |
|---|---|
| Read Managed Prometheus (A) | **Monitoring Data Reader** on the **Azure Monitor workspace**, and the workspace **query endpoint URL** |
| Read custom metrics (B) | **Reader / Monitoring Reader** on the subscriptions (metrics read) |
| Read Log Analytics (C) | **Log Analytics Reader** on the workspace + the **workspace ID** |

The existing **optimizer SP** (Reader) can be reused if you add the relevant
reader role above; keep it read-only.

## 4. Config the app will need (once the pipeline exists)

- Pipeline type (A/B/C) and the **query endpoint** (Prometheus URL, or Log
  Analytics workspace ID).
- The **metric names** (defaults: `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`,
  `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE`).
- **Idle GPU thresholds** (e.g. avg GPU util < 10% over 7/30 days → under-used) and
  the look-back window.

## 5. What we'll build on top (once prerequisites are met)

- A **GPU** dashboard: fleet utilisation (heat strip per GPU), memory pressure,
  temperature/power, and a **top-idle-GPU** table joined to the resource's **actual
  cost** (via the Cost SP, exactly like the optimizer already does).
- A new optimizer finding **"Under-used GPU VM"** — the highest-value reclaim,
  since GPU VMs are the costliest.

## Minimum viable path
Managed Prometheus + DCGM exporter on your AKS GPU pool (pipeline **A**), grant the
optimizer SP **Monitoring Data Reader** on the Azure Monitor workspace, and give us
the **workspace query endpoint**. That unlocks both the dashboard and the GPU
under-utilisation finding.
