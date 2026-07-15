#!/usr/bin/env bash
# Build, push to ACR, and deploy to AKS.
# Usage: ./k8s/deploy.sh <acr-name> [tag]
set -euo pipefail

ACR="${1:?Usage: $0 <acr-name> [tag]}"
TAG="${2:-latest}"
IMAGE="${ACR}.azurecr.io/subnet-manager:${TAG}"

echo "==> Building image: ${IMAGE}"
docker build -t "${IMAGE}" .

echo "==> Logging in to ACR"
az acr login --name "${ACR}"

echo "==> Pushing image"
docker push "${IMAGE}"

echo "==> Updating deployment image"
# Patch the deployment image in-place (avoids needing to edit the YAML)
kubectl set image deployment/subnet-manager \
  subnet-manager="${IMAGE}" \
  -n network-deployments

echo "==> Waiting for rollout"
kubectl rollout status deployment/subnet-manager -n network-deployments

echo "==> Done. Pod status:"
kubectl get pods -n network-deployments
