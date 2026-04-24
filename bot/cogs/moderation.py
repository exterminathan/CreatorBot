from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot.main import CreatorBot

log = logging.getLogger(__name__)

# Regex to parse duration strings like "10m", "2h", "1d", "30s"
_DURATION_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MOD_LOG_MAX = 200


def _parse_duration(raw: str) -> timedelta | None:
    """Parse a duration string (e.g. '10m', '2h', '1d') into a timedelta.

    Returns None if the string is not a valid duration.
    Max Discord timeout is 28 days.
    """
    m = _DURATION_RE.match(raw.strip())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    seconds = value * _DURATION_UNITS[unit]
    td = timedelta(seconds=seconds)
    if td.total_seconds() <= 0 or td > timedelta(days=28):
        return None
    return td


class ModerationCog(discord.ext.commands.Cog):
    """Slash-command group ``/mod`` for server moderation."""

    mod = app_commands.Group(
        name="mod",
        description="Server moderation commands",
    )

    def __init__(self, bot: CreatorBot):
        self.bot = bot

    # ── permission guards ────────────────────────────────────────────────────

    def _is_moderator(self, interaction: discord.Interaction) -> bool:
        """Return True if the user is an admin or has the can_moderate permission."""
        # Admins can always moderate
        if interaction.user.id in self.bot.cfg.admin_user_ids:
            return True
        if isinstance(interaction.user, discord.Member):
            return self.bot._get_user_permission(interaction.user, "can_moderate")
        return False

    async def _deny(self, interaction: discord.Interaction) -> bool:
        """Send an ephemeral denial if not a moderator. Returns True if denied."""
        if not self._is_moderator(interaction):
            await interaction.response.send_message(
                "You don't have permission to use moderation commands.",
                ephemeral=True,
            )
            return True
        return False

    async def _log_action(
        self,
        action: str,
        moderator: discord.User | discord.Member,
        target: str,
        reason: str | None,
        extra: str | None = None,
    ) -> None:
        """Record a mod action to in-memory log and Discord mod-log channel."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "moderator": str(moderator),
            "moderator_id": str(moderator.id),
            "target": target,
            "reason": reason or "",
            "extra": extra or "",
        }
        log_list = self.bot.cfg._mod_action_log
        log_list.append(entry)
        if len(log_list) > _MOD_LOG_MAX:
            del log_list[: len(log_list) - _MOD_LOG_MAX]

        # Post to Discord mod-log channel if configured
        mod_log_id = self.bot.cfg.mod_log_channel_id
        if not mod_log_id:
            return
        channel = self.bot.get_channel(mod_log_id)
        if not isinstance(channel, discord.TextChannel):
            return
        _COLORS = {
            "kick": discord.Color.orange(),
            "ban": discord.Color.red(),
            "unban": discord.Color.green(),
            "timeout": discord.Color.yellow(),
            "untimeout": discord.Color.blurple(),
            "purge": discord.Color.greyple(),
        }
        em = discord.Embed(
            title=f"🛡️ {action.upper()}",
            color=_COLORS.get(action.lower(), discord.Color.blurple()),
            timestamp=datetime.now(timezone.utc),
        )
        em.add_field(name="Target", value=target, inline=True)
        em.add_field(name="Moderator", value=str(moderator), inline=True)
        if reason:
            em.add_field(name="Reason", value=reason, inline=False)
        if extra:
            em.add_field(name="Details", value=extra, inline=False)
        try:
            await channel.send(embed=em)
        except discord.HTTPException:
            log.warning("Failed to send mod log embed to channel %s", mod_log_id)

    # ── welcome event ────────────────────────────────────────────────────────

    @discord.ext.commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        welcome_id = self.bot.cfg.welcome_channel_id
        if not welcome_id:
            return
        channel = self.bot.get_channel(welcome_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Welcome channel %s not found or not a text channel", welcome_id)
            return

        custom_msg = self.bot.cfg.welcome_message
        if custom_msg:
            description = custom_msg.replace("{user}", member.mention).replace(
                "{server}", member.guild.name
            )
        else:
            description = f"Hey {member.mention}, glad you're here! 👋"

        em = discord.Embed(
            title=f"Welcome to {member.guild.name}!",
            description=description,
            color=discord.Color.blurple(),
        )
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(
            name="Account created",
            value=discord.utils.format_dt(member.created_at, style="R"),
            inline=True,
        )
        em.set_footer(text=f"Member #{member.guild.member_count}")

        try:
            await channel.send(embed=em)
            log.info("Sent welcome message for %s in channel %s", member, welcome_id)
        except discord.HTTPException:
            log.exception("Failed to send welcome message for %s", member)

    # ── /mod purge ───────────────────────────────────────────────────────────

    @mod.command(name="purge", description="Delete a number of recent messages from a channel")
    @app_commands.describe(
        amount="Number of messages to delete (1–100)",
        channel="Channel to purge (defaults to current channel)",
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if await self._deny(interaction):
            return

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = await target.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to delete messages in that channel.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Purge failed in channel %s", target.id)
            await interaction.followup.send(f"Purge failed: {exc}", ephemeral=True)
            return

        count = len(deleted)
        log.info("Purged %d messages in %s by %s", count, target.id, interaction.user)
        await self._log_action("purge", interaction.user, f"#{target.name}", None, f"{count} messages deleted")
        await interaction.followup.send(
            f"🗑️ Deleted **{count}** message(s) from {target.mention}.",
            ephemeral=True,
        )

    # ── /mod kick ────────────────────────────────────────────────────────────

    @mod.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(user="Member to kick", reason="Reason for the kick")
    async def kick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        if await self._deny(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"{interaction.user} — {reason}" if reason else str(interaction.user)
        try:
            await user.kick(reason=audit_reason)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to kick that user.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Kick failed for %s", user.id)
            await interaction.followup.send(f"Kick failed: {exc}", ephemeral=True)
            return

        log.info("Kicked %s (%s) by %s — reason: %s", user, user.id, interaction.user, reason)
        await self._log_action("kick", interaction.user, f"{user} ({user.id})", reason)
        msg = f"👢 **{user}** has been kicked."
        if reason:
            msg += f"\n**Reason:** {reason}"
        await interaction.followup.send(msg, ephemeral=True)

    # ── /mod timeout ─────────────────────────────────────────────────────────

    @mod.command(name="timeout", description="Temporarily mute a member")
    @app_commands.describe(
        user="Member to time out",
        duration="Duration (e.g. 10m, 2h, 1d — max 28d)",
        reason="Reason for the timeout",
    )
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: str,
        reason: str | None = None,
    ) -> None:
        if await self._deny(interaction):
            return

        td = _parse_duration(duration)
        if td is None:
            await interaction.response.send_message(
                "Invalid duration. Use a format like `10m`, `2h`, `1d` (max 28d).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"{interaction.user} — {reason}" if reason else str(interaction.user)
        try:
            await user.timeout(td, reason=audit_reason)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to time out that user.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Timeout failed for %s", user.id)
            await interaction.followup.send(f"Timeout failed: {exc}", ephemeral=True)
            return

        # Use the expiry time Discord actually stored, not our pre-call estimate.
        # This avoids clock-skew/latency making the displayed time wrong for some users.
        until = user.timed_out_until or (discord.utils.utcnow() + td)
        log.info("Timed out %s (%s) for %s by %s — reason: %s", user, user.id, duration, interaction.user, reason)
        await self._log_action("timeout", interaction.user, f"{user} ({user.id})", reason, f"Duration: {duration} — until {discord.utils.format_dt(until)}")
        msg = f"⏱️ **{user}** has been timed out until {discord.utils.format_dt(until, style='f')} ({discord.utils.format_dt(until, style='R')})."
        if reason:
            msg += f"\n**Reason:** {reason}"
        sent = await interaction.followup.send(msg, ephemeral=True)

        # Auto-delete the ephemeral message when the timeout expires so the
        # countdown doesn't linger past 0. Interaction tokens last 15 min max —
        # if the timeout is longer, the delete will silently fail and that's fine.
        async def _expire(message: discord.Message, delay: float) -> None:
            await asyncio.sleep(delay)
            try:
                await message.delete()
            except discord.HTTPException:
                pass  # token expired or already dismissed — ignore

        asyncio.create_task(_expire(sent, td.total_seconds()))

    # ── /mod untimeout ───────────────────────────────────────────────────────

    @mod.command(name="untimeout", description="Remove a timeout from a member")
    @app_commands.describe(user="Member to remove timeout from", reason="Reason")
    async def untimeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        if await self._deny(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"{interaction.user} — {reason}" if reason else str(interaction.user)
        try:
            await user.timeout(None, reason=audit_reason)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to remove the timeout for that user.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Untimeout failed for %s", user.id)
            await interaction.followup.send(f"Untimeout failed: {exc}", ephemeral=True)
            return

        log.info("Removed timeout for %s (%s) by %s", user, user.id, interaction.user)
        await self._log_action("untimeout", interaction.user, f"{user} ({user.id})", reason)
        await interaction.followup.send(
            f"✅ Timeout removed for **{user}**.", ephemeral=True
        )

    # ── /mod ban ─────────────────────────────────────────────────────────────

    @mod.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(
        user="Member to ban",
        reason="Reason for the ban",
        delete_days="Days of message history to delete (0–7)",
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        if await self._deny(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        audit_reason = f"{interaction.user} — {reason}" if reason else str(interaction.user)
        try:
            await user.ban(reason=audit_reason, delete_message_days=delete_days)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to ban that user.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Ban failed for %s", user.id)
            await interaction.followup.send(f"Ban failed: {exc}", ephemeral=True)
            return

        log.info("Banned %s (%s) by %s — reason: %s", user, user.id, interaction.user, reason)
        await self._log_action("ban", interaction.user, f"{user} ({user.id})", reason, f"Deleted {delete_days}d of messages" if delete_days else None)
        msg = f"🔨 **{user}** has been banned."
        if reason:
            msg += f"\n**Reason:** {reason}"
        await interaction.followup.send(msg, ephemeral=True)

    # ── /mod unban ───────────────────────────────────────────────────────────

    @mod.command(name="unban", description="Unban a user by their ID")
    @app_commands.describe(
        user_id="The ID of the user to unban",
        reason="Reason for the unban",
    )
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        if await self._deny(interaction):
            return

        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                "Invalid user ID. Please provide a numeric Discord user ID.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.guild is None:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        audit_reason = f"{interaction.user} — {reason}" if reason else str(interaction.user)
        target = discord.Object(id=uid)
        try:
            await interaction.guild.unban(target, reason=audit_reason)
        except discord.NotFound:
            await interaction.followup.send(
                f"No ban found for user ID `{uid}`.", ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to unban users.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.exception("Unban failed for user_id %s", uid)
            await interaction.followup.send(f"Unban failed: {exc}", ephemeral=True)
            return

        log.info("Unbanned user_id=%s by %s — reason: %s", uid, interaction.user, reason)
        await self._log_action("unban", interaction.user, f"User ID {uid}", reason)
        msg = f"✅ User `{uid}` has been unbanned."
        if reason:
            msg += f"\n**Reason:** {reason}"
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: CreatorBot) -> None:
    await bot.add_cog(ModerationCog(bot))
