# Worked example — the composer acceptance test

**kb_version:** 2.1.0
**Supersedes:** 2.0.0 — all AKS sizing figures changed. See the changelog note at the bottom.

This is the reference output and the acceptance test for the environment composer. Given this
input, the implementation must produce output of this shape and depth. All arithmetic here is
authoritative and matches `network_sizing.yaml → canonical_examples`.

---

## Input

> **User:** I want to set up an environment for my development work.
>
> **Advisor:** *(asks intake questions)*
>
> **User:** 10 VMs, 1 AKS cluster, 1 managed PostgreSQL. The application will be publicly hosted.

Parsed inventory, confirmed back to the user:

| Component | Count |
|---|---|
| Virtual machines | 10 |
| AKS clusters | 1 (6 nodes — advisor asks) |
| PostgreSQL Flexible Server | 1 |
| Public exposure | Yes |

---

## Expected output

### Recommended Presight Architecture — Development Environment

A single spoke VNET peered to the Connectivity hub, carrying four subnets. Everything runs
privately; exactly one public IP exists, on the Application Gateway, and it accepts traffic
only from Cloudflare.

---

### Components

I've added three things you didn't ask for, because the rest depends on them:

| Added | Why |
|---|---|
| **Key Vault Premium** | Holds the CMK encryption keys and the TLS certificate. Storage, disks and the database all encrypt against it, so it's built first. |
| **Container Registry** | Your AKS cluster needs somewhere private to pull images from. |
| **Application Gateway WAF v2** | Public access at Presight goes through a WAF behind Cloudflare. There's no other approved path. |

I also need to ask: **will the cluster need persistent storage** — a storage account for
artefacts, logs or persistent volumes? Most do, and it changes the request list.

---

### Network plan

**One spoke VNET.** Segregation is done with subnets and NSGs, not extra VNETs — each
additional VNET multiplies the peering, routing and DNS-link work with no security benefit
here. The hub already exists; your spoke peers to it.

**Recommended VNET size: /23**

| Subnet | Purpose | Size | Usable | Sizing basis |
|---|---|---|---|---|
| `snet-appgw` | Application Gateway | **/24** | 251 | Dedicated subnet, required. Sized so the gateway can autoscale later. |
| `snet-aks` | AKS node pool | **/26** | 59 | Overlay: only nodes take VNET IPs. 6 nodes + surge headroom ≈ 13 today; /26 carries ~40 nodes. |
| `snet-vm` | Virtual machines | **/27** | 27 | 10 VMs today, headroom to 27. |
| `snet-pe` | Private endpoints | **/27** | 27 | PostgreSQL, Key Vault, ACR, Storage. These accumulate. |

**The arithmetic:** 256 + 64 + 32 + 32 = **384 addresses**, which fits a /23 (512) at
**75% allocated**, leaving 128 spare.

That's a comfortable fit with room for a second cluster or a larger VM fleet later.

**Pod networking is separate.** Your cluster runs Azure CNI Overlay, so pods get their
addresses from a **Pod CIDR of 10.244.0.0/16** — not from the VNET. That's why the AKS subnet
is small: it only holds nodes. The Pod CIDR isn't carved from the 10.110.0.0/16 pool and
doesn't count toward the VNET size above, but it must not overlap the VNET, the hub, or the
VPN and ZPA ranges. TechOps confirms it alongside the VNET allocation.

These are proposed sizes. TechOps allocates the actual range from the 10.110.0.0/16 pool and
approves it to guarantee no overlap with the hub, VPN, ZPA or any existing spoke.

---

### Hub integration

Required — this is a new spoke, and it's all handled inside the New VNET request:

- Hub ↔ spoke peering, both directions
- Base UDR on every subnet: `0.0.0.0/0` → Azure Firewall
- Gateway route propagation **disabled** (mandatory wherever a custom UDR applies)
- Hub route tables updated with your CIDR — GatewaySubnet, Identity VNET, Security VNET
- NSGs created and associated per subnet

---

### Private connectivity

Four private endpoints, all landing in `snet-pe`:

| Service | Sub-resource | Private DNS zone to link |
|---|---|---|
| PostgreSQL Flexible Server | `postgresqlServer` | `privatelink.postgres.database.azure.com` |
| Key Vault | `vault` | `privatelink.vaultcore.azure.net` |
| Container Registry | `registry` | `privatelink.azurecr.io` |
| Storage Account | `blob` | `privatelink.blob.core.windows.net` |

