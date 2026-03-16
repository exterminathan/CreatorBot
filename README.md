# CyBot

Discord bot that posts messages as **Cy** using webhooks (appears as a regular user in chat) with AI-generated text from a RunPod Serverless LLM endpoint.

## Setup

### 1. Discord Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Go to **Bot** → copy the token.
3. Enable **Message Content Intent** under Privileged Gateway Intents.
4. Go to **OAuth2 → URL Generator** → select scopes: `bot`, `applications.commands`.
5. Select bot permissions: `Send Messages`, `Manage Webhooks`, `Read Message History`.
6. Copy the generated URL and invite the bot to your server.

### 2. RunPod Serverless Endpoint

1. Create a Serverless endpoint on [runpod.io](https://runpod.io) using the **vLLM** worker template.
2. Set the model to `mistralai/Mistral-7B-Instruct-v0.3` (or any compatible model).
3. Set `Min Workers: 0`, `Max Workers: 1`.
4. Copy the endpoint ID and your RunPod API key.

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```
cp .env.example .env
```

### 4. Persona Data

Edit `data/cy_persona.json` with Cy's personality details.

Drop transcript `.txt` files into `data/transcripts/` — they'll be loaded into the system prompt automatically.

### 5. Run

```bash
pip install -r requirements.txt
python -m bot.main
```

## Commands

All commands are restricted to the configured admin user in the admin channel.

| Command | Description |
|---|---|
| `/cy channel_add #channel` | Add a channel for Cy to post in |
| `/cy channel_remove #channel` | Remove a channel |
| `/cy channel_list` | List active channels |
| `/cy send #channel <prompt>` | Generate and post a message as Cy |
| `/cy prompt <text>` | Preview a generated response (admin only) |
| `/cy persona_reload` | Reload persona data from disk |

## Deploy to Cloud Run

Make sure your `.env` is filled in, then:

```bash
bash deploy.sh
```

This builds the container with Cloud Build, pushes to GCR, and deploys to Cloud Run with always-on CPU and a single instance.

### Manual commands

```bash
# Check status
gcloud run services describe cybot --region us-central1

# View logs
gcloud run services logs read cybot --region us-central1

# Redeploy after code changes
bash deploy.sh
```
