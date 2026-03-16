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
        return (
            interaction.channel_id == self.bot.cfg.admin_channel_id
            and interaction.user.id == self.bot.cfg.admin_user_id
        )

    async def _deny(self, interaction: discord.Interaction) -> bool:
        """Send an ephemeral denial if not admin. Returns True if denied."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True
            )
            return True
        return False

    # -- /cy channel add -----------------------------------------------------

    @cy.command(name="channel_add", description="Add a channel for Cy to post in")
    @app_commands.describe(channel="The channel to activate")
    async def channel_add(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if await self._deny(interaction):
            return
        if self.bot.cfg.add_channel(channel.id):
            try:
                await self.bot.webhooks.get_or_create(channel)
            except discord.Forbidden:
                # Roll back channel activation if webhook permissions are missing.
                self.bot.cfg.remove_channel(channel.id)
                await interaction.response.send_message(
                    f"I can't manage webhooks in {channel.mention}. "
                    "Grant 'Manage Webhooks' and 'View Channel' to the bot role, then try again.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Added {channel.mention} — webhook ready.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{channel.mention} is already active.", ephemeral=True
            )

    # -- /cy channel remove --------------------------------------------------

    @cy.command(name="channel_remove", description="Remove a channel from Cy's list")
    @app_commands.describe(channel="The channel to deactivate")
    async def channel_remove(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if await self._deny(interaction):
            return
        if self.bot.cfg.remove_channel(channel.id):
            await self.bot.webhooks.cleanup(channel)
            await interaction.response.send_message(
                f"Removed {channel.mention}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{channel.mention} was not active.", ephemeral=True
            )

    # -- /cy channel list ----------------------------------------------------

    @cy.command(name="channel_list", description="List active channels")
    async def channel_list(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return
        if not self.bot.cfg.active_channels:
            await interaction.response.send_message("No active channels.", ephemeral=True)
            return
        lines = [f"<#{cid}>" for cid in self.bot.cfg.active_channels]
        await interaction.response.send_message(
            "**Active channels:**\n" + "\n".join(lines), ephemeral=True
        )

    # -- /cy send ------------------------------------------------------------

    @cy.command(name="send", description="Generate a message and post it as Cy")
    @app_commands.describe(
        channel="Target channel",
        prompt="Instruction / topic for the generated message",
    )
    async def send(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        prompt: str,
    ):
        if await self._deny(interaction):
            return
        if channel.id not in self.bot.cfg.active_channels:
            await interaction.response.send_message(
                f"{channel.mention} is not an active channel. Add it first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            text = await asyncio.wait_for(
                self.bot.generate(prompt), timeout=GENERATION_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await interaction.followup.send(
                "Generation timed out. The model may be overloaded; try a shorter prompt or retry in a moment.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            log.exception("Generation failed")
            await interaction.followup.send(f"Generation failed: {exc}", ephemeral=True)
            return

        msg = await self.bot.webhooks.send_as_cy(channel, text)
        await interaction.followup.send(
            f"Posted in {channel.mention}: {msg.jump_url}", ephemeral=True
        )

    # -- /cy prompt (preview) ------------------------------------------------

    @cy.command(name="prompt", description="Preview a generated response without posting")
    @app_commands.describe(prompt="Instruction / topic for the generated message")
    async def prompt_preview(self, interaction: discord.Interaction, prompt: str):
        if await self._deny(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            text = await asyncio.wait_for(
                self.bot.generate(prompt), timeout=GENERATION_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await interaction.followup.send(
                "Generation timed out. The model may be overloaded; try a shorter prompt or retry in a moment.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            log.exception("Generation failed")
            await interaction.followup.send(f"Generation failed: {exc}", ephemeral=True)
            return

        # Truncate to 2000 chars (Discord limit)
        if len(text) > 1990:
            text = text[:1990] + "…"
        await interaction.followup.send(f"**Preview:**\n{text}", ephemeral=True)

    # -- /cy say -------------------------------------------------------------

    @cy.command(name="say", description="Post a raw message as Cy (no AI, test only)")
    @app_commands.describe(channel="Target channel", message="Message to send as Cy")
    async def say(
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
        await interaction.response.send_message(
            f"Sent to {channel.mention}: {msg.jump_url}", ephemeral=True
        )

    # -- /cy persona reload --------------------------------------------------

    @cy.command(name="persona_reload", description="Reload Cy's persona data from disk")
    async def persona_reload(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return
        self.bot.persona.reload()
        await interaction.response.send_message("Persona reloaded.", ephemeral=True)


async def setup(bot: CyBot):
    await bot.add_cog(AdminCog(bot))
