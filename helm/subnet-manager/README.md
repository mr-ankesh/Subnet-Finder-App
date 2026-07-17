# Network Copilot — Helm Chart

Deploys **Network Copilot** (Presight R&D's AI-assisted hub & spoke network
operations portal) to Kubernetes/AKS: request portal + admin console, CIDR
allocation, hub integration, firewall policy lifecycle, ZPA/NMO routing,
DNS, decommissioning, audit trail and change ledger with revert.

## Quick start

```bash
# 1. Build & push the image
docker build -t <acr>.azurecr.io/subnet-manager:<tag> .
az acr login --name <acr> && docker push <acr>.azurecr.io/subnet-manager:<tag>

# 2. Prepare an override file (never commit real secrets)
cat > my-values.yaml <<EOF
image:
  repository: <acr>.azurecr.io/subnet-manager
  tag: <tag>
ingress:
  host: azsubnetmanager.presight.ai
  tls:
    secretName: presight-tls        # must exist in the namespace
secrets:
  values:
    FLASK_SECRET_KEY: "$(openssl rand -hex 32)"   # KEEP STABLE FOREVER
    ADMIN_PASSWORD: "<strong-password>"
    AZURE_TENANT_ID: "…"
    AZURE_CLIENT_ID: "…"
    AZURE_CLIENT_SECRET: "…"
    ANTHROPIC_API_KEY: "…"          # or OPENAI_API_KEY / on-prem endpoint
EOF

# 3. Install / upgrade
helm upgrade --install subnet-manager ./helm/subnet-manager \
  -n network-deployments --create-namespace -f my-values.yaml
```

After install, follow the printed NOTES: **import the subnet inventory
first** (`/admin/inventory`), then configure Settings (credentials, hub,
firewall, LLM) — all live, no restarts. Keep **dry-run ON** until verified.

## Key values

| Value | Default | Notes |
|---|---|---|
| `image.repository` / `image.tag` | `<YOUR_ACR>…` / `latest` | Required |
| `replicaCount` | `1` | **Locked** — SQLite single writer; chart fails to render if raised |
| `secrets.values.FLASK_SECRET_KEY` | — | **Required & must stay stable** (encrypts stored settings secrets); chart fails without it |
| `secrets.existingSecret` | `""` | Preferred in prod: reference a Key-Vault-CSI/Sealed secret with the same keys; `secrets.values` is then ignored |
| `persistence.storageClass` / `size` | `managed-csi` / `5Gi` | Azure Disk only — Azure Files breaks SQLite locking |
| `persistence.existingClaim` | `""` | Reuse an existing disk |
| `ingress.enabled` / `host` / `tls` | nginx + TLS | Set your hostname; cert secret must exist in the namespace |
| `config.*` | see values.yaml | Bootstrap env only — nearly everything is editable live in `/admin/settings` (DB override wins) |
| `extraConfig` | `{}` | Additional env entries merged over `config` |

## Guard rails baked into the templates

- `replicaCount > 1` → render fails (SQLite; migrate to PostgreSQL first).
- Missing `FLASK_SECRET_KEY` (without `existingSecret`) → render fails.
- ConfigMap/Secret changes roll the pod automatically (checksum annotations).
- `Recreate` strategy, non-root uid 10001, `fsGroup` for the PVC, no
  privilege escalation, all capabilities dropped, `/health` probes.

## Operations

- **Backup**: snapshot the PVC — `requests.db` (+`-wal`/`-shm`) holds all
  state (requests, inventory, settings, audit, change ledger).
- **Upgrade**: `helm upgrade … --set image.tag=<new>` (seconds of downtime
  by design). Rollback: `helm rollback subnet-manager`.
- More detail: `docs/DEPLOYMENT.md`; architecture: `docs/HOW_IT_WORKS.md`.
