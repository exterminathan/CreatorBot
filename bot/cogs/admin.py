from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot.main import CyBot

log = logging.getLogger(__name__)
GENERATION_TIMEOUT_SECONDS = 75


class AdminCog(discord.ext.commands.Cog):
    """Slash-command group ``/cy`` for admin control of the bot."""

    cy = app_commands.Group(name="cy", description="Control the Cy persona bot")

    def __init__(self, bot: CyBot):
        self.bot = bot

    # -- guards --------------------------------------------------------------

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id != self.bot.cfg.admin_channel_id:
            return False
        if interaction.user.id in self.bot.cfg.admin_user_ids:
            return True
        # Check role-based permission
        if isinstance(interaction.user, discord.Member):
            return self.bot._get_user_permission(interaction.user, "can_use_commands")
        return False

    async def _deny(self, interaction: discord.Interaction) -> bool:
        """Send an ephemeral denial if not admin. Returns True if denied."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True
            )
            return True
        return False

    # -- /cy newpost ---------------------------------------------------------

    @cy.command(name="newpost", description="Generate a message and post it as Cy")
    @app_commands.describe(
        prompt="Instruction / topic for the generated message",
        channel="Target channel (uses default if not specified)",
    )
    async def newpost(
        self,
        interaction: discord.Interaction,
        prompt: str,
        channel: discord.TextChannel | None = None,
    ):
        if await self._deny(interaction):
            return

        if channel is None:
            if self.bot.cfg.default_channel_id:
                channel = self.bot.get_channel(self.bot.cfg.default_channel_id)
                if channel is None:
                    await interaction.response.send_message(
                        "Default channel not found. Specify a channel or update the default.",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.response.send_message(
                    "No channel specified and no default channel set.",
                    ephemeral=True,
                )
                return

        if channel.id not in self.bot.cfg.active_channels:
            await interaction.response.send_message(
                f"{channel.mention} is not an active channel. Add it first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        log.info(
            "generate/newpost start: channel_id=%s prompt=%r",
            channel.id, prompt[:120],
        )
        try:
            text = await asyncio.wait_for(
                self.bot.generate(prompt), timeout=GENERATION_TIMEOUT_SECONDS
            )
        except TimeoutError:
            log.warning("generate/newpost timeout: channel_id=%s", channel.id)
            await interaction.followup.send(
                "Generation timed out. The model may be overloaded; try a shorter prompt or retry in a moment.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            log.exception("generate/newpost error: channel_id=%s", channel.id)
            await interaction.followup.send(f"Generation failed: {exc}", ephemeral=True)
            return

        msg = await self.bot.webhooks.send_as_cy(channel, text)
        log.info(
            "generate/newpost done: channel_id=%s message_id=%s chars=%d",
            channel.id, msg.id, len(text),
        )
        await interaction.followup.send(
            f"Posted in {channel.mention}: {msg.jump_url}", ephemeral=True
        )

    # -- /cy preview_post ----------------------------------------------------

    @cy.command(name="preview_post", description="Preview a generated response without posting")
    @app_commands.describe(prompt="Instruction / topic for the generated message")
    async def preview_post(self, interaction: discord.Interaction, prompt: str):
        if await self._deny(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        log.info("generate/preview_post start: prompt=%r", prompt[:120])
        try:
            text = await asyncio.wait_for(
                self.bot.generate(prompt), timeout=GENERATION_TIMEOUT_SECONDS
            )
        except TimeoutError:
            log.warning("generate/preview_post timeout")
            await interaction.followup.send(
                "Generation timed out. The model may be overloaded; try a shorter prompt or retry in a moment.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            log.exception("generate/preview_post error")
            await interaction.followup.send(f"Generation failed: {exc}", ephemeral=True)
            return

        if len(text) > 1990:
            text = text[:1990] + "…"
        log.info("generate/preview_post done: chars=%d", len(text))
        await interaction.followup.send(f"**Preview:**\n{text}", ephemeral=True)

    # -- /cy say_raw ---------------------------------------------------------

    @cy.command(name="say_raw", description="Post a raw message as Cy (no AI)")
    @app_commands.describe(channel="Target channel", message="Message to send as Cy")
    async def say_raw(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        if await self._deny(interaction):
            return
        if channel.id not in self.bot.cfg.active_channels:
            await interaction.response.send_message(
                f"{channel.mention} is not an active channel. Add it first.",
                ephemeral=True,
            )
            return
        msg = await self.bot.webhooks.send_as_cy(channel, message)
        log.info("say_raw: channel_id=%s message_id=%s", channel.id, msg.id)
        await interaction.response.send_message(
            f"Sent to {channel.mention}: {msg.jump_url}", ephemeral=True
        )


async def setup(bot: CyBot):
    await bot.add_cog(AdminCog(bot))
