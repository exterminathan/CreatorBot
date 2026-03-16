import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"


class Config:
    """Loads env vars and manages persistent bot state (active channels)."""

    def __init__(self):
        self.bot_token: str = os.environ["DISCORD_BOT_TOKEN"]
        self.admin_channel_id: int = int(os.environ["ADMIN_CHANNEL_ID"])
        self.admin_user_id: int = int(os.environ["ADMIN_USER_ID"])
        self.gcp_project_id: str = os.environ.get(
            "GCP_PROJECT_ID", "project-a89ff80d-7ecd-456f-aee"
        )
        self.gcp_location: str = os.environ.get("GCP_LOCATION", "us-central1")
        self.gemini_model: str = os.environ.get(
            "GEMINI_MODEL", "gemini-2.0-flash-lite-001"
        )
        self.cy_display_name: str = os.environ.get("CY_DISPLAY_NAME", "Cy")
        self.cy_avatar_url: str | None = os.environ.get("CY_AVATAR_URL") or None

        # Persistent state
        self.active_channels: list[int] = []
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.active_channels = data.get("active_channels", [])

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_channels": self.active_channels}, f, indent=2)

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
        self.save()
        return True
