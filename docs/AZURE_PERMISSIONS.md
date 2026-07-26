# Azure access required by AlMadar 360

The portal uses **three independent identities**. Grant each only what it needs.

| Identity | Settings | Used for |
|---|---|---|
| **Automation SP** | Azure Credentials (`AZURE_*`) or Managed Identity | All network/DNS/AKS operations + the read-only diagnostics and lookups |
| **Cost SP** | Cost / Billing (`COST_*`) | The cost dashboard only — deliberately isolated from automation |
| **Optimizer SP** | Resource Optimizer (`OPT_*`) | The idle/orphaned resource scan only — read-only, isolated from the others |

All write operations honour `AZURE_DRY_RUN`; the diagnostics, lookups, cost
dashboard and optimizer scan are strictly read-only.

---

## 1. Automation service principal

Assign at the narrowest scope that still covers your hub and spoke resource
groups / subscriptions. Built-in roles below; a least-privilege custom role
alternative follows.

### By feature

| Feature | Role / actions | Scope |
|---|---|---|
| **New VNET, subnets, peering, spoke UDRs** | **Network Contributor** | Spoke RG/subscription **and** the hub VNet (peering is two-sided) |
| **Create resource group** (admin-deploy) | `Microsoft.Resources/subscriptions/resourceGroups/write` (in **Contributor**) | Spoke subscription |
| **Hub routing (gateway/ZPA route tables)** | **Network Contributor** | Hub route-table RG (`UDR_RESOURCE_GROUP`) |
| **Firewall policy** (add/modify/delete rules, lookups) | **Network Contributor**, or a custom role with `Microsoft.Network/firewallPolicies/read` + `.../ruleCollectionGroups/read,write,delete` | Firewall policy RG (`FIREWALL_POLICY_RG`) |
| **Private DNS** (records, zone create, VNet links, zone availability check) | **Private DNS Zone Contributor** | DNS-zones RG (`DNS_ZONE_RG`) and any spoke VNet you link |
| **AKS create / delete** | **Azure Kubernetes Service Contributor** (create/delete managed clusters) | Cluster RG/subscription |
| **AKS node-subnet integration** | **Network Contributor** (needs `.../subnets/join/action`) | The VNet/subnet the cluster joins |
| **AKS → hub private-DNS link** (ZPA-access clusters) | **Private DNS Zone Contributor** on the AKS **node resource group** (`MC_*`) + **Reader** on the hub VNet (the link references it) | Node RG + hub VNet |
| **Live option lookups** (K8s versions, VM sizes, regions, VNets, subnets) | **Reader** (`list_kubernetes_versions`, `resource_skus`, `/locations`, VNets, subnets) | Reported subscriptions |
| **Network diagnosis** (Report Network Issue) | **Reader** — VNets, subnets, route tables, private DNS zones/records, VNet peerings, firewall policy | Hub + spoke subscriptions to be traced |
| **ZPA Analyzer / Reachability** | *No Azure role* — SSH (key) to the connector VMs | n/a |

> **Note on `list_locations` / cross-subscription search:** the diagnosis and
> lookups search the **hub and spoke subscriptions** (plus any the requester
> names). The SP must have at least **Reader** on every subscription you want it
> to see; a subscription with no role assignment is invisible.

### Simplest grant
**Contributor** on the hub + each spoke resource group covers everything above
except DNS. Add **Private DNS Zone Contributor** on the DNS-zones RG (and each
AKS node RG for ZPA-access clusters). Contributor is broad — prefer the
per-feature roles for least privilege.

### Least-privilege custom role (data actions it performs)
```
Microsoft.Network/virtualNetworks/*            (read, write, peer, subnets/join)
Microsoft.Network/routeTables/*                (read, write, routes)
Microsoft.Network/networkSecurityGroups/*      (read, write — NSG rules)
Microsoft.Network/firewallPolicies/read
Microsoft.Network/firewallPolicies/ruleCollectionGroups/read,write,delete
Microsoft.Network/privateDnsZones/*            (or Private DNS Zone Contributor)
Microsoft.ContainerService/managedClusters/read,write,delete
Microsoft.ContainerService/locations/kubernetesVersions/read
Microsoft.Compute/locations/vmSizes/read       (or resourceSkus/read)
Microsoft.Resources/subscriptions/resourceGroups/read,write
Microsoft.Resources/subscriptions/read, .../locations/read      (Reader)
```

