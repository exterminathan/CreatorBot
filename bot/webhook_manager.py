from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.config import Config

log = logging.getLogger(__name__)

WEBHOOK_NAME = "CyBot-Hook"


class WebhookManager:
    """Creates, caches, and posts messages via channel webhooks so they appear
    as a regular user (Cy's name + avatar)."""

    def __init__(self, config: Config):
        self.config = config
        # channel_id -> Webhook object
        self._cache: dict[int, discord.Webhook] = {}

    async def get_or_create(self, channel: discord.TextChannel) -> discord.Webhook:
        """Return an existing CyBot webhook for the channel, or create one.

        Handles the race condition where a webhook may be deleted between the
        cache check and first use, or where two concurrent callers would both
        try to create a new webhook at the same time.
        """
        if channel.id in self._cache:
            return self._cache[channel.id]

        # Check existing webhooks on Discord before creating a new one
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.name == WEBHOOK_NAME:
                self._cache[channel.id] = wh
                return wh

        # Create a new webhook; if another process beat us to it, fall back
        # to re-fetching rather than propagating a duplicate-creation error.
        try:
            wh = await channel.create_webhook(name=WEBHOOK_NAME)
        except discord.HTTPException:
            # Re-fetch in case a concurrent call already created it
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == WEBHOOK_NAME:
                    self._cache[channel.id] = wh
                    log.info("Webhook already existed in #%s (%s), using it", channel.name, channel.id)
                    return wh
            raise  # Re-raise if still not found (genuine permission error etc.)
        self._cache[channel.id] = wh
        log.info("Created webhook in #%s (%s)", channel.name, channel.id)
        return wh

    async def send_as_cy(self, channel: discord.TextChannel, content: str) -> discord.WebhookMessage:
        """Post a message in *channel* that appears to come from Cy."""
        wh = await self.get_or_create(channel)
        return await wh.send(
            content=content,
            username=self.config.cy_display_name,
            avatar_url=self.config.cy_avatar_url,
            wait=True,
        )

    async def cleanup(self, channel: discord.TextChannel):
        """Delete the CyBot webhook from a channel."""
        if channel.id in self._cache:
            try:
                await self._cache.pop(channel.id).delete()
            except discord.NotFound:
                pass
        else:
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == WEBHOOK_NAME:
                    await wh.delete()
