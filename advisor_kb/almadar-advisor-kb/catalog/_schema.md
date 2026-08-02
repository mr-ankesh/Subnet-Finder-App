# Catalog schema

Every pattern file in `catalog/` must contain these keys. The pattern matcher reads
them positionally — do not rename.

| Key | Type | Meaning |
|---|---|---|
| `id` | string | Stable identifier. Never reuse or rename; referenced by mapping + diagrams. |
| `name` | string | Human label shown in the recommendation header. |
| `service` | string | AlMadar request type family (`storage_account`). |
| `status` | enum | `approved` \| `conditional` \| `exception` — see below. |
| `summary` | string | One sentence, shown under the header. |
| `when_to_use` | list | Plain-English triggers, used for LLM classification. |
| `not_for` | list | Explicit anti-triggers. Prevents over-matching. |
| `match` | object | Deterministic scoring inputs (`required`, `preferred`, `disqualify`). |
| `design` | object | The actual Presight-approved configuration. |
| `security_floor` | object | Non-negotiable settings. Advisor states, never offers. |
| `required_requests` | list | Ordered AlMadar requests the user must raise. |
| `prerequisites` | list | Things that must exist before request #1. |
| `diagram` | string | Filename in `diagrams/`. |
| `cost_band` | enum | `$` \| `$$` \| `$$$` — relative only, never quote figures. |
| `source` | list | Document + section for every non-obvious design value. |

## `status` semantics

- **`approved`** — recommend freely. Matches the Presight design documents directly.
- **`conditional`** — recommend only when the stated condition is met; the
  recommendation must surface the condition to the user.
- **`exception`** — never auto-recommend. Requires a named approver and a policy
  exception. The advisor may only *mention* it exists and route to that process.

## `match` scoring

```yaml
match:
  required:            # ALL must be true, else pattern is excluded
    data_shape: [unstructured_objects]
  preferred:           # each true adds +1
    access_protocol: [rest_sdk, blobfuse]
  disqualify:          # ANY true excludes the pattern outright
    data_classification: [restricted]
```

Score = count of satisfied `preferred`. Ties break by catalog file order (declare the
most common pattern first). If two patterns tie **and** differ in `status`, prefer
`approved` over `conditional`. If the winning score is 0, do not guess — ask the
disambiguating question listed in `rules/storage_decision_matrix.yaml` under
`tiebreak_questions`.

## Rules that apply to every pattern

Do not restate these per-pattern; the renderer injects them:

- Region UAE North (`aen`) unless a justification is supplied.
- ZRS replication.
- CMK encryption via Key Vault Premium, RSA-HSM key, user-assigned managed identity.
- Public network access disabled; private endpoint + private DNS zone.
- TLS 1.2, HTTPS-only, shared-key disabled, infra encryption on, default action Deny.
- Resource lock applied post-deploy.
- Diagnostic + security logs to the BU Operational and Security Log Analytics Workspaces.
