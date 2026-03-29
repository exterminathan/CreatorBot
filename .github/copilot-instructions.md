# CyBot — Workspace Instructions

## Architecture

Discord bot that posts AI-generated messages as **Cy** (display name: Loler01) using webhooks, making responses appear as a real user in chat. Deployed to **Google Cloud Run**.

**Message flow:**

1. Admin issues `/cy send #channel <prompt>` (only works in admin channel, admin user only)
2. `CyBot.generate()` → `prompt_builder.py` builds system+user message list using persona + transcripts
3. `ai/client.py` calls **Google AI Gemini 2.5 Flash Lite (google-genai SDK)** via `client.aio.models.generate_content()`
4. Response posted to channel via Discord webhook (`webhook_manager.py`) — appears as Cy

**Key components:**

- `bot/main.py` — Entrypoint; starts health-check HTTP server on port 8080 (Cloud Run probe) + Discord bot
- `bot/config.py` — Loads `.env`; manages persistent channel state in `data/config.json`
- `bot/cogs/admin.py` — All `/cy` slash commands (7 commands); all restricted to `ADMIN_USER_ID` in `ADMIN_CHANNEL_ID`
- `ai/client.py` — Wraps google-genai SDK; converts OpenAI-style messages to Gemini format. Raises `GeminiGenerationError` on API failures (sanitised, never exposes raw API details).
- `ai/persona.py` — Loads `data/cy_persona.json` + all `.txt` files in `data/transcripts/`
- `bot/webhook_manager.py` — Creates/caches webhooks per channel in memory; handles race conditions on concurrent creation.

## Build and Run

### Local Development

```bash
pip install -r requirements.txt
python -m bot.main
pytest tests/ -v
```

Requires `.env` with `DISCORD_BOT_TOKEN`, `ADMIN_CHANNEL_ID`, `ADMIN_USER_ID`.

**Local GCP auth required:** Run `gcloud auth application-default login` before running locally. Cloud Run uses its service identity automatically.

### Deploy to Cloud Run

**When user requests to build and deploy**, follow these steps **exactly in order**:

1. **Build container image:**

   ```bash
   gcloud builds submit --tag gcr.io/project-a89ff80d-7ecd-456f-aee/cybot --project project-a89ff80d-7ecd-456f-aee .
   ```

   Wait for build to complete. Output will show "SUCCESS" when done.

2. **Deploy to Cloud Run:**

   ```bash
   gcloud run deploy cybot --image gcr.io/project-a89ff80d-7ecd-456f-aee/cybot --region us-central1 --platform managed --allow-unauthenticated --timeout 3600 --min-instances 1 --max-instances 1 --memory 512Mi --cpu 1
   ```

   Wait for deployment to complete.

3. **Verify deployment:**
   ```bash
   gcloud run services describe cybot --region us-central1
   ```
   Check that the service is running and the URL is accessible.

**Do NOT use a deploy script.** Always run these commands directly via terminal.

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
- **Gemini model name**: Defaults to `gemini-2.5-flash-lite`. Override via `GEMINI_MODEL` env var if the model is renamed.
- **Webhook permissions**: Bot needs `Manage Webhooks` in a channel to post as Cy. `/cy channel_add` rolls back if this fails.
- **`message_content` is a privileged intent** — must be enabled in the Discord Developer Portal for the bot application.
- **Port 8080** must be free locally; Cloud Run health probes hit `GET /` on this port.
- **`GEMINI_API_KEY` is required** — if missing, the bot raises a clear `RuntimeError` on startup rather than a cryptic `KeyError`.
