from __future__ import annotations

import asyncio
import logging
import os
import threading

from aiohttp import web
import discord
from discord.ext import commands

from ai.client import GeminiClient
from ai.persona import Persona
from ai.prompt_builder import build_messages
from bot.config import Config
from bot.webhook_manager import WebhookManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("cybot")


# ── Cloud Run health-check server ───────────────────────────────────────────
def _start_health_server():
    """Run a tiny HTTP server on $PORT so Cloud Run knows the container is alive."""
    port = int(os.environ.get("PORT", 8080))

    async def _health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", _health)

    runner = web.AppRunner(app)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", port)
    loop.run_until_complete(site.start())
    log.info("Health-check server listening on port %d", port)
    loop.run_forever()


class CyBot(commands.Bot):
    """The CyBot Discord bot."""

    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.cfg = cfg
        self.webhooks = WebhookManager(cfg)
        self.persona = Persona()
        self.gemini = GeminiClient(
            project_id=cfg.gcp_project_id,
            location=cfg.gcp_location,
            model_name=cfg.gemini_model,
        )

    async def setup_hook(self):
        await self.load_extension("bot.cogs.admin")

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("Active channels: %s", self.cfg.active_channels)
        # Sync slash commands to every guild for instant registration
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), guild.id)

    async def generate(self, prompt: str) -> str:
        """Generate a message as Cy from a prompt string."""
        messages = build_messages(self.persona, prompt)
        return await self.gemini.generate(messages)

    async def close(self):
        await self.gemini.close()
        await super().close()


def main():
    # Start health-check server in a daemon thread for Cloud Run
    health_thread = threading.Thread(target=_start_health_server, daemon=True)
    health_thread.start()

    cfg = Config()
    bot = CyBot(cfg)
    bot.run(cfg.bot_token, log_handler=None)


if __name__ == "__main__":
    main()
