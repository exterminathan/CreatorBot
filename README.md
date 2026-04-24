# CreatorBot

Discord bot framework that posts AI-generated messages as a configurable persona. The bot speaks via channel webhooks so messages appear to come from a regular user (with your chosen display name and avatar) rather than a bot. Generations are powered by Google Gemini; persistent state lives in a single JSON file (optionally backed by Google Cloud Storage); deployment targets Google Cloud Run.

Fork it, fill in two configuration files, run two scripts, and you have your own persona bot in a Discord server.

## Features

- AI-generated posts via `/bot newpost <prompt>` — the bot writes a message in your persona's voice, then posts via webhook
- `@mention` interaction replies with per-user rate limiting
- Full admin control panel (web UI, password-protected)
- Role-based permissions, exclusion lists, slang dictionary, channel permissions
- Moderation commands (`/mod kick`, `/mod timeout`, `/mod ban`, `/mod purge`, welcome messages)
- Giveaways (`/giveaway start`, auto-end, reroll, winner selection)
- Forms (user-submitted modals, configurable via web UI)
- Kill switch (`/bot disable`) to immediately stop all public responses
- Structured logs on Cloud Run, plain-text logs locally

## Prerequisites

- A Google Cloud project with billing linked (or active free credits)
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- A Discord application + bot token ([Developer Portal](https://discord.com/developers/applications))
- A Google AI Studio API key ([aistudio.google.com/apikey](https://aistudio.google.com/apikey))
- Python 3.12+ for local development and the bundled setup scripts
- Bash (macOS/Linux, or Git Bash/WSL on Windows) for running `scripts/*.sh`

## Quick start

```bash
# 1. Clone
git clone <your-fork-url> creatorbot
cd creatorbot

# 2. Configure (edit both files with your values)
cp config.example.py config.py
cp .env.example .env

# 3. Install Python dependencies (for running scripts and local dev)
pip install -r requirements.txt

# 4. One-time GCP bootstrap (enables APIs, creates service account + IAM)
scripts/setup.sh

# 5. First deploy
scripts/deploy.sh
```

Future redeploys after code changes:

```bash
scripts/update.sh
```

## Configuration

All per-deployment customization lives in exactly two files:

- **`config.py`** — non-secret values (GCP project, Cloud Run service name, Discord admin IDs, bot display name, persona file path, etc.). Gitignored; you edit this once.
- **`.env`** — secrets only (Discord bot token, Gemini API key, web panel password). Gitignored; never commit.

Both ship with `*.example` templates you copy and fill in. See comments in `config.example.py` for every field.

### Discord bot invite

Generate an invite URL from the Developer Portal → OAuth2 → URL Generator. Required scopes and permissions:

| Scopes | Permissions |
|---|---|
| `bot` | Send Messages, Manage Webhooks, Read Message History, Embed Links, Attach Files, Manage Messages, Mention Everyone, Add Reactions |
| `applications.commands` | (for slash command registration) |

Also enable **Message Content Intent** and **Server Members Intent** under Bot → Privileged Gateway Intents.

## Configuring your persona

Edit `data/persona.json` to define who the bot is — their bio, writing style, vocabulary, known facts, and example messages. The AI uses this to stay in character.

```json
{
  "name": "YourBot",
  "bio": "Short description of who this persona is",
  "writing_style": "How they write — formality, punctuation, length",
  "vocabulary": ["word1", "word2"],
  "facts": ["Known fact 1", "Known fact 2"],
  "example_messages": ["example message 1", "example message 2"]
}
```

For personal/private persona content you don't want committed, save it as `data/persona.local.json` — the loader prefers that file over `persona.json` if it exists (gitignored by default).

## Running locally

```bash
pip install -r requirements.txt
python -m bot.main
```

The bot connects to Discord and also starts a local web server (default port 8080) with a health-check endpoint and the admin control panel at `/admin`.

## Slash commands

The root group name is configurable via `COMMAND_GROUP_NAME` in `config.py` (default: `bot`). Examples below assume the default.

| Command | Description |
|---|---|
| `/bot newpost <prompt> [#channel]` | Generate an AI message and post it as the persona |
| `/bot preview_post <prompt>` | Preview a generated response (ephemeral) without posting |
| `/bot say_raw <channel> <message>` | Post a raw message as the persona (no AI) |
| `/bot disable` | Kill switch — stop all public responses |
| `/bot enable` | Re-enable responses |
| `/mod kick <user> [reason]` | Kick a member |
| `/mod ban <user> [reason] [delete_days]` | Ban a member |
| `/mod timeout <user> <duration> [reason]` | Timeout a member (e.g. `10m`, `2h`, `1d`) |
| `/mod untimeout <user>` | Remove a timeout |
| `/mod unban <user_id>` | Unban by ID |
| `/mod purge <amount> [#channel]` | Bulk-delete recent messages |
| `/giveaway start <duration> <winners> <prize> [channel]` | Start a giveaway |
| `/giveaway end <message_id>` | End early and pick winners |
| `/giveaway reroll <message_id>` | Pick new winners |
| `/giveaway list` | List active giveaways |
| `/form list` | Show forms the user can fill out |
| `/form submit <name>` | Open the form modal |

## Optional: GCS-backed persistent state

By default, the bot's runtime state (active channels, admins, giveaways, etc.) persists to `data/config.json` on local disk. This works fine for single-instance Cloud Run, but if you want state to survive container recreation cleanly — or run multiple replicas — point it at Google Cloud Storage:

1. Set `CONFIG_BUCKET = "your-bucket-name"` in `config.py`
2. Re-run `scripts/setup.sh` — it will create the bucket and grant the service account access

The bot auto-falls back to local disk if GCS is unreachable at startup.

## Architecture

```
creatorbot/
├── config.py              # Your deployment config (gitignored)
├── config.example.py      # Template
├── .env                   # Your secrets (gitignored)
├── .env.example           # Template
├── ai/                    # Persona + prompt builder + Gemini client
├── bot/                   # Discord bot + web admin panel + cogs
├── data/
│   ├── persona.json       # Committed persona template
│   ├── persona.local.json # Gitignored — your real persona (overrides above)
│   └── config.json        # Runtime state (gitignored, auto-created)
├── scripts/
│   ├── setup.sh           # One-time GCP bootstrap
│   ├── deploy.sh          # Initial deploy (image + service)
│   ├── update.sh          # Redeploy (image only)
│   └── _load_config.py    # Helper: exports config.py values to shell
├── tests/                 # pytest suite — runs fully offline
├── Dockerfile
└── requirements.txt
```

## Testing

```bash
pytest tests/ -v
```

Tests run fully offline — no Discord connection, no Gemini calls, no GCS bucket needed. See `tests/README.md` for details.

## Monitoring

```bash
# Live logs
gcloud run services logs read $CLOUD_RUN_SERVICE --region $GCP_REGION --follow

# Service status
gcloud run services describe $CLOUD_RUN_SERVICE --region $GCP_REGION
```

Substitute `$CLOUD_RUN_SERVICE` and `$GCP_REGION` with your values from `config.py`, or `eval "$(python3 scripts/_load_config.py)"` first.

## License

[Apache License 2.0](LICENSE).
