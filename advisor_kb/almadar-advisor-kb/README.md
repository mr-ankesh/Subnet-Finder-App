# AlMadar AI Architecture Advisor — Knowledge Base (Storage V1)

This is the **source of truth** for the AI Architecture Advisor. The LLM does **not**
design architecture. It classifies intent, picks a pattern from `catalog/`, applies
`rules/`, and explains the result using `templates/`.

```
Rules decide.  LLM explains.  Forms validate.  Azure deploys.
```

## Provenance

Every design decision in this KB is traceable to one of two Microsoft/Kyndryl
deliverables held in the Presight-Azure workspace. Nothing here is invented.

| Source | Used for |
|---|---|
| **Presight Storage Design Document** (Microsoft ISD, v1 Draft, 25-Mar-2024) | Replication (ZRS), performance tier (GPv2 Standard), CMK mandate, TLS 1.2, access tiers, private endpoints, soft delete/versioning/WORM, Defender for Storage, Blobfuse2, NFS options, service limits |
| **G42-AzurePlatformDesign-v1.2** (Microsoft, 18-Apr-2024) | Hub-and-spoke topology, subscription model, private DNS zone centralisation, "deny public PaaS endpoints" policy initiative, naming/tagging conventions, CMK + Key Vault Premium + RSA-HSM + UAMI, resource locks, UAE North primary / UAE Central secondary, Confidential Corp sovereignty path |

Each pattern and rule carries a `source:` field pointing back to the document and
section. **When you extend this KB, keep that discipline** — an entry with no source
is an opinion, not a Presight standard, and the advisor must not present it as one.

## Directory map

| Path | Deliverable | Purpose |
|---|---|---|
| `catalog/` | **1. Architecture Catalog** | Approved Presight patterns. One YAML per pattern. |
| `questions/` | **2. Question Bank** | Plain-English intake flow, in ask-order. |
| `rules/` | **3. Decision Matrix** | Deterministic rules. Runs *before* the LLM. |
| `mapping/` | **4. Request Field Mapping** | Answers → AlMadar request-type fields/tags. |
| `templates/` | **5. Recommendation Template** | Fixed output shape + LLM system prompts. |
| `diagrams/` | **6. Diagram Templates** | Mermaid per pattern. Placeholders only. |

## Runtime flow

```
User: "I need a storage account"
   ↓
questions/storage_questions.yaml      → ask in order, honour skip_if / stop_if
   ↓
rules/storage_decision_matrix.yaml    → hard constraints, blockers, derived values
   ↓
catalog/*.yaml                        → score + select pattern (deterministic)
   ↓
LLM                                   → explain the selection only
   ↓
templates/recommendation_template.md  → render fixed sections
   ↓
mapping/storage_request_mapping.yaml  → prefill Storage Account request
   ↓
diagrams/*.mmd                        → optional, on user request
```

## Non-negotiables the advisor must never override

These are enforced by Azure Policy or hardcoded in `create_storage_account()`.
The advisor **describes** them; it never offers them as choices.

- Public network access **disabled** — a `Landing Zones/Corp` policy initiative denies
  PaaS resources with exposed public endpoints.
- Private endpoint + private DNS zone integration — enforced by the
  "Configure Azure PaaS services to use private DNS zones" initiative.
- TLS 1.2 minimum, HTTPS-only, shared-key access disabled, blob public access disabled,
  infrastructure encryption on, network default action Deny.
- **ZRS** replication for all Presight storage.
- **CMK** encryption — Presight encrypts all data with customer-managed keys.
- Resource lock on storage accounts (DD-GOV-7 lists Storage Accounts as lock targets).

If a user asks for public access, the advisor must say it is not available in AlMadar
and route them to a policy-exception conversation — not quietly generate the request.

## Wiring into AlMadar

Load once at startup, cache per-process is fine (this is static config, not Azure state):

```python
from advisor.catalog_loader import load_catalog
CATALOG   = load_catalog("architecture_catalog/storage")
QUESTIONS = load_yaml("questions/storage_questions.yaml")
RULES     = load_yaml("rules/storage_decision_matrix.yaml")
MAPPING   = load_yaml("mapping/storage_request_mapping.yaml")
```

Version the KB with `kb_version` (top of each file) and surface it in the advisor
footer, so a recommendation can be traced to the KB revision that produced it.

## Extending beyond storage

The five file types are service-agnostic. To add VM or AKS advisory, add
`catalog/vm_*.yaml`, `questions/vm_questions.yaml`, etc. Do **not** widen the storage
files. Keep one question bank and one decision matrix per service so a bad rule in one
service can never mis-route another.
