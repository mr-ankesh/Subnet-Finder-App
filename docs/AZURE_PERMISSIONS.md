# Azure access required by Network Copilot

The portal uses **two independent identities**. Grant each only what it needs.

| Identity | Settings | Used for |
|---|---|---|
| **Automation SP** | Azure Credentials (`AZURE_*`) or Managed Identity | All network/DNS/AKS operations + the read-only diagnostics and lookups |
| **Cost SP** | Cost / Billing (`COST_*`) | The cost dashboard only — deliberately isolated from automation |

All write operations honour `AZURE_DRY_RUN`; the diagnostics, lookups and cost
dashboard are strictly read-only.

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

> **Fast spend for many subscriptions — set a management group.**
> With no management group configured the portal fetches spend with **one Cost
> Management query per subscription**. That is fine for a handful, but with dozens
> Azure **throttles** the queries (HTTP 429) and the dashboard/inventory spend
> loads slowly. Set **Cost / Billing → Management group ID (fast spend)**
> (`COST_MANAGEMENT_GROUP`) to a management group the cost SP can read — all
> subscription spend then comes back in a **single** grouped query. Grant the cost
> SP **Cost Management Reader** at that management group (the tenant **root** MG,
> whose ID equals the tenant ID, covers everything). Spend results are also cached
> for ~10 minutes so the two pages share one lookup.

---

## Setup checklist
1. Create the **automation** app registration → grant the roles in §1 at your
   hub/spoke scopes → fill Settings → Azure Credentials → **Test Connection**.
2. Create a **separate cost** app registration → grant **Cost Management Reader**
   + **Reader** (§2) → fill Settings → Cost / Billing → **Test Cost SP**.
3. Keep both client secrets in the portal (encrypted at rest) or supply via env.
