# Production Deployment (Docker / Kubernetes)

The app ships as a single container (gunicorn, non-root user `10001`) with a
SQLite database on a persistent volume. **Run exactly one replica** — SQLite
has a single writer.

Two deployment options:
- **Helm chart** (preferred): `helm/subnet-manager/` — see §3a.
- **Raw manifests**: `k8s/` — see §3b.

## 1 — Image

```bash
docker build -t <acr>.azurecr.io/subnet-manager:<tag> .
az acr login --name <acr>
docker push <acr>.azurecr.io/subnet-manager:<tag>
```

Local smoke test:

```bash
docker run --rm -p 8080:8080 \
  -e FLASK_SECRET_KEY=$(openssl rand -hex 32) \
  -e ADMIN_PASSWORD=test \
  -v subnet-data:/app/data \
  <acr>.azurecr.io/subnet-manager:<tag>
curl http://localhost:8080/health
```

## 2 — Pre-deploy checklist

| Item | Where | Notes |
|---|---|---|
| `FLASK_SECRET_KEY` | `k8s/02-secret.yaml` | **Strong + stable.** It encrypts settings secrets at rest and signs sessions — rotating it invalidates both. `openssl rand -hex 32` |
| `ADMIN_PASSWORD` | `k8s/02-secret.yaml` | Change from `changeme` |
| Azure SP creds | `k8s/02-secret.yaml` | Or switch to Managed Identity in Settings → Azure Credentials after deploy |
| LLM key(s) | `k8s/02-secret.yaml` | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` |
| Image reference | `k8s/04-deployment.yaml` | Replace `<YOUR_ACR>` |
| Hostname + TLS | `k8s/06-ingress.yaml` | Copy the wildcard cert secret into the `network-deployments` namespace |
| Storage class | `k8s/03-pvc.yaml` | Default is `managed-csi` (AKS) |

Secrets hygiene: prefer Azure Key Vault CSI driver or Sealed Secrets over
committing values into `02-secret.yaml`.

## 3a — Deploy with Helm (preferred)

```bash
# Values you don't want in git go in an override file or --set flags
cat > my-values.yaml <<EOF
image:
  repository: <acr>.azurecr.io/subnet-manager
  tag: <tag>
ingress:
  host: azsubnetmanager.presight.ai
secrets:
  values:
    FLASK_SECRET_KEY: "$(openssl rand -hex 32)"   # keep this stable forever
    ADMIN_PASSWORD: "<strong-password>"
    AZURE_TENANT_ID: "..."
    AZURE_CLIENT_ID: "..."
    AZURE_CLIENT_SECRET: "..."
EOF

helm upgrade --install subnet-manager helm/subnet-manager \
  -n network-deployments --create-namespace \
  -f my-values.yaml
```

Notes:
- For production secret hygiene, create the secret out-of-band and set
  `secrets.existingSecret: <name>` instead of putting values in a file.
- The chart refuses `replicaCount > 1` (SQLite) and a missing
  `FLASK_SECRET_KEY`; config/secret changes roll the pod automatically
  via checksum annotations.
- Reuse an existing disk with `persistence.existingClaim`.

## 3b — Deploy with raw manifests

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/            # everything else
# or, after the first apply:
./k8s/deploy.sh <acr-name> <tag> # build + push + rollout
```

Verify:

```bash
kubectl get pods -n network-deployments
kubectl logs deploy/subnet-manager -n network-deployments
curl https://<host>/health
```

## 4 — Post-deploy configuration (no restarts)

The image carries **no data** — a fresh deployment starts with an empty
database: no requests and no subnet allocations from any other environment.

Log in at `/admin/login`, then:

1. **Import the current subnet inventory** — the home page shows a
   "Fresh deployment" banner linking to `/admin/inventory`. Paste your
   allocations (one `CIDR, purpose, requested_by, allocated_by, status`
   per line) or upload an Excel/CSV export (`Subnet` column required).
   Do this FIRST — until the real allocation state is loaded, the
   allocator would hand out ranges that are already in use. Imports are
   validated (CIDR, pool membership, overlaps) and audited.
2. Open **Settings**:
   - **Azure Credentials** → fill in / test connection.
   - **Hub & Subscriptions, Firewall, Routing** → your hub topology.
   - **AI Agent / LLM** → provider, model, API key (stored encrypted).
   - **Safety** → keep **Dry-run ON** until you've verified everything.

Every setting resolves live: DB override → env var → default.

## 5 — Operations

- **Backups**: the entire state is `/app/data/requests.db` (+ WAL). Snapshot
  the PVC or `kubectl cp` the file on a schedule. WAL means copying while
  running is safe if you copy `requests.db`, `-wal` and `-shm` together.
- **Upgrades**: `./k8s/deploy.sh <acr> <new-tag>` — strategy is `Recreate`,
  expect a few seconds of downtime (required for SQLite).
- **Health**: `/health` (unauthenticated) checks DB connectivity; wired to
  liveness + readiness probes and the container HEALTHCHECK.
- **Scaling**: do NOT raise `replicas`. If HA becomes a requirement, migrate
  SQLite → PostgreSQL first (SQLAlchemy makes this mostly a URI change plus
  the raw-sqlite helpers in `db_utils.py`/`audit.py`/`settings_store.py`).
- **SSO**: when ready, follow `docs/KEYCLOAK.md`.
