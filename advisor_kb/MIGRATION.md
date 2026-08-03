# Migration notes — adding services to the existing advisor

**Target:** your committed `advisor_kb/` (v1.0.0, storage-only, 5 patterns / 19 questions)
**This package:** additive delta, KB v2.0.0

## Design decision: additive, not a restructure

This delta deliberately follows **your existing per-service file naming**:

```
rules/storage_decision_matrix.yaml      <- yours, untouched
rules/aks_decision_matrix.yaml          <- new
rules/vm_decision_matrix.yaml           <- new
rules/postgres_decision_matrix.yaml     <- new
rules/appgw_decision_matrix.yaml        <- new
rules/platform_constants.yaml           <- new, shared across all services

mapping/storage_request_mapping.yaml    <- yours, untouched
mapping/aks_request_mapping.yaml        <- new
mapping/vm_request_mapping.yaml         <- new
mapping/postgres_request_mapping.yaml   <- new
mapping/appgw_request_mapping.yaml      <- new
```

**Nothing existing is modified or moved.** Your `catalog_loader.py`, your 63-check suite and
the storage path all keep working exactly as they are. The only code change needed is
service-aware file resolution, which is additive.

## Files in this delta

| Path | Count | Notes |
|---|---|---|
| `catalog/` | 7 | aks ×2, vm, postgres, appgw ×2, keyvault |
| `questions/` | 4 | aks, vm, postgres, appgw |
| `rules/` | 5 | 4 service matrices + `platform_constants.yaml` |
| `mapping/` | 4 | aks, vm, postgres, appgw |
| `diagrams/` | 7 | one per new pattern + `environment_full.mmd` |
| `composer/` | 6 | whole-environment composition |
| `templates/` | 1 | `environment_recommendation_template.md` |

Copy straight over the top of `advisor_kb/` — there are **no filename collisions** with v1.

## Code changes required

### 1. `catalog_loader.py` — service-aware resolution

Currently hardcoded to the storage files. Make it resolve by service:

```python
SERVICE_FILES = {
    "storage_account": {
        "questions": "questions/storage_questions.yaml",
        "rules":     "rules/storage_decision_matrix.yaml",
        "mapping":   "mapping/storage_request_mapping.yaml",
    },
    "aks_cluster": {
        "questions": "questions/aks_questions.yaml",
        "rules":     "rules/aks_decision_matrix.yaml",
        "mapping":   "mapping/aks_request_mapping.yaml",
    },
    # vm_create, postgres_create, app_gateway …
}
```

Load `rules/platform_constants.yaml` **once**, shared, and inject it into every service's
rule context. Do not copy it per service.

### 2. Service selection step

The advisor currently assumes storage. Add a first question:

> "What are you looking to set up?"
> Storage · Kubernetes cluster · Virtual machines · Database · Application gateway ·
> **A whole environment**

The last option routes to the composer (Phase 3).

### 3. Two request types don't exist yet

`postgres_request_mapping.yaml` and `appgw_request_mapping.yaml` target
`RequestType.POSTGRES_CREATE` and `RequestType.APP_GATEWAY`, which aren't in `models.py`.

**Options:**
- **(a)** Build those request types first, then wire the mappings.
- **(b)** Ship the advisory now with `RequestType.OTHER` as the prefill target, and switch
  once the real types land.

The mapping files' `user_must_provide` blocks double as the field specification if you build
them. I'd take (b) — the advice is valuable before the request type exists, and the mapping
tells you exactly what to build.

### 4. Existing types that DO exist

`aks_cluster` and `vm_create` map onto your real request types, so those two are wireable
immediately.

## Consistency with your storage implementation

- **`ServiceClass`** is `skip: true` in all four new mappings, matching your decision to
  leave it for manual entry while the mapping is unconfirmed.
- **Semantic vocabulary → form markup.** You found 4 mismatches on storage. Expect the same
  class of issue here: check `identity_type`, `encryption_type`, disk SKU lists and zone
  values against the actual AKS and VM form markup before wiring prefill.
- **Condition strings** use the same grammar your evaluator already parses. The three
  evaluator bugs you fixed should mean these parse cleanly, but the `in [a, b]` and
  `'x' in field` forms are both used — worth a targeted test.

## Suggested commit sequence

```
1. feat(advisor-kb): add platform constants + AKS/VM/PostgreSQL/AppGW/KeyVault patterns
2. feat(advisor): service-aware catalog loading + service selection step
3. feat(advisor): wire AKS and VM advisory end-to-end
4. feat(advisor): PostgreSQL and App Gateway advisory (RequestType.OTHER prefill)
5. feat(advisor): environment composer
```

## Unchanged non-negotiables

Everything in `platform_constants.yaml` is injected globally. The advisor describes these,
never offers them:

- Public network access disabled on PaaS; private endpoint + DNS zone link
- TLS 1.2, CMK via Key Vault Premium + RSA-HSM + UAMI
- `0.0.0.0/0` via hub firewall; gateway route propagation disabled
- Resource locks on Key Vaults and Storage Accounts
- **Any public exposure requires formal InfoSec onboarding** — the one genuinely blocking gate
