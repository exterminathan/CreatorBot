"""Giveaway slash commands and button handler for CyBot."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.giveaway_manager import (
    GiveawayManager,
    ENTRIES_BUTTON_ID,
    GIVEAWAY_EMOJI,
    parse_duration,
    _fmt_duration,
    _build_embed,
)

if TYPE_CHECKING:
    from bot.main import CyBot

log = logging.getLogger(__name__)


class GiveawayCog(discord.ext.commands.Cog):
    """Slash-command group ``/giveaway`` for running giveaways."""

    giveaway = app_commands.Group(
        name="giveaway",
        description="Manage server giveaways",
    )

    def __init__(self, bot: CyBot):
        self.bot = bot
        self.manager = GiveawayManager(bot, bot.cfg)

    # ── permission guards ────────────────────────────────────────────────────

    def _can_manage(self, interaction: discord.Interaction) -> bool:
        """Return True if the user can manage giveaways."""
        if interaction.user.id in self.bot.cfg.admin_user_ids:
            return True
        if isinstance(interaction.user, discord.Member):
            # Check for giveaway manager role
            manager_role_ids = self.bot.cfg.giveaway_settings.get("manager_role_ids", [])
            if manager_role_ids:
                user_role_ids = [r.id for r in interaction.user.roles]
                if any(rid in user_role_ids for rid in manager_role_ids):
                    return True
        return False

    async def _deny(self, interaction: discord.Interaction) -> bool:
        if not self._can_manage(interaction):
            await interaction.response.send_message(
                "You don't have permission to manage giveaways.",
                ephemeral=True,
            )
            return True
        return False

    # ── /giveaway start ──────────────────────────────────────────────────────

    @giveaway.command(name="start", description="Start a new giveaway")
    @app_commands.describe(
        duration="Duration e.g. 30s, 5m, 2h, 1d",
        winners="Number of winners",
        prize="What is being given away",
        channel="Channel to post in (defaults to giveaway default or current)",
        message="Optional announcement message sent before the embed (supports @mentions, @here, @everyone)",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        duration: str,
        winners: app_commands.Range[int, 1, 20],
        prize: str,
        channel: discord.TextChannel | None = None,
        message: str | None = None,
    ):
        if await self._deny(interaction):
            return

        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message(
                "Invalid duration. Use formats like `30s`, `5m`, `2h`, `1d` (max 30 days).",
                ephemeral=True,
            )
            return

        # Resolve channel
        if channel is None:
            default_cid = self.bot.cfg.giveaway_settings.get("default_channel_id")
            if default_cid:
                channel = self.bot.get_channel(int(default_cid))
            if channel is None and isinstance(interaction.channel, discord.TextChannel):
                channel = interaction.channel

        if channel is None:
            await interaction.response.send_message(
                "Could not determine a channel. Specify one or set a default in the Web UI.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        giveaway = await self.manager.start(
            channel=channel,
            prize=prize,
            winner_count=winners,
            duration_seconds=seconds,
            host=interaction.user,
            announcement_message=message,
        )

        msg_url = (
            f"https://discord.com/channels/{giveaway['guild_id']}"
            f"/{giveaway['channel_id']}/{giveaway['message_id']}"
        )
        duration_str = _fmt_duration(seconds)
        await interaction.followup.send(
            f"{GIVEAWAY_EMOJI} Giveaway started in {channel.mention} for **{prize}**! "
            f"Duration: {duration_str}, Winners: {winners}\n{msg_url}",
            ephemeral=True,
        )

    # ── /giveaway end ────────────────────────────────────────────────────────

    @giveaway.command(name="end", description="End a giveaway early and pick winners")
    @app_commands.describe(message_id="The message ID of the giveaway to end")
    async def end(self, interaction: discord.Interaction, message_id: str):
        if await self._deny(interaction):
            return

        giveaway = self.bot.cfg.get_giveaway(message_id.strip())
        if giveaway is None:
            await interaction.response.send_message(
                "Giveaway not found. Check the message ID.", ephemeral=True
            )
            return
        if giveaway.get("ended"):
            await interaction.response.send_message(
                "That giveaway has already ended.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.manager.end(message_id.strip())
        if result is None:
            await interaction.followup.send("Failed to end giveaway.", ephemeral=True)
            return
        winners = result.get("winners", [])
        if winners:
            winner_list = ", ".join(f"<@{w}>" for w in winners)
            await interaction.followup.send(
                f"Giveaway for **{result['prize']}** ended! Winner(s): {winner_list}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Giveaway for **{result['prize']}** ended with no entries.",
                ephemeral=True,
            )

    # ── /giveaway reroll ─────────────────────────────────────────────────────

    @giveaway.command(name="reroll", description="Pick a new winner from an ended giveaway")
    @app_commands.describe(message_id="The message ID of the ended giveaway to reroll")
    async def reroll(self, interaction: discord.Interaction, message_id: str):
        if await self._deny(interaction):
            return

        giveaway = self.bot.cfg.get_giveaway(message_id.strip())
        if giveaway is None:
            await interaction.response.send_message(
                "Giveaway not found. Check the message ID.", ephemeral=True
            )
            return
        if not giveaway.get("ended"):
            await interaction.response.send_message(
                "That giveaway is still active. Use `/giveaway end` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.manager.end(message_id.strip(), reroll=True)
        if result is None:
            await interaction.followup.send("Failed to reroll giveaway.", ephemeral=True)
            return
        winners = result.get("winners", [])
        if winners:
            winner_list = ", ".join(f"<@{w}>" for w in winners)
            await interaction.followup.send(
                f"Rerolled! New winner(s) for **{result['prize']}**: {winner_list}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "No valid entries to reroll from.", ephemeral=True
            )

    # ── /giveaway list ───────────────────────────────────────────────────────

    @giveaway.command(name="list", description="List all active giveaways on this server")
    async def list_giveaways(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return

        import time
        now = time.time()
        active = [
            g for g in self.manager.get_active()
            if g.get("guild_id") == (interaction.guild_id or 0)
        ]

        if not active:
            await interaction.response.send_message(
                "No active giveaways right now.", ephemeral=True
            )
            return

        lines = []
        for g in active:
            remaining = max(0, g["end_time"] - now)
            msg_url = (
                f"https://discord.com/channels/{g['guild_id']}"
                f"/{g['channel_id']}/{g['message_id']}"
            )
            entries = len(g.get("entries", []))
            lines.append(
                f"• **{g['prize']}** — {_fmt_duration(remaining)} left, "
                f"{entries} {"entry" if entries == 1 else "entries"}, "
                f"{g['winner_count']} winner(s) — [Jump]({msg_url})"
            )

        embed = discord.Embed(
            title=f"{GIVEAWAY_EMOJI} Active Giveaways",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Button interaction handler ───────────────────────────────────────────

    @discord.ext.commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle giveaway entry button presses."""
        if interaction.type != discord.InteractionType.component:
            return
        custom_id: str = interaction.data.get("custom_id", "")
        if not custom_id.startswith(f"{ENTRIES_BUTTON_ID}:"):
            return

        message_id = custom_id.split(":", 1)[1]
        user_id = interaction.user.id

        # Bot can't enter its own giveaway
        if self.bot.user and user_id == self.bot.user.id:
            await interaction.response.send_message(
                "Bots can't enter giveaways.", ephemeral=True
            )
            return

        result = await self.manager.add_entry(message_id, user_id)
        if result == "ended":
            await interaction.response.send_message(
                "This giveaway has already ended!", ephemeral=True
            )
        elif result == "added":
            await interaction.response.send_message(
                f"{GIVEAWAY_EMOJI} You've entered the giveaway! Good luck!",
                ephemeral=True,
            )
        else:  # removed
            await interaction.response.send_message(
                "You've withdrawn your entry from the giveaway.",
                ephemeral=True,
            )


async def setup(bot: CyBot):
    await bot.add_cog(GiveawayCog(bot))
