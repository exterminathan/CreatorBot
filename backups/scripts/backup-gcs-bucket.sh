#!/usr/bin/env bash
# Mirror a GCS bucket (or prefix) to ../gcs/<bucket-name>.
#
# Usage:   backup-gcs-bucket.sh <gs://bucket[/prefix]> [project-id]
# Example: backup-gcs-bucket.sh gs://lego-art-archive
#          backup-gcs-bucket.sh gs://lego-art-archive/sets my-project
#
# Uses `gcloud storage rsync -r` so re-running only transfers new/changed
# objects. Does NOT delete local files that were removed from the bucket
# (add --delete-unmatched-destination-objects if that behavior is wanted).

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <gs://bucket[/prefix]> [project-id]" >&2
    exit 2
fi

SOURCE="$1"
PROJECT="${2:-}"

if [[ "$SOURCE" != gs://* ]]; then
    echo "error: source must start with gs://" >&2
    exit 2
fi

# Strip gs:// and take first path segment as bucket name for the local dir.
BUCKET_NAME="${SOURCE#gs://}"
BUCKET_NAME="${BUCKET_NAME%%/*}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../gcs/${BUCKET_NAME}"
mkdir -p "$OUT_DIR"

PROJECT_ARG=()
[[ -n "$PROJECT" ]] && PROJECT_ARG=(--project="$PROJECT")

echo "==> Mirroring ${SOURCE} -> ${OUT_DIR}"
gcloud storage rsync -r "$SOURCE" "$OUT_DIR" "${PROJECT_ARG[@]}"

echo "==> Done. Local mirror at ${OUT_DIR}"
