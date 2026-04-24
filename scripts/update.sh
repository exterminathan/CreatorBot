#!/usr/bin/env bash
#
# scripts/update.sh — redeploy CreatorBot after code changes
#
# Use this instead of deploy.sh for routine updates. It rebuilds the container
# and pushes the new image to the existing Cloud Run service without touching
# env vars, IAM, or scaling config.
#
# If you changed config.py values (scaling, region, env vars), use deploy.sh
# instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

eval "$(python3 "$SCRIPT_DIR/_load_config.py")"

echo "────────────────────────────────────────────────────────────"
echo "CreatorBot update (image-only)"
echo "  Project:  $GCP_PROJECT_ID"
echo "  Service:  $CLOUD_RUN_SERVICE"
echo "  Region:   $GCP_REGION"
echo "  Image:    $CONTAINER_IMAGE"
echo "────────────────────────────────────────────────────────────"

echo "→ Building container image"
gcloud builds submit \
    --tag "$CONTAINER_IMAGE" \
    --project "$GCP_PROJECT_ID" \
    "$REPO_ROOT"

echo "→ Updating Cloud Run service"
gcloud run services update "$CLOUD_RUN_SERVICE" \
    --image "$CONTAINER_IMAGE" \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION"

SERVICE_URL=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" \
    --format='value(status.url)')

echo ""
echo "✓ Update complete."
echo "Service URL: $SERVICE_URL"
