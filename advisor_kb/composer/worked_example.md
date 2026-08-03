# Worked example — the composer acceptance test

**kb_version:** 2.0.0

This is the reference output and the acceptance test for the environment composer. Given
this input, the implementation must produce output of this shape and depth.

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

**Recommended VNET size: /22**

| Subnet | Purpose | Size | Usable | Sizing basis |
|---|---|---|---|---|
| `snet-appgw` | Application Gateway | **/24** | 251 | Dedicated subnet, required. Sized so the gateway can autoscale later. |
| `snet-aks` | AKS node pool | **/23** | 507 | Azure CNI uses one IP per **pod**. 6 nodes × 31 = 186 today; /23 carries you to ~16 nodes. |
| `snet-vm` | Virtual machines | **/27** | 27 | 10 VMs today, headroom to 27. |
| `snet-pe` | Private endpoints | **/27** | 27 | PostgreSQL, Key Vault, ACR, Storage. These accumulate. |

**The arithmetic:** 256 + 512 + 32 + 32 = **832 addresses**, which fits a /22 (1024) at
**81% allocated**, leaving 192 spare.

That's workable, but tighter than I'd normally leave things. If you expect the cluster to go
beyond ~16 nodes or the VM count to grow substantially, ask TechOps for a **/21** instead —
the cost of asking now is zero, and subnets can't be resized once resources are deployed
into them.

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
| **4 — workloads** | PostgreSQL + PE + DNS · AKS cluster · VM(s) ×10 | Parallel. One VM request covers all ten, numbered `-001`…`-010`. |
| **5 — access** | Firewall egress rules · ZPA routing · private DNS A records | Parallel. |
| **6 — front door** | Application Gateway · Cloudflare allow-list rule · Cloudflare DNS record | The DNS record waits on wave 0. |

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
- ⬜ Confirm expected AKS node count so the subnet is sized right first time
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

1. **The arithmetic is shown.** 256 + 512 + 32 + 32 = 832 in a /22. TechOps approves the
   CIDR, and a number with no derivation gets sent back.
2. **The tight fit is admitted.** 81% allocated is workable but flagged, with /21 offered.
   Do not round the awkwardness away.
3. **Inferred components are labelled as inferred**, with reasons. Never silently expand
   someone's environment.
4. **One open question is asked, not assumed.** Storage for the cluster is genuinely
   ambiguous — ask rather than guessing either way. Ask one, not three.
5. **The InfoSec gate explains the architecture before the process.** People accept a gate
   they understand. The prior security finding is cited once as evidence, not as a threat.
6. **Parallelism is explicit.** The user's first fear is a six-week serial queue. Tell them
   it isn't.
7. **Security posture is stated, never offered.** No "you could consider TLS 1.2".
8. **No cost figures.** Bands only, and only where they inform a decision.

## Negative test — must also pass

Same inventory with `exposure = internal_only` must produce:
- **No** InfoSec gate section
- **No** Application Gateway in the component list
- **Zero** public IPs, stated explicitly
- `snet-appgw` **absent** from the subnet table, and the VNET re-sized accordingly
  (256 + 32 + 32 = 320 → /23, not /22)
- The Cloudflare subgraph **omitted entirely** from `environment_full.mmd`
