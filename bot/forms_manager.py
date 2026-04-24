"""Forms system.

Forms are created and configured via the web admin panel, then users
interact with them via Discord slash commands (/form submit, /form list).

Submissions are collected via Discord modals (max 5 TextInput fields).
State is persisted via Config.forms and Config.form_submissions in config.json.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.config import Config

log = logging.getLogger(__name__)

MAX_FIELDS = 5          # Discord modal hard limit
MAX_SUBMISSIONS = 200   # Max submissions stored per form


def new_form_id() -> str:
    return "form_" + uuid.uuid4().hex[:8]


def build_modal(form: dict) -> discord.ui.Modal:
    """Build a Discord Modal from a form definition dict."""
    fields = form.get("fields", [])[:MAX_FIELDS]

    class FormModal(discord.ui.Modal, title=form.get("name", "Form")[:45]):
        def __init__(self, cfg: Config):
            super().__init__()
            self._cfg = cfg
            self._form = form
            for field in fields:
                style = (
                    discord.TextStyle.paragraph
                    if field.get("style") == "paragraph"
                    else discord.TextStyle.short
                )
                text_input = discord.ui.TextInput(
                    label=field.get("label", "Field")[:45],
                    style=style,
                    placeholder=field.get("placeholder", "")[:100] or None,
                    required=field.get("required", True),
                    min_length=field.get("min_length") or None,
                    max_length=field.get("max_length") or None,
                )
                self.add_item(text_input)

        async def on_submit(self, interaction: discord.Interaction):
            answers = [str(child.value) for child in self.children]
            submission = {
                "form_id": self._form["id"],
                "user_id": str(interaction.user.id),
                "user_name": str(interaction.user),
                "submitted_at": int(time.time()),
                "answers": answers,
            }
            self._cfg.add_form_submission(submission)

            # Post to submission channel if configured
            channel_id = self._form.get("submission_channel_id")
            if channel_id and interaction.client:
                ch = interaction.client.get_channel(int(channel_id))
                if ch:
                    embed = _build_submission_embed(self._form, submission, interaction.user)
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        log.exception(
                            "Failed to send form submission to channel %s", channel_id
                        )

            # DM the submitter if configured
            if self._form.get("dm_submitter"):
                try:
                    embed = _build_submission_embed(self._form, submission, interaction.user)
                    await interaction.user.send(
                        content="Your form submission was received:",
                        embed=embed,
                    )
                except Exception:
                    pass  # DMs may be closed

            await interaction.response.send_message(
                self._form.get("confirmation_message") or "Your response has been submitted!",
                ephemeral=True,
            )
            log.info(
                "Form submission: form_id=%s user=%s", self._form["id"], interaction.user
            )

        async def on_error(self, interaction: discord.Interaction, error: Exception):
            log.exception("Error in form modal: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong submitting the form. Please try again.",
                    ephemeral=True,
                )

    return FormModal


def _build_submission_embed(
    form: dict, submission: dict, user: discord.User | discord.Member
) -> discord.Embed:
    fields = form.get("fields", [])[:MAX_FIELDS]
    embed = discord.Embed(
        title=f"📋 New Submission — {form.get('name', 'Form')}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=str(user), icon_url=user.display_avatar.url if user.display_avatar else None)
    for i, field in enumerate(fields):
        answer = submission["answers"][i] if i < len(submission["answers"]) else ""
        embed.add_field(
            name=field.get("label", f"Field {i+1}"),
            value=answer[:1024] or "*no answer*",
            inline=False,
        )
    embed.set_footer(text=f"User ID: {submission['user_id']}")
    return embed
