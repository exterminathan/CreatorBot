"""CreatorBot — all per-deployment customization lives here.

Copy this file to `config.py` and fill in your values.
Secrets are loaded from `.env` (copy `.env.example` to `.env`).

This is the ONE file you need to edit (plus `.env`) to run your own bot.
Every other file in the repo is generic framework code.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── GCP / Cloud Run ──────────────────────────────────────────────
# Your Google Cloud project ID (where Cloud Run will deploy the bot).
GCP_PROJECT_ID = "your-gcp-project-id"

# Region for Cloud Run + (optionally) GCS bucket.
GCP_REGION = "us-central1"

# Cloud Run service + container image names. Change if you want a different
# identity on GCP; the defaults are fine for most people.
CLOUD_RUN_SERVICE = "creatorbot"
CONTAINER_IMAGE = f"gcr.io/{GCP_PROJECT_ID}/{CLOUD_RUN_SERVICE}"

# Cloud Run scaling / resources.
CLOUD_RUN_MIN_INSTANCES = 1   # 1 = always-on (no cold starts, costs more)
CLOUD_RUN_MAX_INSTANCES = 1   # keep at 1 unless you know you need horizontal scale
CLOUD_RUN_MEMORY = "512Mi"
CLOUD_RUN_CPU = "1"

# Service account created by scripts/setup.sh and used by the Cloud Run service.
SERVICE_ACCOUNT = f"{CLOUD_RUN_SERVICE}-sa@{GCP_PROJECT_ID}.iam.gserviceaccount.com"

# Optional: persist runtime state (active channels, admins, etc.) to a GCS
# bucket instead of local disk. Useful if you run multiple replicas or want
# state to survive container redeploys.
#
# Set to a bucket name like "creatorbot-state" to enable. scripts/setup.sh will
# create the bucket if it doesn't exist. Leave as None to use local disk only.
CONFIG_BUCKET: str | None = None


# ── Discord ──────────────────────────────────────────────────────
# Channel where admin-only slash commands are invoked (you can run commands
# from anywhere if you're in the admin list, but this is the "home" channel).
ADMIN_CHANNEL_ID = 0

# Your Discord user ID — becomes the bot owner / primary admin.
ADMIN_USER_ID = 0

# Slash command root name. With "bot", commands become /bot newpost, /bot enable, etc.
COMMAND_GROUP_NAME = "bot"


# ── Bot persona / webhook ────────────────────────────────────────
# Username shown on webhook-posted messages in Discord.
BOT_DISPLAY_NAME = "CreatorBot"

# Optional: URL to an avatar image for webhook messages. If None, the bot's
# own avatar (set in the Discord Developer Portal) is used automatically.
BOT_AVATAR_URL: str | None = None

# Internal webhook identifier. Change this if you run multiple bots in the
# same channel and need to distinguish their webhooks.
WEBHOOK_NAME = "CreatorBot-Hook"

# Persona JSON file (relative to data/). The loader prefers `persona.local.json`
# if present so you can keep your real persona uncommitted.
PERSONA_FILE = "persona.json"


# ── AI ───────────────────────────────────────────────────────────
# Google Gemini model name. See https://ai.google.dev/gemini-api/docs/models
GEMINI_MODEL = "gemini-2.5-flash-lite"


# ── Secrets (loaded from .env) ───────────────────────────────────
# Never put real secret values in this file — they belong in .env.
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")


# ── Paths (don't edit unless you know what you're doing) ─────────
REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
