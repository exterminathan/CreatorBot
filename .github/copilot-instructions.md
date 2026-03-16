# CyBot — Workspace Instructions

## Architecture

Discord bot that posts AI-generated messages as **Cy** (display name: Loler01) using webhooks, making responses appear as a real user in chat. Deployed to **Google Cloud Run**.

**Message flow:**

1. Admin issues `/cy send #channel <prompt>` (only works in admin channel, admin user only)
2. `CyBot.generate()` → `prompt_builder.py` builds system+user message list using persona + transcripts
3. `ai/client.py` calls **Vertex AI Gemini 2.0 Flash Lite** via `generate_content_async()`
4. Response posted to channel via Discord webhook (`webhook_manager.py`) — appears as Cy

**Key components:**

- `bot/main.py` — Entrypoint; starts health-check HTTP server on port 8080 (Cloud Run probe) + Discord bot
- `bot/config.py` — Loads `.env`; manages persistent channel state in `data/config.json`
- `bot/cogs/admin.py` — All `/cy` slash commands (7 commands); all restricted to `ADMIN_USER_ID` in `ADMIN_CHANNEL_ID`
- `ai/client.py` — Wraps Vertex AI SDK; converts OpenAI-style messages to Gemini format
- `ai/persona.py` — Loads `data/cy_persona.json` + all `.txt` files in `data/transcripts/`
- `bot/webhook_manager.py` — Creates/caches webhooks per channel in memory

## Build and Run

### Local Development

```bash
pip install -r requirements.txt
python -m bot.main
```

Requires `.env` with `DISCORD_BOT_TOKEN`, `ADMIN_CHANNEL_ID`, `ADMIN_USER_ID`.

**Local GCP auth required:** Run `gcloud auth application-default login` before running locally. Cloud Run uses its service identity automatically.

### Deploy to Cloud Run

**After any code change requiring a new build**, you will automatically run these commands **in order**:

1. **Build:** `gcloud builds submit --tag gcr.io/project-a89ff80d-7ecd-456f-aee/cybot --project project-a89ff80d-7ecd-456f-aee .`
2. **Deploy:** `gcloud run deploy cybot --image gcr.io/project-a89ff80d-7ecd-456f-aee/cybot --region us-central1 --platform managed`
3. **Check logs:** `gcloud run services logs read cybot --region us-central1 --limit 10`

### Update Environment Variables

To update Cloud Run environment variables, request: "Update CyBot environment variables: KEY1=value1 KEY2=value2"

I will run: `gcloud run services update cybot --region us-central1 --update-env-vars KEY1=value1,KEY2=value2 --allow-unauthenticated`


## Conventions

- **Async everywhere**: All I/O is `async`/`await`. Use `asyncio.wait_for()` for timeouts on external calls.
- **Logging**: `log = logging.getLogger(__name__)` in every module. Use `INFO` level for normal operations.
- **Admin command pattern**: Defer with `await interaction.response.defer(ephemeral=True, thinking=True)`, then `followup.send()` on completion or error.
- **Config persistence**: Add/remove channels via `config.add_channel()` / `config.remove_channel()` — these auto-save to `data/config.json`.
- **Webhook cache**: `WebhookManager._cache` is in-memory only; repopulated automatically on next use if missing.
- **Python 3.12+**: Use type hints. The codebase uses modern Python.
- **Gemini message format**: `system` role → `system_instruction`; `assistant` role → `model`; keep this mapping in `ai/client.py`.

## Pitfalls

- **Never commit `.env`** — it contains `DISCORD_BOT_TOKEN`. It's gitignored.
- **After code changes, run deploy commands** — Build with `gcloud builds submit`, deploy with `gcloud run deploy`, then check logs with `gcloud run services logs read`. See "Deploy to Cloud Run" section for commands.
- **Transcript files**: Drop `.txt` files in `data/transcripts/` to feed more examples to the persona. Only 10 are loaded (capped), truncated at 500 chars each.
- **Generation timeout is 75 seconds** (hardcoded in `admin.py`). Vertex AI can be slow under load.
- **Gemini model name**: Defaults to `gemini-2.0-flash-lite`. Override via `GEMINI_MODEL` env var if the model is renamed.
- **Webhook permissions**: Bot needs `Manage Webhooks` in a channel to post as Cy. `/cy channel_add` rolls back if this fails.
- **`message_content` is a privileged intent** — must be enabled in the Discord Developer Portal for the bot application.
- **Port 8080** must be free locally; Cloud Run health probes hit `GET /` on this port.
