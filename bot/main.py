from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time

from aiohttp import web
import discord
from discord.ext import commands

from ai.client import GeminiClient
from ai.persona import Persona
from ai.prompt_builder import build_post_messages, build_interaction_messages
from bot.config import Config
from bot.webhook_manager import WebhookManager

def _setup_logging() -> None:
    """Use structured Cloud Logging on Cloud Run; plain text locally."""
    if os.environ.get("K_SERVICE"):  # injected automatically by Cloud Run
        try:
            import google.cloud.logging as cloud_logging
            cloud_logging.Client().setup_logging(log_level=logging.INFO)
            return
        except Exception:
            pass  # fall through if SDK unavailable or auth fails
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


_setup_logging()
log = logging.getLogger("cybot")

GENERATION_TIMEOUT_SECONDS = 75

_URL_RE = re.compile(
    r'https?://[^\s<>\"\')]+', re.IGNORECASE
)

_MAX_EXCLUSION_RETRIES = 3


def _find_exclusion_violations(text: str, exclusion_list: list[dict]) -> list[str]:
    """Return topics (severity >= 2) whose word/phrase appears in text."""
    violations: list[str] = []
    for entry in exclusion_list:
        if entry.get("severity", 3) < 2:
            continue  # severity 1 = explicitly allowed
        topic = entry.get("topic", "").strip()
        if not topic:
            continue
        pattern = r'\b' + re.escape(topic) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            violations.append(topic)
    return violations


def _extract_urls(text: str) -> list[str]:
    """Return all HTTP(S) URLs found in text."""
    return _URL_RE.findall(text)


