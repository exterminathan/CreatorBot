import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"
GCS_OBJECT = "config.json"


def _require_env(key: str) -> str:
    """Return the env var value or raise a clear error if missing."""
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            "Check your .env file or Cloud Run environment variables."
        )
    return val


class Config:
    """Loads env vars and manages persistent bot state (active channels)."""

    def __init__(self):
        self.bot_token: str = _require_env("DISCORD_BOT_TOKEN")
        self.admin_channel_id: int = int(_require_env("ADMIN_CHANNEL_ID"))
        self.admin_user_id: int = int(_require_env("ADMIN_USER_ID"))
        self.gemini_api_key: str = _require_env("GEMINI_API_KEY")
        self.gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.cy_display_name: str = os.environ.get("CY_DISPLAY_NAME", "Cy")
        self.cy_avatar_url: str | None = os.environ.get("CY_AVATAR_URL") or None
        self._config_bucket: str | None = os.environ.get("CONFIG_BUCKET")
        self.web_password: str = os.environ.get("WEB_PASSWORD", "")

        # Persistent state
        self.active_channels: list[int] = []
        self.admin_user_ids: list[int] = []
        self.default_channel_id: int | None = None
        self.log_channel_id: int | None = None
        self.persona_data: dict = {}
        self.post_settings: dict = {
            "max_tokens": 512,
            "temperature": 0.8,
            "system_prompt": "",
        }
        self.interaction_settings: dict = {
            "enabled": False,
            "channel_ids": [],
            "max_tokens": 150,
            "temperature": 0.9,
            "rate_limit_seconds": 300,
            "system_prompt": "",
        }
        self.role_permissions: dict = {}
        self.default_permissions: dict = {
            "bypass_cooldown": False,
            "can_interact": True,
            "can_use_commands": False,
            "can_moderate": False,
            "can_view_logs": False,
        }
        self.exclusion_list: list[dict] = []
        self.slang_dict: dict[str, str] = {}
        self.channel_permissions: dict = {}
        self.default_responses: list[str] = [
            "hmm",
            "lol",
            "idk man",
            "bruh",
        ]
        self.system_prompt_template: str = ""
        self.welcome_channel_id: int | None = None
        self.mod_log_channel_id: int | None = None
        self.welcome_message: str = ""
        self.giveaways: list[dict] = []
        self.giveaway_settings: dict = {
            "default_channel_id": None,
            "embed_color": "#5865F2",
            "manager_role_ids": [],
        }
        self.forms: list[dict] = []
        self.form_submissions: list[dict] = []
        # Runtime-only (populated by bot, not persisted)
        self._available_channels: list[dict] = []
        self._available_roles: list[dict] = []
        self._interaction_cooldowns: dict[int, float] = {}
        self._user_names: dict[str, str] = {}
        self._mod_action_log: list[dict] = []  # in-memory only, not persisted
        self._load()

        # Ensure the owner is always in admin list
        if self.admin_user_id not in self.admin_user_ids:
            self.admin_user_ids.insert(0, self.admin_user_id)
            self.save()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        data = {}
        if self._config_bucket:
            try:
                from google.cloud import storage
                blob = storage.Client().bucket(self._config_bucket).blob(GCS_OBJECT)
                if blob.exists():
                    data = json.loads(blob.download_as_text())
                    log.info("Config loaded from GCS bucket %s", self._config_bucket)
            except Exception:
                log.exception("Failed to load config from GCS, falling back to local file")
        if not data and CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

        if not isinstance(data, dict):
            log.error("config.json root is not a JSON object — resetting to defaults")
            data = {}

        # Coerce active_channels and admin_user_ids to lists of ints, tolerating
        # string IDs written by older versions or manual edits.
        def _to_int_list(raw: object) -> list[int]:
            if not isinstance(raw, list):
                return []
            result = []
            for item in raw:
                try:
                    result.append(int(item))
                except (TypeError, ValueError):
                    log.warning("Skipping non-integer ID in config: %r", item)
            return result

        self.active_channels = _to_int_list(data.get("active_channels", []))
        self.admin_user_ids = _to_int_list(data.get("admin_user_ids", []))
        _raw_default = data.get("default_channel_id")
        self.default_channel_id = int(_raw_default) if _raw_default is not None else None
        _raw_log = data.get("log_channel_id")
        self.log_channel_id = int(_raw_log) if _raw_log is not None else None
        self.persona_data = data.get("persona", {})
        if "post_settings" in data:
            ps = data["post_settings"]
            # Migrate old key name
            if "custom_instruction" in ps and "system_prompt" not in ps:
                ps["system_prompt"] = ps.pop("custom_instruction")
            self.post_settings.update(ps)
        if "interaction_settings" in data:
            iss = data["interaction_settings"]
            # Migrate old key name
            if "custom_instruction" in iss and "system_prompt" not in iss:
                iss["system_prompt"] = iss.pop("custom_instruction")
            # Migrate single channel_id -> channel_ids list
            if "channel_id" in iss and "channel_ids" not in iss:
                old_id = iss.pop("channel_id")
                iss["channel_ids"] = [old_id] if old_id else []
            self.interaction_settings.update(iss)
        self.role_permissions = data.get("role_permissions", {})
        if "default_permissions" in data:
            self.default_permissions.update(data["default_permissions"])
        raw_exclusions = data.get("exclusion_list", [])
        # Migrate old string-only format to {topic, severity} dicts
        self.exclusion_list = [
            e if isinstance(e, dict) else {"topic": e, "severity": 3}
            for e in raw_exclusions
        ]
        self.slang_dict = data.get("slang_dict", {})
        if "default_responses" in data:
            self.default_responses = data["default_responses"]
        self.channel_permissions = data.get("channel_permissions", {})
        self.system_prompt_template = data.get("system_prompt_template", "")
        self.bot_enabled: bool = data.get("bot_enabled", True)
        _raw_welcome = data.get("welcome_channel_id")
        self.welcome_channel_id = int(_raw_welcome) if _raw_welcome is not None else None
        _raw_mod_log = data.get("mod_log_channel_id")
        self.mod_log_channel_id = int(_raw_mod_log) if _raw_mod_log is not None else None
        self.welcome_message = data.get("welcome_message", "")
        self.giveaways = data.get("giveaways", [])
        if "giveaway_settings" in data:
            self.giveaway_settings.update(data["giveaway_settings"])
        self.forms = data.get("forms", [])
        self.form_submissions = data.get("form_submissions", [])

    def _to_dict(self) -> dict:
        d: dict = {
            "active_channels": self.active_channels,
            "admin_user_ids": self.admin_user_ids,
            "default_channel_id": self.default_channel_id,
            "log_channel_id": self.log_channel_id,
            "post_settings": self.post_settings,
            "interaction_settings": self.interaction_settings,
            "role_permissions": self.role_permissions,
            "default_permissions": self.default_permissions,
            "exclusion_list": self.exclusion_list,
            "slang_dict": self.slang_dict,
            "channel_permissions": self.channel_permissions,
            "default_responses": self.default_responses,
            "system_prompt_template": self.system_prompt_template,
            "bot_enabled": self.bot_enabled,
            "welcome_channel_id": self.welcome_channel_id,
            "mod_log_channel_id": self.mod_log_channel_id,
            "welcome_message": self.welcome_message,
            "giveaways": self.giveaways,
            "giveaway_settings": self.giveaway_settings,
            "forms": self.forms,
            "form_submissions": self.form_submissions,
        }
        if self.persona_data:
            d["persona"] = self.persona_data
        return d

    def save(self):
        data = json.dumps(self._to_dict(), indent=2)
        if self._config_bucket:
            try:
                from google.cloud import storage
                storage.Client().bucket(self._config_bucket).blob(GCS_OBJECT).upload_from_string(
                    data, content_type="application/json"
                )
                return
            except Exception:
                log.exception("Failed to save config to GCS, falling back to local file")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(data)

    # -- channel management --------------------------------------------------

    def add_channel(self, channel_id: int) -> bool:
        """Add a channel. Returns True if newly added, False if already present."""
        if channel_id in self.active_channels:
            return False
        self.active_channels.append(channel_id)
        self.save()
        return True

    def remove_channel(self, channel_id: int) -> bool:
        """Remove a channel. Returns True if removed, False if not found."""
        if channel_id not in self.active_channels:
            return False
        self.active_channels.remove(channel_id)
        if self.default_channel_id == channel_id:
            self.default_channel_id = None
        self.save()
        return True

    # -- admin management ---------------------------------------------------

    def add_admin(self, user_id: int) -> bool:
        if user_id in self.admin_user_ids:
            return False
        self.admin_user_ids.append(user_id)
        self.save()
        return True

    def remove_admin(self, user_id: int) -> bool:
        if user_id == self.admin_user_id:
            return False  # can't remove owner
        if user_id not in self.admin_user_ids:
            return False
        self.admin_user_ids.remove(user_id)
        self.save()
        return True

    def set_default_channel(self, channel_id: int | None):
        self.default_channel_id = channel_id
        self.save()

    def set_log_channel(self, channel_id: int | None):
        self.log_channel_id = channel_id
        self.save()

    def set_bot_enabled(self, enabled: bool):
        self.bot_enabled = enabled
        self.save()

    # -- giveaway management ------------------------------------------------

    def add_giveaway(self, giveaway: dict) -> None:
        self.giveaways.append(giveaway)
        self.save()

    def get_giveaway(self, message_id: str) -> dict | None:
        for g in self.giveaways:
            if g.get("message_id") == str(message_id):
                return g
        return None

    def update_giveaway(self, message_id: str, updates: dict) -> bool:
        for g in self.giveaways:
            if g.get("message_id") == str(message_id):
                g.update(updates)
                self.save()
                return True
        return False

    def remove_giveaway(self, message_id: str) -> bool:
        before = len(self.giveaways)
        self.giveaways = [g for g in self.giveaways if g.get("message_id") != str(message_id)]
        if len(self.giveaways) != before:
            self.save()
            return True
        return False

    # -- forms management ---------------------------------------------------

    def add_form(self, form: dict) -> None:
        self.forms.append(form)
        self.save()

    def get_form(self, form_id: str) -> dict | None:
        for f in self.forms:
            if f.get("id") == form_id:
                return f
        return None

    def update_form(self, form_id: str, updates: dict) -> bool:
        for f in self.forms:
            if f.get("id") == form_id:
                f.update(updates)
                self.save()
                return True
        return False

    def remove_form(self, form_id: str) -> bool:
        before = len(self.forms)
        self.forms = [f for f in self.forms if f.get("id") != form_id]
        if len(self.forms) != before:
            # Also purge submissions for this form
            self.form_submissions = [
                s for s in self.form_submissions if s.get("form_id") != form_id
            ]
            self.save()
            return True
        return False

    def add_form_submission(self, submission: dict) -> None:
        self.form_submissions.append(submission)
        # Cap total stored submissions to avoid unbounded growth
        if len(self.form_submissions) > 2000:
            self.form_submissions = self.form_submissions[-2000:]
        self.save()