---

## 2. Cost service principal (separate)

A **second app registration**, isolated from automation. On every subscription
(or a management group / billing account that contains them):

| Role | Why |
|---|---|
| **Cost Management Reader** | Run cost queries (`Microsoft.CostManagement/query/action`) |
| **Reader** | Enumerate subscriptions (`Microsoft.Resources/subscriptions/read`) |

- Grant at a **management group** to cover many subscriptions with one
  assignment; or per-subscription. If you set **Subscriptions to report**
  (`COST_SUBSCRIPTIONS`) explicitly, each listed subscription still needs Cost
  Management Reader.
- It needs **no** write, network, or resource access — keep it read-only.
- Verify from **Settings → Cost / Billing → Test Cost SP**.

> **Fast spend for many subscriptions — grant the cost SP a management group.**
> Fetching spend with **one Cost Management query per subscription** is fine for a
> handful, but with dozens Azure **throttles** the queries (HTTP 429) and spend
> loads slowly. Instead, grant the cost SP **Cost Management Reader** at a
> **management group** that contains your subscriptions — all subscription spend
> then comes back in a **single** grouped query.
>
> - The tenant **root** MG covers everything, but many tenants don't allow role
>   assignments there. Any **intermediate** management group that sits above your
>   subscriptions works just as well — grant Cost Management Reader on that.
> - **You don't need to enter the ID.** Leave **Cost / Billing → Management group
>   ID (fast spend)** (`COST_MANAGEMENT_GROUP`) blank and the portal
>   auto-discovers the hierarchy (via `getEntities`) and uses the highest group it
>   can actually read — skipping groups it only sees but can't query. Set an ID
>   only to pin a specific group and skip discovery.
> - Discovery needs `Microsoft.Management/managementGroups/read`, which **Cost
>   Management Reader includes**. Spend results are cached ~10 minutes so both
>   pages share one lookup.

---

## 3. Optimizer service principal (separate)

A **third app registration**, isolated from automation and cost, used only by the
**Resource Optimizer** to scan for idle / orphaned resources (unattached disks,
unassociated public IPs, stopped/deallocated VMs, stale snapshots, orphaned
NSGs/route tables, empty resource groups).

| Role | Why |
|---|---|
| **Reader** | Read resources and query **Azure Resource Graph** (`Microsoft.ResourceGraph/*/read`, included in Reader) across the scanned scopes |

- Grant **Reader** at a **management group** to cover many subscriptions with one
  assignment, or per-subscription. Set **Resource Optimizer → Subscriptions to
  scan** (`OPT_SUBSCRIPTIONS`) to limit the scope, or leave blank to scan every
  subscription the SP can see.
- It needs **no** write, cost, or network-specific access — Reader is enough, and
  the scan is **strictly read-only**: the platform reports findings and links to
  the Azure Portal, but **never deletes anything**.
- Snapshots older than `OPT_SNAPSHOT_AGE_DAYS` (default 90) are flagged as stale.
- Verify from **Settings → Resource Optimizer → Test Optimizer SP**. Results are
  cached ~10 minutes.

> **Real costs come from the Cost SP, not the optimizer SP.** The optimizer SP
> only *finds* idle resources (Resource Graph). Their **actual** monthly cost is
> fetched by the **Cost SP** (§2) via a Cost Management query grouped by
> `ResourceId` (last full month). So for real figures instead of retail estimates,
> configure the Cost SP and give it **Cost Management Reader** on the scanned
> subscriptions. Without the Cost SP, the optimizer falls back to approximate
> retail-rate estimates and labels them as such.

---

## Setup checklist
1. Create the **automation** app registration → grant the roles in §1 at your
   hub/spoke scopes → fill Settings → Azure Credentials → **Test Connection**.
2. Create a **separate cost** app registration → grant **Cost Management Reader**
   + **Reader** (§2) → fill Settings → Cost / Billing → **Test Cost SP**.
3. Create a **separate optimizer** app registration → grant **Reader** (§3) →
   fill Settings → Resource Optimizer → **Test Optimizer SP**.
4. Keep all client secrets in the portal (encrypted at rest) or supply via env.
