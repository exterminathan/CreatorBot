"""Forms slash commands.

Discord-side features (what regular users can interact with):
  /form submit [form_name]  — Open a modal to fill out a form
  /form list                — List available forms

Admin configuration lives in the web panel (create, edit, delete forms,
configure submission channels, required roles, etc.).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from bot.forms_manager import build_modal, MAX_FIELDS

if TYPE_CHECKING:
    from bot.main import CreatorBot

log = logging.getLogger(__name__)


class FormsCog(discord.ext.commands.Cog):
    """Slash-command group ``/form`` for user-facing form submissions."""

    form = app_commands.Group(name="form", description="Submit or list available forms")

    def __init__(self, bot: CreatorBot):
        self.bot = bot

    def _get_available_forms(self, interaction: discord.Interaction) -> list[dict]:
        """Return forms that are enabled and accessible to the invoking user."""
        available = []
        for f in self.bot.cfg.forms:
            if not f.get("enabled", True):
                continue
            required_roles = f.get("required_role_ids", [])
            if required_roles and isinstance(interaction.user, discord.Member):
                user_role_ids = [r.id for r in interaction.user.roles]
                if not any(int(r) in user_role_ids for r in required_roles):
                    continue
            available.append(f)
        return available

    # ── /form list ───────────────────────────────────────────────────────────

    @form.command(name="list", description="Show all forms you can currently fill out")
    async def form_list(self, interaction: discord.Interaction):
        available = self._get_available_forms(interaction)
        if not available:
            await interaction.response.send_message(
                "There are no forms available right now.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📋 Available Forms",
            color=discord.Color.blurple(),
        )
        for f in available:
            field_count = len(f.get("fields", []))
            desc = f.get("description") or ""
            value = (f"{desc}\n" if desc else "") + f"`{field_count} question{'s' if field_count != 1 else ''}`"
            embed.add_field(
                name=f.get("name", "Unnamed Form"),
                value=value,
                inline=False,
            )
        embed.set_footer(text='Use /form submit to fill one out')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /form submit ─────────────────────────────────────────────────────────

    @form.command(name="submit", description="Fill out and submit a form")
    @app_commands.describe(form_name="Name of the form to fill out (use /form list to see options)")
    async def form_submit(self, interaction: discord.Interaction, form_name: str):
        available = self._get_available_forms(interaction)

        # Case-insensitive match
        matched = next(
            (f for f in available if f.get("name", "").lower() == form_name.lower()),
            None,
        )
        if matched is None:
            names = [f.get("name", "") for f in available]
            if names:
                options = ", ".join(f"`{n}`" for n in names[:10])
                await interaction.response.send_message(
                    f"Form **{form_name}** not found. Available: {options}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "There are no forms available right now.", ephemeral=True
                )
            return

        fields = matched.get("fields", [])
        if not fields:
            await interaction.response.send_message(
                "That form has no questions configured yet.", ephemeral=True
            )
            return

        ModalClass = build_modal(matched)
        modal = ModalClass(self.bot.cfg)
        await interaction.response.send_modal(modal)

    @form_submit.autocomplete("form_name")
    async def form_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        available = self._get_available_forms(interaction)
        return [
            app_commands.Choice(name=f.get("name", ""), value=f.get("name", ""))
            for f in available
            if current.lower() in f.get("name", "").lower()
        ][:25]


async def setup(bot: CreatorBot):
    await bot.add_cog(FormsCog(bot))
