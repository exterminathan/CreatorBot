"""Giveaway system.

Giveaways are posted as regular bot messages (not webhooks) so that
Discord button interactions can be tracked back to the bot.

State is persisted via Config.giveaways (list of dicts) stored in config.json.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.config import Config

log = logging.getLogger(__name__)

GIVEAWAY_EMOJI = "🎉"
GIVEAWAY_COLOR = 0x5865F2  # Discord blurple
ENDED_COLOR = 0x808080     # Grey for ended giveaways
ENTRIES_BUTTON_ID = "giveaway_enter"

# Regex: parse "30s", "5m", "2h", "1d" etc.
_DURATION_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(raw: str) -> int | None:
    """Return duration in seconds, or None if invalid. Max 30 days."""
    m = _DURATION_RE.match(raw.strip())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    seconds = value * _DURATION_UNITS[unit]
    if seconds <= 0 or seconds > 30 * 86400:
        return None
    return seconds


def _fmt_duration(seconds: float) -> str:
    """Format remaining seconds as a human-readable string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h {m}m"
    d, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{d}d {h}h"


def _build_embed(giveaway: dict, remaining: float | None = None) -> discord.Embed:
    """Build the giveaway embed from a giveaway dict."""
    ended = giveaway.get("ended", False)
    end_ts = int(giveaway["end_time"])
    winner_count = giveaway["winner_count"]
    entries = giveaway.get("entries", [])
    winners = giveaway.get("winners", [])
    prize = giveaway["prize"]
    host_id = giveaway.get("host_id")

    if ended:
        color = ENDED_COLOR
        if winners:
            winner_mentions = ", ".join(f"<@{w}>" for w in winners)
            description = f"**Winners:** {winner_mentions}"
        else:
            description = "No valid entries — no winner was drawn."
        title = f"{GIVEAWAY_EMOJI} Giveaway Ended — {prize}"
    else:
        color = GIVEAWAY_COLOR
        description = (
            f"Click the button below to enter!\n\n"
            f"**Ends:** <t:{end_ts}:R> (<t:{end_ts}:f>)\n"
            f"**Winners:** {winner_count}"
        )
        title = f"{GIVEAWAY_EMOJI} GIVEAWAY — {prize}"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Entries", value=str(len(entries)), inline=True)
    embed.add_field(name="Winners", value=str(winner_count), inline=True)
    if host_id:
        embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
    embed.set_footer(text=f"{'Ended' if ended else 'Ends'} at")
    embed.timestamp = discord.utils.utcnow().__class__.fromtimestamp(end_ts, tz=discord.utils.utcnow().tzinfo)
    return embed


def _build_view(giveaway: dict) -> discord.ui.View | None:
    """Build the button view for an active giveaway."""
    if giveaway.get("ended", False):
        return None
    entries = giveaway.get("entries", [])
    view = discord.ui.View(timeout=None)
    btn = discord.ui.Button(
        label=f"Enter {GIVEAWAY_EMOJI} ({len(entries)})",
        style=discord.ButtonStyle.primary,
        custom_id=f"{ENTRIES_BUTTON_ID}:{giveaway['message_id']}",
        emoji=GIVEAWAY_EMOJI,
    )
    view.add_item(btn)
    return view


