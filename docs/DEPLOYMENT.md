# Production Deployment (Docker / Kubernetes)

The app ships as a single container (gunicorn, non-root user `10001`) with a
SQLite database on a persistent volume. **Run exactly one replica** — SQLite
has a single writer. All manifests live in `k8s/`.

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

## 3 — Deploy

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

Log in at `/admin/login`, open **Settings**:

1. **Azure Credentials** → fill in / test connection.
2. **Hub & Subscriptions, Firewall, Routing** → your hub topology.
3. **AI Agent / LLM** → provider, model, API key (stored encrypted).
4. **Safety** → keep **Dry-run ON** until you've verified everything.

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
