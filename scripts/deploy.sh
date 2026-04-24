#!/usr/bin/env bash
#
# scripts/deploy.sh — initial deploy of CreatorBot to Cloud Run
#
# Prerequisites:
#   - scripts/setup.sh has completed successfully on this project
#   - config.py has your real values
#   - .env has your secrets (DISCORD_BOT_TOKEN, GEMINI_API_KEY, WEB_PASSWORD)
#
# What this script does:
#   - Builds the container image with Cloud Build
#   - Creates or replaces the Cloud Run service with your config
#   - Injects secrets from .env as Cloud Run env vars
#   - Prints the service URL
#
# For a quick image-only redeploy after code changes, use scripts/update.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load non-secret values from config.py
eval "$(python3 "$SCRIPT_DIR/_load_config.py")"

# Source secrets from .env
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    echo "ERROR: .env not found in $REPO_ROOT"
    echo "Copy .env.example to .env and fill in your secrets."
    exit 1
fi
set -o allexport
# shellcheck disable=SC1091
source "$REPO_ROOT/.env"
set +o allexport

if [[ -z "${DISCORD_BOT_TOKEN:-}" ]]; then
    echo "ERROR: DISCORD_BOT_TOKEN is not set in .env"
    exit 1
fi
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "ERROR: GEMINI_API_KEY is not set in .env"
    exit 1
fi

echo "────────────────────────────────────────────────────────────"
echo "CreatorBot deploy"
echo "  Project:   $GCP_PROJECT_ID"
echo "  Service:   $CLOUD_RUN_SERVICE"
echo "  Region:    $GCP_REGION"
echo "  Image:     $CONTAINER_IMAGE"
echo "────────────────────────────────────────────────────────────"

echo "→ Building container image (this may take a few minutes)"
gcloud builds submit \
    --tag "$CONTAINER_IMAGE" \
    --project "$GCP_PROJECT_ID" \
    "$REPO_ROOT"

echo "→ Deploying to Cloud Run"
ENV_VARS="DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN},GEMINI_API_KEY=${GEMINI_API_KEY},WEB_PASSWORD=${WEB_PASSWORD:-}"
if [[ -n "${CONFIG_BUCKET:-}" ]]; then
    ENV_VARS="${ENV_VARS},CONFIG_BUCKET=${CONFIG_BUCKET}"
fi

gcloud run deploy "$CLOUD_RUN_SERVICE" \
    --image "$CONTAINER_IMAGE" \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" \
    --service-account "$SERVICE_ACCOUNT" \
    --min-instances "$CLOUD_RUN_MIN_INSTANCES" \
    --max-instances "$CLOUD_RUN_MAX_INSTANCES" \
    --memory "$CLOUD_RUN_MEMORY" \
    --cpu "$CLOUD_RUN_CPU" \
    --no-cpu-throttling \
    --allow-unauthenticated \
    --set-env-vars "$ENV_VARS"

SERVICE_URL=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
    --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" \
    --format='value(status.url)')

echo ""
echo "✓ Deploy complete."
echo ""
echo "Service URL:  $SERVICE_URL"
echo "Health check: $SERVICE_URL/"
echo "Admin panel:  $SERVICE_URL/admin"
echo ""
echo "View live logs:"
echo "   gcloud run services logs read $CLOUD_RUN_SERVICE --region $GCP_REGION --follow"
