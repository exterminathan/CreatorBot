#!/bin/bash
# =============================================================================
# CyBot — Cloud Run Deployment Script
# Run this from your local machine inside the CyBot directory.
# Prereqs: gcloud CLI installed and authenticated, project already set.
# =============================================================================
set -e

PROJECT_ID="project-a89ff80d-7ecd-456f-aee"
REGION="us-central1"
SERVICE_NAME="cybot"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo ""
echo "=============================="
echo " CyBot Cloud Run Deployment"
echo " Project: $PROJECT_ID"
echo "=============================="
echo ""

# ── Step 0: Ensure Vertex AI API is enabled ─────────────────────────────────
echo "[0/3] Ensuring Vertex AI API is enabled..."
gcloud services enable aiplatform.googleapis.com --project "$PROJECT_ID" 2>/dev/null || true

# ── Step 1: Build container image with Cloud Build ──────────────────────────
echo "[1/3] Building container image via Cloud Build..."
gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID" .

# ── Step 2: Load env vars from .env ─────────────────────────────────────────
echo "[2/3] Reading .env for Cloud Run env vars..."

if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and fill it in."
    exit 1
fi

# Build --set-env-vars flag from .env (skip comments and blank lines)
ENV_VARS=""
while IFS= read -r line || [ -n "$line" ]; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    # Skip lines without =
    [[ "$line" != *"="* ]] && continue
    # Skip lines where value is empty
    key="${line%%=*}"
    val="${line#*=}"
    [[ -z "$val" ]] && continue
    if [ -z "$ENV_VARS" ]; then
        ENV_VARS="$key=$val"
    else
        ENV_VARS="$ENV_VARS,$key=$val"
    fi
done < .env

# ── Step 3: Deploy to Cloud Run ─────────────────────────────────────────────
echo "[3/3] Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" --image "$IMAGE" --region "$REGION" --platform managed --no-allow-unauthenticated --set-env-vars "$ENV_VARS" --no-cpu-throttling --min-instances 1 --max-instances 1 --memory 512Mi --cpu 1 --timeout 3600

echo ""
echo "=============================="
echo " Deployed!"
echo "=============================="
echo ""
echo "Check status:  gcloud run services describe $SERVICE_NAME --region $REGION"
echo "View logs:     gcloud run services logs read $SERVICE_NAME --region $REGION"
echo ""