def _strip_urls(text: str) -> str:
    """Remove all URLs from text and clean up leftover whitespace."""
    cleaned = _URL_RE.sub('', text)
    cleaned = re.sub(r'[ \t]+\n', '\n', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def _surface_links(text: str, extra_links: list[str] | None = None) -> str:
    """Remove all URLs from the body and append them at the end on their own
    lines so Discord can generate embeds.  ``extra_links`` are URLs sourced
    from the original prompt that must always appear."""
    links = _extract_urls(text)
    if extra_links:
        seen = set(links)
        for link in extra_links:
            if link not in seen:
                links.append(link)
                seen.add(link)
    if not links:
        return text
    cleaned = _strip_urls(text)
    return cleaned + '\n' + '\n'.join(links)


# ── Web server (health check + admin UI) ────────────────────────────────────
def _start_web_server(cfg: Config, persona: Persona):
    """Run the web server on $PORT for Cloud Run health check + admin UI."""
    from bot.web import create_app

    port = int(os.environ.get("PORT", 8080))
    app = create_app(cfg, persona)
    runner = web.AppRunner(app)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", port)
    loop.run_until_complete(site.start())
    log.info("Web server listening on port %d", port)
    loop.run_forever()


class CyBot(commands.Bot):
    """The CyBot Discord bot."""

    def __init__(self, cfg: Config, persona: Persona):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.cfg = cfg
        self.webhooks = WebhookManager(cfg)
        self.persona = persona
        self.gemini = GeminiClient(
            api_key=cfg.gemini_api_key,
            model_name=cfg.gemini_model,
        )
        # Rate-limit tracking for interaction replies: user_id -> last reply timestamp
        self._interaction_cooldowns: dict[int, float] = {}
        self.cfg._interaction_cooldowns = self._interaction_cooldowns

    async def setup_hook(self):
        await self.load_extension("bot.cogs.admin")

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("Active channels: %s", self.cfg.active_channels)
        # Use the bot's own avatar for webhook messages if no override is set
        if self.cfg.cy_avatar_url is None and self.user.avatar:
            self.cfg.cy_avatar_url = self.user.avatar.url
            log.info("Using bot avatar for webhooks: %s", self.cfg.cy_avatar_url)
        # Sync slash commands to every guild for instant registration
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), guild.id)
        # Populate channel/role info for web API
        self._populate_guild_info()
        await self._populate_user_names()

    def _populate_guild_info(self):
        """Store channel/role info from all guilds for the web API."""
        channels = []
        roles = []
        seen_roles: set[int] = set()
        for guild in self.guilds:
            for ch in guild.text_channels:
                channels.append({"id": str(ch.id), "name": ch.name, "guild": guild.name})
            for role in guild.roles:
                if role.id not in seen_roles and not role.managed:
                    roles.append({"id": str(role.id), "name": role.name, "color": str(role.color)})
                    seen_roles.add(role.id)
        self.cfg._available_channels = channels
        self.cfg._available_roles = roles

    async def _populate_user_names(self):
        """Fetch display names for all admin user IDs via the Discord API."""
        user_names: dict[str, str] = {}
        for uid in self.cfg.admin_user_ids:
            try:
                user = await self.fetch_user(uid)
                user_names[str(uid)] = user.display_name or user.name
            except Exception:
                pass
        self.cfg._user_names = user_names

    async def on_guild_channel_create(self, channel):
        if isinstance(channel, discord.TextChannel):
            self.cfg._available_channels.append(
                {"id": str(channel.id), "name": channel.name, "guild": channel.guild.name}
            )

    async def on_guild_channel_delete(self, channel):
        self.cfg._available_channels = [
            c for c in self.cfg._available_channels if c["id"] != str(channel.id)
        ]

    async def on_guild_role_create(self, role):
        if not role.managed:
            self.cfg._available_roles.append(
                {"id": str(role.id), "name": role.name, "color": str(role.color)}
            )

    async def on_guild_role_delete(self, role):
        self.cfg._available_roles = [
            r for r in self.cfg._available_roles if r["id"] != str(role.id)
        ]

    async def on_guild_role_update(self, before, after):
        for r in self.cfg._available_roles:
            if r["id"] == str(after.id):
                r["name"] = after.name
                r["color"] = str(after.color)
                break

    async def on_guild_join(self, guild: discord.Guild):
        """Sync slash commands and register channels/roles when added to a new server."""
        log.info("Joined new guild: %s (ID: %s)", guild.name, guild.id)
        # Sync slash commands to the new guild
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to new guild %s", len(synced), guild.id)
        except Exception:
            log.exception("Failed to sync commands to guild %s", guild.id)
        # Register channels and roles
        for ch in guild.text_channels:
            self.cfg._available_channels.append(
                {"id": str(ch.id), "name": ch.name, "guild": guild.name}
            )
        seen = {int(r["id"]) for r in self.cfg._available_roles}
        for role in guild.roles:
            if role.id not in seen and not role.managed:
                self.cfg._available_roles.append(
                    {"id": str(role.id), "name": role.name, "color": str(role.color)}
                )
                seen.add(role.id)

    async def on_guild_remove(self, guild: discord.Guild):
        """Clean up channels/roles when removed from a guild."""
        log.info("Removed from guild: %s (ID: %s)", guild.name, guild.id)
        self.cfg._available_channels = [
            c for c in self.cfg._available_channels if c.get("guild") != guild.name
        ]

    def _get_user_permission(self, member: discord.Member, perm: str) -> bool:
        """Check a permission for a member based on their roles.

        Uses 'allow wins' model: if any role explicitly allows, it's granted
        even if another role explicitly denies.
        """
        allow = False
        deny = False
        for role in member.roles:
            role_perms = self.cfg.role_permissions.get(str(role.id))
            if role_perms and perm in role_perms:
                val = role_perms[perm]
                if val is True:
                    allow = True
                elif val is False:
                    deny = True
        if allow:
            return True
        if deny:
            return False
        return self.cfg.default_permissions.get(perm, False)

    async def generate_post(self, prompt: str) -> str:
        """Generate a post message as Cy from an admin prompt."""
        prompt_links = _extract_urls(prompt)
        # Strip URLs from prompt so AI doesn't mangle them
        clean_prompt = _strip_urls(prompt) if prompt_links else prompt
        ps = self.cfg.post_settings
        messages = build_post_messages(
            self.persona, clean_prompt, ps.get("system_prompt", ""),
            exclusion_list=self.cfg.exclusion_list,
            slang_dict=self.cfg.slang_dict,
            template=self.cfg.system_prompt_template,
        )
        text = ""
        for attempt in range(_MAX_EXCLUSION_RETRIES):
            text = await self.gemini.generate(
                messages,
                max_tokens=ps.get("max_tokens", 512),
                temperature=ps.get("temperature", 0.8),
            )
            violations = _find_exclusion_violations(text, self.cfg.exclusion_list)
            if not violations:
                break
            log.warning(
                "generate_post exclusion violation (attempt %d/%d): %s",
                attempt + 1, _MAX_EXCLUSION_RETRIES, violations,
            )
        return _surface_links(text, extra_links=prompt_links)

    async def generate_interaction(self, user_message: str, user_name: str) -> str:
        """Generate an interaction reply to a user's @Cy message."""
        prompt_links = _extract_urls(user_message)
        clean_msg = _strip_urls(user_message) if prompt_links else user_message
        isettings = self.cfg.interaction_settings
        messages = build_interaction_messages(
            self.persona, clean_msg, user_name,
            isettings.get("system_prompt", ""),
            exclusion_list=self.cfg.exclusion_list,
            slang_dict=self.cfg.slang_dict,
            template=self.cfg.system_prompt_template,
        )
        text = ""
        for attempt in range(_MAX_EXCLUSION_RETRIES):
            text = await self.gemini.generate(
                messages,
                max_tokens=isettings.get("max_tokens", 256),
                temperature=isettings.get("temperature", 0.9),
            )
            violations = _find_exclusion_violations(text, self.cfg.exclusion_list)
            if not violations:
                break
            log.warning(
                "generate_interaction exclusion violation (attempt %d/%d): %s",
                attempt + 1, _MAX_EXCLUSION_RETRIES, violations,
            )
        return _surface_links(text, extra_links=prompt_links)

    # Keep old name as alias for admin cog compatibility
    async def generate(self, prompt: str) -> str:
        return await self.generate_post(prompt)

    def _fallback_response(self) -> str:
        """Return a random default response for when generation fails."""
        responses = self.cfg.default_responses
        if responses:
            return random.choice(responses)
        return "hmm"

    async def log_to_channel(self, embed: discord.Embed) -> None:
        """Send a log embed to the configured log channel, if set."""
        if not self.cfg.log_channel_id:
            return
        channel = self.get_channel(self.cfg.log_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(embed=embed)
        except Exception:
            log.warning("Failed to send log to channel %s", self.cfg.log_channel_id)

    async def on_message(self, message: discord.Message):
        """Handle @Cy mentions in the interaction channel."""
        # Ignore own messages and bot messages
        if message.author.bot:
            return
        # Kill switch: block all public output when disabled
        if not self.cfg.bot_enabled:
            return
        # Check if interactions are enabled
        isettings = self.cfg.interaction_settings
        if not isettings.get("enabled", False):
            return
        # Check per-channel permission (default: enabled)
        chperms = self.cfg.channel_permissions.get(str(message.channel.id), {})
        if not chperms.get("can_interact", True):
            return
        # If specific interaction channels are configured, restrict to them
        interaction_channels = isettings.get("channel_ids", [])
        if interaction_channels and message.channel.id not in interaction_channels:
            return
        # Must mention the bot
        if self.user not in message.mentions:
            return

        # Admin users always bypass permission and cooldown checks
        is_admin = message.author.id in self.cfg.admin_user_ids

        # Permission check: can this user interact?
        if not is_admin and isinstance(message.author, discord.Member):
            if not self._get_user_permission(message.author, "can_interact"):
                return

        # Rate-limit check
        user_id = message.author.id
        now = time.time()
        cooldown_secs = isettings.get("rate_limit_seconds", 300)
        # Admins and members with bypass_cooldown skip the rate limit
        bypass_cooldown = is_admin
        if not bypass_cooldown and isinstance(message.author, discord.Member):
            bypass_cooldown = self._get_user_permission(message.author, "bypass_cooldown")
        if not bypass_cooldown and cooldown_secs > 0:
            last_used = self._interaction_cooldowns.get(user_id, 0)
            if now - last_used < cooldown_secs:
                remaining = int(cooldown_secs - (now - last_used))
                await message.reply(
                    f"Slow down! You can interact again in {remaining}s.",
                    mention_author=False,
                )
                return

        self._interaction_cooldowns[user_id] = now

        # Strip the bot mention from the message content
        clean_content = message.content
        for mention_str in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            clean_content = clean_content.replace(mention_str, "").strip()

        if not clean_content:
            clean_content = "say something random"

        log.info(
            "interaction: user=%s channel=%s msg=%r",
            message.author, message.channel.id, clean_content[:120],
        )

        # Pre-LLM exclusion check: if the user's message directly mentions an
        # excluded topic (sev >= 2), skip generation entirely and use a neutral
        # fallback.  This ensures all excluded topics are handled identically
        # regardless of any model-level bias.
        if _find_exclusion_violations(clean_content, self.cfg.exclusion_list):
            text = self._fallback_response()
            mention = f"<@{message.author.id}>"
            text = f"{mention} {text}"
            if message.channel.id in self.cfg.active_channels:
                await self.webhooks.send_as_cy(message.channel, text)
            else:
                await message.reply(text, mention_author=False)
            return

        # Use reaction indicator instead of typing (typing persists with webhooks)
        try:
            await message.add_reaction('\U0001f4ad')
        except discord.HTTPException:
            pass

        try:
            try:
                text = await asyncio.wait_for(
                    self.generate_interaction(clean_content, message.author.display_name),
                    timeout=GENERATION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                log.warning("interaction timeout: user=%s", message.author)
                text = self._fallback_response()
            except Exception:
                log.exception("interaction error: user=%s", message.author)
                text = self._fallback_response()
        finally:
            try:
                await message.remove_reaction('\U0001f4ad', self.user)
            except discord.HTTPException:
                pass

        # Use fallback if generation returned empty / was blocked
        if not text or not text.strip():
            text = self._fallback_response()

        # Prepend user mention so the reply feels personal
        mention = f"<@{message.author.id}>"
        text = f"{mention} {text}"

        # Post via webhook so it appears as Cy
        if message.channel.id in self.cfg.active_channels:
            await self.webhooks.send_as_cy(message.channel, text)
        else:
            await message.reply(text, mention_author=False)

        em = discord.Embed(title="\U0001f4ac Interaction Reply", color=discord.Color.blurple())
        em.timestamp = discord.utils.utcnow()
        em.add_field(name="User", value=str(message.author), inline=True)
        em.add_field(name="Channel", value=message.channel.mention, inline=True)
        em.add_field(name="Message", value=clean_content[:300], inline=False)
        em.add_field(name="Reply", value=text[:500], inline=False)
        await self.log_to_channel(em)

    async def close(self):
        await self.gemini.close()
        await super().close()


def main():
    cfg = Config()
    persona = Persona()
    if cfg.persona_data:
        persona.apply_overrides(cfg.persona_data)

    # Start web server in a daemon thread for Cloud Run
    web_thread = threading.Thread(
        target=_start_web_server, args=(cfg, persona), daemon=True
    )
    web_thread.start()

    bot = CyBot(cfg, persona)
    bot.run(cfg.bot_token, log_handler=None)


if __name__ == "__main__":
    main()
