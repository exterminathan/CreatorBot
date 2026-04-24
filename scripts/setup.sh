#!/usr/bin/env bash
#
# scripts/setup.sh — one-time GCP bootstrap for CreatorBot
#
# Prerequisites (you do these manually before running this script):
#   1. You have an active GCP project with billing (or free credits) linked
#   2. You've run:  gcloud auth login
#   3. You've copied config.example.py → config.py and filled in your values
#   4. You've copied .env.example → .env and filled in your secrets
#
# What this script does (all idempotent — safe to re-run):
#   - Sets the active gcloud project to your GCP_PROJECT_ID
#   - Enables required Cloud APIs (Run, Build, Container Registry, IAM, Logging,
#     and Storage if you're using CONFIG_BUCKET)
#   - Creates a service account for the Cloud Run service
#   - Grants minimal IAM roles to that service account
#   - Creates the GCS bucket if CONFIG_BUCKET is set
#
# After this completes, run scripts/deploy.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load values from config.py into the environment
eval "$(python3 "$SCRIPT_DIR/_load_config.py")"

if [[ "$GCP_PROJECT_ID" == "your-gcp-project-id" || -z "$GCP_PROJECT_ID" ]]; then
    echo "ERROR: GCP_PROJECT_ID is unset or still the placeholder value."
    echo "Edit config.py and set it to your real GCP project ID."
    exit 1
fi

echo "────────────────────────────────────────────────────────────"
echo "CreatorBot GCP setup"
echo "  Project:         $GCP_PROJECT_ID"
echo "  Region:          $GCP_REGION"
echo "  Cloud Run:       $CLOUD_RUN_SERVICE"
echo "  Service account: $SERVICE_ACCOUNT"
echo "  GCS bucket:      ${CONFIG_BUCKET:-<not set>}"
echo "────────────────────────────────────────────────────────────"

echo "→ Setting active gcloud project"
gcloud config set project "$GCP_PROJECT_ID"

echo "→ Enabling required APIs (this may take a minute)"
REQUIRED_APIS=(
    "run.googleapis.com"
    "cloudbuild.googleapis.com"
    "artifactregistry.googleapis.com"
    "containerregistry.googleapis.com"
    "iam.googleapis.com"
    "logging.googleapis.com"
)
if [[ -n "$CONFIG_BUCKET" ]]; then
    REQUIRED_APIS+=("storage.googleapis.com")
fi
gcloud services enable "${REQUIRED_APIS[@]}"

SA_ACCOUNT_ID="${CLOUD_RUN_SERVICE}-sa"

echo "→ Ensuring service account exists: $SERVICE_ACCOUNT"
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SA_ACCOUNT_ID" \
        --display-name="CreatorBot runtime service account"
else
    echo "   (already exists, skipping)"
fi

echo "→ Granting IAM roles to service account"
for role in \
    "roles/run.invoker" \
    "roles/logging.logWriter"; do
    gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="$role" \
        --condition=None \
        >/dev/null
    echo "   granted $role"
done

if [[ -n "$CONFIG_BUCKET" ]]; then
    echo "→ Ensuring GCS bucket exists: gs://$CONFIG_BUCKET"
    if ! gsutil ls -b "gs://$CONFIG_BUCKET" >/dev/null 2>&1; then
        gsutil mb -p "$GCP_PROJECT_ID" -l "$GCP_REGION" "gs://$CONFIG_BUCKET"
    else
        echo "   (already exists, skipping)"
    fi
    echo "→ Granting bucket access to service account"
    gsutil iam ch "serviceAccount:$SERVICE_ACCOUNT:roles/storage.objectAdmin" \
        "gs://$CONFIG_BUCKET" >/dev/null
fi

echo ""
echo "✓ Setup complete."
echo ""
echo "Next step:"
echo "   scripts/deploy.sh"