class GiveawayManager:
    """Manages all giveaway lifecycle: creation, entries, ending, rerrolling."""

    def __init__(self, bot: discord.ext.commands.Bot, cfg: Config):
        self.bot = bot
        self.cfg = cfg
        self._tasks: dict[str, asyncio.Task] = {}  # message_id -> expiry task

    # ── Public API ──────────────────────────────────────────────────────────

    async def start(
        self,
        channel: discord.TextChannel,
        prize: str,
        winner_count: int,
        duration_seconds: int,
        host: discord.User | discord.Member,
        announcement_message: str | None = None,
    ) -> dict:
        """Create and post a giveaway. Returns the giveaway dict."""
        end_time = time.time() + duration_seconds

        # Placeholder — we need the message_id before we can build the button
        giveaway: dict = {
            "message_id": "",  # filled in after posting
            "channel_id": channel.id,
            "guild_id": channel.guild.id if channel.guild else 0,
            "prize": prize,
            "winner_count": winner_count,
            "end_time": end_time,
            "host_id": host.id,
            "entries": [],
            "excluded_entries": [],
            "ended": False,
            "winners": [],
            "announcement_message": announcement_message or "",
        }

        # Send the optional announcement message first (supports role/user pings)
        if announcement_message:
            await channel.send(announcement_message)

        embed = _build_embed(giveaway)
        # Temporarily post without button to get the message ID
        msg = await channel.send(embed=embed)

        giveaway["message_id"] = str(msg.id)
        # Now post with button (edit the message)
        view = _build_view(giveaway)
        await msg.edit(embed=embed, view=view)

        self.cfg.add_giveaway(giveaway)
        self._schedule_expiry(giveaway, duration_seconds)
        log.info(
            "Giveaway started: msg=%s channel=%s prize=%r duration=%ds",
            msg.id, channel.id, prize, duration_seconds,
        )
        return giveaway

    async def add_entry(self, message_id: str, user_id: int) -> str:
        """Add or remove a user's entry. Returns 'added', 'removed', or 'ended'."""
        giveaway = self.cfg.get_giveaway(message_id)
        if giveaway is None or giveaway.get("ended"):
            return "ended"
        entries: list[int] = giveaway.setdefault("entries", [])
        if user_id in entries:
            entries.remove(user_id)
            action = "removed"
        else:
            entries.append(user_id)
            action = "added"
        self.cfg.update_giveaway(message_id, {"entries": entries})
        # Update button label
        await self._refresh_message(giveaway)
        return action

    async def end(self, message_id: str, *, reroll: bool = False) -> dict | None:
        """End a giveaway, pick winners, edit the message. Returns updated giveaway."""
        giveaway = self.cfg.get_giveaway(message_id)
        if giveaway is None:
            return None

        # Cancel scheduled task if present
        task = self._tasks.pop(message_id, None)
        if task and not task.done():
            task.cancel()

        entries: list[int] = giveaway.get("entries", [])
        excluded: list[int] = giveaway.get("excluded_entries", [])
        winner_count = giveaway["winner_count"]

        eligible = [e for e in set(entries) if e not in excluded]
        if eligible:
            winners = random.sample(eligible, min(winner_count, len(eligible)))
        else:
            winners = []

        updates: dict = {"ended": True, "winners": winners}
        if not reroll:
            updates["end_time"] = time.time()  # finalize time
        else:
            # Reroll: keep original end_time, just update winners
            pass

        self.cfg.update_giveaway(message_id, updates)
        giveaway.update(updates)

        await self._post_end_result(giveaway, reroll=reroll)
        log.info(
            "Giveaway ended: msg=%s winners=%s reroll=%s",
            message_id, winners, reroll,
        )
        return giveaway

    async def delete(self, message_id: str) -> bool:
        """Delete a giveaway record (and optionally its message). Returns True if found."""
        giveaway = self.cfg.get_giveaway(message_id)
        if giveaway is None:
            return False
        task = self._tasks.pop(message_id, None)
        if task and not task.done():
            task.cancel()
        # Try to delete the Discord message
        try:
            channel = self.bot.get_channel(giveaway["channel_id"])
            if channel:
                msg = await channel.fetch_message(int(message_id))
                await msg.delete()
        except Exception:
            pass
        self.cfg.remove_giveaway(message_id)
        return True

    def get_active(self) -> list[dict]:
        return [g for g in self.cfg.giveaways if not g.get("ended")]

    def get_all(self) -> list[dict]:
        return list(self.cfg.giveaways)

    # ── Resume on restart ───────────────────────────────────────────────────

    async def resume_all(self):
        """Called on bot ready — reschedule expiry tasks for active giveaways."""
        now = time.time()
        for giveaway in self.cfg.giveaways:
            if giveaway.get("ended"):
                continue
            remaining = giveaway["end_time"] - now
            if remaining <= 0:
                # Already expired while bot was offline
                await self.end(giveaway["message_id"])
            else:
                self._schedule_expiry(giveaway, remaining)
        log.info(
            "Giveaway manager resumed: %d active", len(self.get_active())
        )

    # ── Internal helpers ────────────────────────────────────────────────────

    def _schedule_expiry(self, giveaway: dict, delay_seconds: float):
        message_id = giveaway["message_id"]
        task = asyncio.get_event_loop().create_task(
            self._expiry_task(message_id, delay_seconds)
        )
        self._tasks[message_id] = task

    async def _expiry_task(self, message_id: str, delay_seconds: float):
        try:
            await asyncio.sleep(delay_seconds)
            giveaway = self.cfg.get_giveaway(message_id)
            if giveaway and not giveaway.get("ended"):
                await self.end(message_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Error in giveaway expiry task for %s", message_id)

    async def _refresh_message(self, giveaway: dict):
        """Edit the giveaway message to reflect updated entry count."""
        try:
            channel = self.bot.get_channel(giveaway["channel_id"])
            if not channel:
                return
            msg = await channel.fetch_message(int(giveaway["message_id"]))
            embed = _build_embed(giveaway)
            view = _build_view(giveaway)
            await msg.edit(embed=embed, view=view)
        except Exception:
            log.debug("Could not refresh giveaway message %s", giveaway.get("message_id"))

    async def _post_end_result(self, giveaway: dict, *, reroll: bool = False):
        """Edit the original message to show ended state and post winner announcement."""
        try:
            channel = self.bot.get_channel(giveaway["channel_id"])
            if not channel:
                return
            msg = await channel.fetch_message(int(giveaway["message_id"]))
            embed = _build_embed(giveaway)
            # Remove button by passing empty view
            await msg.edit(embed=embed, view=discord.ui.View())
        except Exception:
            log.debug("Could not edit giveaway message to ended state: %s", giveaway.get("message_id"))

        # Announce winners
        winners = giveaway.get("winners", [])
        prize = giveaway["prize"]
        try:
            channel = self.bot.get_channel(giveaway["channel_id"])
            if channel:
                if winners:
                    mentions = " ".join(f"<@{w}>" for w in winners)
                    verb = "Rerolled winner" if reroll else f"{GIVEAWAY_EMOJI} Congratulations"
                    await channel.send(
                        f"{verb}! {mentions} won **{prize}**! "
                        f"(https://discord.com/channels/{giveaway['guild_id']}/{giveaway['channel_id']}/{giveaway['message_id']})"
                    )
                else:
                    await channel.send(
                        f"The giveaway for **{prize}** ended with no valid entries. No winner was drawn."
                    )
        except Exception:
            log.debug("Could not post giveaway winner announcement for %s", giveaway.get("message_id"))
