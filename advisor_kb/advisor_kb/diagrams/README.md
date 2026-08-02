# Diagram templates

**kb_version:** 1.0.0

One Mermaid template per catalog pattern. The advisor **fills placeholders**; it never
generates diagram syntax from scratch. This guarantees every diagram is topologically
correct and visually consistent, regardless of what the LLM does.

## Placeholders

| Token | Source | Fallback when unknown |
|---|---|---|
| `{APP}` | `application_name` | `Your workload` |
| `{ENGINE}` | `application_name` | `Analytics engine` |
| `{VNET}` | `vnet_name` | `<your spoke VNET>` |
| `{SUBNET}` | `subnet_name` | `<your subnet>` |
| `{SA_NAME}` | `storage_account_name` | `<storage account>` |
| `{ENV}` | `environment` (expanded) | `<environment>` |
| `{PROTOCOL}` | `access_protocol` | `SMB / NFS` |
| `{RETENTION}` | `retention_period` | `<retention period>` |
| `{END_DATE}` | `workload_end_date` | `<agreed end date>` |

Never leave a raw `{TOKEN}` in rendered output — substitute the fallback.

## Rendering

Client-side with Mermaid.js is simplest and needs no new Python dependency:

```html
<div class="mermaid">{{ diagram_source }}</div>
<script type="module">
  import mermaid from '/static/vendor/mermaid.esm.min.mjs';
  mermaid.initialize({ startOnLoad: true, theme: 'dark', securityLevel: 'strict' });
</script>
```

Vendor the Mermaid file into `static/` rather than loading from a CDN — the app runs in
environments where outbound CDN access isn't guaranteed, and a CDN script tag is an
avoidable supply-chain dependency.

Set `securityLevel: 'strict'`. Placeholder values come from user input, and strict mode
prevents HTML injection through a crafted application name. Additionally, strip or escape
`<`, `>`, `"` and backticks from every substituted value before rendering.

## Legend

| Style | Meaning |
|---|---|
| Solid arrow `-->` | Data path |
| Bold arrow `==>` | Private link connection |
| Dotted arrow `-.->` | Name resolution (DNS) |
| Green nodes | Storage and encryption |
| Blue nodes | Network components |
| Amber nodes | Something requiring attention (archive rehydration, de-provisioning date) |

## Rules

- The diagram reflects the **selected pattern**, not the user's phrasing.
- The ZPA subgraph renders **only** when `derived.zpa_routing_required == true`.
  Otherwise omit those nodes entirely — an unused ZPA box confuses more than it explains.
- The `dfs` + `blob` dual endpoint in the data lake diagram renders both only when the
  engine is confirmed to use both. Otherwise drop the second endpoint and its DNS zone.
- Never draw a public internet path to storage. It does not exist in any Presight pattern.
- Diagrams are illustrative. Add a caption: *"Illustrative — final topology is confirmed
  by the platform team during implementation."*

## Adding a diagram for a new pattern

1. Copy the closest existing `.mmd`.
2. Keep the subgraph structure (Consumer → Spoke VNET → Hub DNS → Azure Storage).
3. Keep the `classDef` block verbatim so styling stays consistent.
4. Reference the filename from the pattern's `diagram:` field.
5. Add any new placeholder to the table above.
