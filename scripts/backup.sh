#!/usr/bin/env bash
#
# scripts/backup.sh — snapshot CreatorBot state + config to a local archive
#
# What's backed up:
#   - data/config.json       runtime state (channels, admins, settings, etc.)
#   - data/persona*.json     persona definition(s)
#   - data/transcripts/      chat transcripts fed into the system prompt
#   - data/*.jpg             avatar / frame images
#   - config.py              per-deployment config (GCP project, scaling, etc.)
#   - .env                   secrets (bot token, API key, etc.)
#
# If CONFIG_BUCKET is set in config.py, the authoritative config.json is
# pulled from GCS before archiving so the backup reflects live state.
#
# Backups are written to backups/YYYY-MM-DD_HHMMSS.tar.gz (relative to repo
# root). The backups/ directory is git-ignored so archives stay local.
#
# Usage:
#   bash scripts/backup.sh
#   bash scripts/backup.sh --no-gcs   # skip GCS pull even if bucket is set

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_GCS=false
for arg in "$@"; do
    [[ "$arg" == "--no-gcs" ]] && SKIP_GCS=true
done

# Load deployment config values
eval "$(python3 "$SCRIPT_DIR/_load_config.py")"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
BACKUP_DIR="$REPO_ROOT/backups"
ARCHIVE="$BACKUP_DIR/${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "────────────────────────────────────────────────────────────"
echo "CreatorBot backup"
echo "  Timestamp: $TIMESTAMP"
echo "  Archive:   $ARCHIVE"
[[ -n "$CONFIG_BUCKET" ]] && echo "  GCS bucket: gs://$CONFIG_BUCKET"
echo "────────────────────────────────────────────────────────────"

# If a GCS bucket is configured, pull the live config.json down first so the
# backup reflects what's actually running, not a stale local copy.
if [[ -n "$CONFIG_BUCKET" && "$SKIP_GCS" == false ]]; then
    echo "→ Pulling live config.json from GCS (gs://$CONFIG_BUCKET/config.json)"
    if gsutil cp "gs://$CONFIG_BUCKET/config.json" "$REPO_ROOT/data/config.json" 2>/dev/null; then
        echo "   done"
    else
        echo "   WARNING: GCS pull failed — archiving local copy instead"
    fi
else
    [[ "$SKIP_GCS" == true ]] && echo "→ Skipping GCS pull (--no-gcs)"
fi

# Build the list of paths to include, skipping any that don't exist.
INCLUDE=()

add_if_exists() {
    local path="$1"
    if [[ -e "$REPO_ROOT/$path" ]]; then
        INCLUDE+=("$path")
    else
        echo "   (skipping $path — not found)"
    fi
}

echo "→ Collecting files"
add_if_exists "data/config.json"
add_if_exists "data/persona.json"
add_if_exists "data/persona.local.json"
add_if_exists "data/transcripts"
add_if_exists "data/cyrframe.jpg"
add_if_exists "data/cyNewPfp.jpg"
add_if_exists "data/avatar.jpg"
add_if_exists "config.py"
add_if_exists ".env"

if [[ ${#INCLUDE[@]} -eq 0 ]]; then
    echo "ERROR: nothing to back up — no expected files found under $REPO_ROOT"
    exit 1
fi

echo "→ Writing archive"
tar -czf "$ARCHIVE" -C "$REPO_ROOT" "${INCLUDE[@]}"

SIZE="$(du -sh "$ARCHIVE" | cut -f1)"

echo ""
echo "✓ Backup complete."
echo ""
echo "  Archive: $ARCHIVE"
echo "  Size:    $SIZE"
echo "  Files:"
tar -tzf "$ARCHIVE" | sed 's/^/    /'
echo ""
echo "To restore config.json to GCS:"
echo "  tar -xzf $ARCHIVE data/config.json"
echo "  gsutil cp data/config.json gs://$CONFIG_BUCKET/config.json"
