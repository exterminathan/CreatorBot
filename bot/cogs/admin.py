from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.main import _surface_links

# Load the configurable slash-command group name from the top-level config.
# Falls back to config.example for fresh clones without a local config.py.
try:
    from config import COMMAND_GROUP_NAME as _COMMAND_GROUP_NAME
except ImportError:
    import importlib.util
    from pathlib import Path
    _p = Path(__file__).resolve().parent.parent.parent / "config.example.py"
    _spec = importlib.util.spec_from_file_location("_creatorbot_config_example", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    _COMMAND_GROUP_NAME = _mod.COMMAND_GROUP_NAME

if TYPE_CHECKING:
    from bot.main import CreatorBot

log = logging.getLogger(__name__)
GENERATION_TIMEOUT_SECONDS = 75


class AdminCog(discord.ext.commands.Cog):
    """Slash-command group for admin control of the bot."""

    root = app_commands.Group(
        name=_COMMAND_GROUP_NAME,
        description="Control the persona bot",
    )

    def __init__(self, bot: CreatorBot):
        self.bot = bot

    # -- guards --------------------------------------------------------------

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        # Any admin user can use commands from any channel in any server
        if interaction.user.id in self.bot.cfg.admin_user_ids:
            return True
        # Role-based: can_use_commands allows commands from any channel
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

    # -- newpost ------------------------------------------------------------

    @root.command(name="newpost", description="Generate a message and post it as the persona")
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

        if not self.bot.cfg.channel_permissions.get(str(channel.id), {}).get("can_post", True):
            await interaction.response.send_message(
                f"Posting is disabled for {channel.mention}. Enable it in the Channels panel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not self.bot.cfg.bot_enabled:
            await interaction.followup.send(
                f"Bot is currently disabled. Use `/{_COMMAND_GROUP_NAME} enable` to re-enable.",
                ephemeral=True,
            )
            return

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
            em = discord.Embed(title="⏱️ Post Timed Out", color=discord.Color.orange())
            em.timestamp = discord.utils.utcnow()
            em.add_field(name="Channel", value=channel.mention, inline=True)
            em.add_field(name="By", value=str(interaction.user), inline=True)
            em.add_field(name="Prompt", value=prompt[:300], inline=False)
            await self.bot.log_to_channel(em)
            await interaction.followup.send(
                "Generation timed out. The model may be overloaded; try a shorter prompt or retry in a moment.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            log.exception("generate/newpost error: channel_id=%s", channel.id)
            em = discord.Embed(title="❌ Post Failed", color=discord.Color.red())
            em.timestamp = discord.utils.utcnow()
            em.add_field(name="Channel", value=channel.mention, inline=True)
            em.add_field(name="By", value=str(interaction.user), inline=True)
            em.add_field(name="Error", value=str(exc)[:300], inline=False)
            await self.bot.log_to_channel(em)
            await interaction.followup.send(f"Generation failed: {exc}", ephemeral=True)
            return

        msg = await self.bot.webhooks.send_as_persona(channel, text)
        log.info(
            "generate/newpost done: channel_id=%s message_id=%s chars=%d",
            channel.id, msg.id, len(text),
        )
        em = discord.Embed(title="\U0001f4ee Post Created", color=discord.Color.green())
        em.timestamp = discord.utils.utcnow()
        em.add_field(name="Channel", value=channel.mention, inline=True)
        em.add_field(name="By", value=str(interaction.user), inline=True)
        em.add_field(name="Prompt", value=prompt[:300], inline=False)
        em.add_field(name="Message", value=msg.jump_url, inline=False)
        await self.bot.log_to_channel(em)
        await interaction.followup.send(
            f"Posted in {channel.mention}: {msg.jump_url}", ephemeral=True
        )

    # -- preview_post -------------------------------------------------------

    @root.command(name="preview_post", description="Preview a generated response without posting")
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

    # -- say_raw ------------------------------------------------------------

    @root.command(name="say_raw", description="Post a raw message as the persona (no AI)")
    @app_commands.describe(channel="Target channel", message="Message to send")
    async def say_raw(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        if await self._deny(interaction):
            return
        if not self.bot.cfg.bot_enabled:
            await interaction.response.send_message(
                f"Bot is currently disabled. Use `/{_COMMAND_GROUP_NAME} enable` to re-enable.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        # Surface links at the end so Discord embeds them cleanly without duplication
        formatted_message = _surface_links(message)
        msg = await self.bot.webhooks.send_as_persona(channel, formatted_message)
        log.info("say_raw: channel_id=%s message_id=%s", channel.id, msg.id)
        em = discord.Embed(title="\U0001f4ac Raw Message Sent", color=discord.Color.blue())
        em.timestamp = discord.utils.utcnow()
        em.add_field(name="Channel", value=channel.mention, inline=True)
        em.add_field(name="By", value=str(interaction.user), inline=True)
        em.add_field(name="Message", value=message[:500], inline=False)
        em.add_field(name="Jump", value=msg.jump_url, inline=False)
        await self.bot.log_to_channel(em)
        await interaction.followup.send(
            f"Sent to {channel.mention}: {msg.jump_url}", ephemeral=True
        )


    # -- disable ------------------------------------------------------------

    @root.command(name="disable", description="Kill switch: immediately stop all bot responses")
    async def disable(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return
        self.bot.cfg.set_bot_enabled(False)
        log.warning("Kill switch activated by %s", interaction.user)
        em = discord.Embed(title="\U0001f6d1 Bot Disabled", color=discord.Color.red())
        em.timestamp = discord.utils.utcnow()
        em.add_field(name="By", value=str(interaction.user), inline=False)
        await self.bot.log_to_channel(em)
        await interaction.response.send_message(
            f"\U0001f6d1 **Bot disabled.** All public responses are now blocked. Use `/{_COMMAND_GROUP_NAME} enable` to resume.",
            ephemeral=True,
        )

    # -- enable -------------------------------------------------------------

    @root.command(name="enable", description="Re-enable bot responses after a kill switch")
    async def enable(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return
        self.bot.cfg.set_bot_enabled(True)
        log.info("Bot re-enabled by %s", interaction.user)
        em = discord.Embed(title="✅ Bot Enabled", color=discord.Color.green())
        em.timestamp = discord.utils.utcnow()
        em.add_field(name="By", value=str(interaction.user), inline=False)
        await self.bot.log_to_channel(em)
        await interaction.response.send_message(
            "✅ **Bot enabled.** Responses are live again.",
            ephemeral=True,
        )


async def setup(bot: CreatorBot):
    await bot.add_cog(AdminCog(bot))