Plus the AKS private cluster zone, since the API server is private.

**The DNS links are not optional.** Without them the client resolves the public name, gets a
public IP, and the connection fails — while the private endpoint sits there looking perfectly
healthy. It's the single most common way this architecture appears broken.

---

### Public access — how this works at Presight

Your application needs to be reachable from the internet, which changes the shape of the
architecture rather than just adding a setting.

**The design.** Public traffic never reaches your application directly. It arrives at
Cloudflare, which handles public DNS, TLS, DDoS protection and a first WAF pass. Cloudflare
forwards to an Azure Application Gateway running WAF v2 — a second, independent inspection
layer that terminates TLS again and routes to your backend. Your AKS cluster, VMs and
database stay entirely private behind it, with no public IPs of their own.

**The control that makes it work.** The Application Gateway's public IP accepts connections
**only from Cloudflare's published IP ranges**. Everything else is dropped. This matters more
than it sounds: if the origin is reachable directly, an attacker simply skips Cloudflare and
every protection it provides goes with it. A previous Presight security assessment found
exactly this — an origin IP answering on an unexpected port, bypassing the WAF completely. So
alongside the IP allow-list we recommend Cloudflare Authenticated Origin Pulls, and closing
every port on the origin that isn't serving the application.

**The part that needs people, not configuration.** Public exposure at Presight requires formal
**InfoSec onboarding and approval**. InfoSec owns the public DNS record and is accountable for
it — this isn't a checkbox in the request form, and it isn't something the platform team can
grant on their behalf.

Practically, this doesn't have to slow you down. You can design, build and test everything
internally in parallel; what waits on InfoSec is the moment the hostname becomes publicly
resolvable. The projects that run late are the ones that leave this until the week before
go-live.

Worth knowing up front: approval is ongoing rather than one-off. InfoSec runs periodic audits
against the security standards to confirm the exposure still looks the way it did on day one.
Give this endpoint a named technical owner who expects to answer those.

I've put InfoSec onboarding first in your request list and drafted the brief below, so you can
start that conversation with most of the answers already prepared.

**Traffic path**

```
Internet
  → Cloudflare            public DNS, TLS, WAF, DDoS, bot filtering
  → App Gateway WAF v2    public IP, Cloudflare ranges only, Prevention mode
  → AKS ingress           internal load balancer, private
  → PostgreSQL / Storage  private endpoints only
```

---

### Build sequence

Waves 0 and 1 start together. After the network lands, most of the rest can run in parallel.

| Wave | Requests | Notes |
|---|---|---|
| **0 — day one** | InfoSec public exposure onboarding | Doesn't block building. Blocks publishing the DNS record. |
| **1 — foundation** | New VNET (CIDR, subnets, hub integration) | Everything else lands in this. |
| **2 — shared** | Key Vault Premium · Container Registry · Storage Account | Parallel. |
| **3 — connectivity** | Private endpoints + DNS links for the above | Always paired. |
| **4 — workloads** | PostgreSQL Flexible Server + PE + DNS · AKS cluster · VM(s) ×10 | Parallel. One VM request covers all ten, numbered `-001`…`-010`. |
| **5 — access** | Firewall egress rules · ZPA routing · private DNS A records | Parallel. |
| **6 — front door** | Application Gateway WAF v2 · Cloudflare allow-list rule · Cloudflare DNS record | The DNS record waits on wave 0. |

**Critical path:** InfoSec onboarding → Cloudflare DNS record. That's why it's raised first.

---

### Security posture

Applied automatically — stated here so you know what you're getting, not offered as choices:

- Public network access disabled on every PaaS service
- Private endpoints and private DNS integration throughout
- CMK encryption via Key Vault Premium, RSA-HSM key, user-assigned managed identity
- TLS 1.2 minimum everywhere
- No public IP on any backend resource
- WAF in Prevention mode at go-live, OWASP Core Rule Set
- NSGs on every subnet; default route via the hub firewall
- Resource locks on Key Vault and Storage
- Diagnostics to the Operational LAW; security logs to the Security LAW and Sentinel

---

### Before you start

- ✅ Azure subscription — you have one
- ⬜ Confirm expected AKS node count and the cluster's max-surge setting, so the subnet is
  sized right first time
