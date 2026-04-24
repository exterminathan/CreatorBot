#!/usr/bin/env bash
# Export a Firestore database and mirror it to ../firestore/<database>.
#
# Usage:   backup-firestore.sh <project-id> <database> [staging-bucket]
# Example: backup-firestore.sh project-a89ff80d-7ecd-456f-aee "(default)"
#          backup-firestore.sh project-a89ff80d-7ecd-456f-aee toolset-database
#
# staging-bucket defaults to <project-id>-firestore-backups (multi-region US).
# The GCS export artifacts remain in the staging bucket after download so the
# same export can be re-imported into another project with:
#   gcloud firestore import gs://<bucket>/<database>/<database>.overall_export_metadata \
#     --database=<target-db> --project=<target-project>

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <project-id> <database> [staging-bucket]" >&2
    exit 2
fi

PROJECT="$1"
DATABASE="$2"
STAGING_BUCKET="${3:-${PROJECT}-firestore-backups}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../firestore/${DATABASE}"
GS_PREFIX="gs://${STAGING_BUCKET}/${DATABASE}"

echo "==> Ensuring staging bucket gs://${STAGING_BUCKET} exists"
if ! gcloud storage buckets describe "gs://${STAGING_BUCKET}" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${STAGING_BUCKET}" \
        --project="$PROJECT" \
        --location=US \
        --uniform-bucket-level-access
fi

echo "==> Starting Firestore export: ${DATABASE} -> ${GS_PREFIX}"
gcloud firestore export "$GS_PREFIX" \
    --database="$DATABASE" \
    --project="$PROJECT"

echo "==> Downloading to ${OUT_DIR}"
mkdir -p "$OUT_DIR"
gcloud storage cp -r "${GS_PREFIX}/*" "$OUT_DIR/" --project="$PROJECT"

echo "==> Done. Local backup at ${OUT_DIR}"
echo "    GCS export kept at ${GS_PREFIX} (delete manually if no longer needed)."