- ⬜ Confirm the Pod CIDR with TechOps alongside the VNET allocation
- ⬜ Decide the public hostname and confirm who owns the domain
- ⬜ TLS certificate for that hostname, to be stored in Key Vault
- ⬜ Name a technical owner for the public endpoint — InfoSec will need one
- ⬜ Confirm whether the cluster needs persistent storage

---

### What I've pre-filled

Business unit, environment, application name, owner, criticality, data classification, and the
resource counts — carried into each request.

### What you'll need to add

Subscription ID, resource group names, and the specific VM sizes and OS images. The forms
validate all of these live against Azure.

---

**Next:** [Start with the InfoSec brief →] · [Open the New VNET request →]

Would you like the architecture diagram?

---

## Notes for the implementer

What makes this output good, and what must be preserved:

1. **The arithmetic is shown.** 256 + 64 + 32 + 32 = 384 in a /23. TechOps approves the CIDR,
   and a number with no derivation gets sent back.
2. **The AKS subnet is small, and the reason is stated.** Under Overlay only nodes take VNET
   addresses. If the output ever explains the AKS subnet as "sized for pods", the KB
   correction has been lost — that is a regression, not a wording preference.
3. **The Pod CIDR appears as prose, not a subnet row.** It is a separate address space. Putting
   it in the subnet table would be wrong and would corrupt the VNET arithmetic.
4. **Inferred components are labelled as inferred**, with reasons. Never silently expand
   someone's environment.
5. **One open question is asked, not assumed.** Storage for the cluster is genuinely ambiguous
   — ask rather than guessing either way. Ask one, not three.
6. **75% does not trip the flag.** `utilisation_flag.comparison` is `strictly_greater`. This
   example lands on exactly 75.0%, so no "consider the next size up" caveat appears. If one
   does, the comparison has been implemented as `>=`.
7. **The InfoSec gate explains the architecture before the process.** People accept a gate they
   understand. The prior security finding is cited once as evidence, not as a threat.
8. **Parallelism is explicit.** The user's first fear is a six-week serial queue.
9. **Wave 4 and 6 name the real services**, not "Other" — even though `postgres_create` and
   `app_gateway` currently prefill against `RequestType.OTHER`.
10. **Security posture is declarative.** No "you could consider TLS 1.2".
11. **No cost figures.** Bands only.

---

## Negative test — must also pass

Same inventory with `exposure = internal_only`. Assert each of these **individually** — this
test is easy to pass cursorily because the positive case looks convincing.

| # | Assertion |
|---|---|
| N1 | **No** InfoSec gate section anywhere in the output |
| N2 | **No** Application Gateway in the component list |
| N3 | Zero public IPs, stated explicitly |
| N4 | `snet-appgw` **absent** from the subnet table |
| N5 | Arithmetic is **64 + 32 + 32 = 128** |
| N6 | VNET is **/24** (256 capacity, 50% allocated, 128 spare) — not /23, not /22 |
| N7 | Cloudflare subgraph **omitted entirely** from the diagram; no orphan CF or AGW nodes |
| N8 | Pod CIDR still stated — exposure does not affect pod networking |

Expected subnet table for the negative case:

| Subnet | Size | Total |
|---|---|---|
| `snet-aks` | /26 | 64 |
| `snet-vm` | /27 | 32 |
| `snet-pe` | /27 | 32 |
| **Sum** | | **128 → /24 at 50%** |

---

## Changelog — 2.0.0 → 2.1.0

The platform defaults to Azure CNI **Overlay** (`AKS_NETWORK_PLUGIN_MODE=overlay`, Pod CIDR
`10.244.0.0/16`), which the KB previously did not reflect. Under Overlay pods draw from the
Pod CIDR, not the VNET, so only nodes consume VNET addresses.

| | 2.0.0 (classic CNI) | 2.1.0 (Overlay) |
|---|---|---|
| `snet-aks` | /23 (512) | **/26 (64)** |
| Positive sum | 832 | **384** |
| Positive VNET | /22, 81% | **/23, 75%** |
| Utilisation flag | tripped | **not tripped** |
| Negative sum | 320 | **128** |
| Negative VNET | /23 | **/24, 50%** |
| Pod CIDR | not covered | **new section** |

Any assertion still expecting /23 for `snet-aks`, a sum of 832, or an 81% utilisation warning
is stale and must be updated.
