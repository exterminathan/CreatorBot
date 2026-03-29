"""
Tests: bot/config.py — Config loading & validation
====================================================

Covers:
    - Required env var validation (_require_env / Config.__init__)
    - _load() parsing, type coercion, and migration logic
    - Channel management (add_channel, remove_channel)
    - Admin management (add_admin, remove_admin)
    - set_bot_enabled, set_default_channel, set_log_channel

All tests write state to a tmp_path directory to avoid touching the real
data/config.json.  They patch CONFIG_PATH so Config reads/writes from the
temp directory.

Run:
    pytest tests/test_config.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import bot.config as config_module
from bot.config import Config, _require_env


# ---------------------------------------------------------------------------
# _require_env
# ---------------------------------------------------------------------------

class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "hello")
        assert _require_env("MY_TEST_VAR") == "hello"

    def test_raises_runtime_error_when_missing(self, monkeypatch):
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        with pytest.raises(RuntimeError, match="MY_TEST_VAR"):
            _require_env("MY_TEST_VAR")

    def test_raises_runtime_error_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "")
        with pytest.raises(RuntimeError, match="MY_TEST_VAR"):
            _require_env("MY_TEST_VAR")

    def test_error_message_mentions_env_file(self, monkeypatch):
        monkeypatch.delenv("MY_MISSING_KEY", raising=False)
        with pytest.raises(RuntimeError, match=".env"):
            _require_env("MY_MISSING_KEY")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_config(
    monkeypatch,
    tmp_path: Path,
    config_data: dict | None = None,
) -> Config:
    """Construct a Config using a temp directory for config.json."""
    cfg_path = tmp_path / "config.json"
    if config_data is not None:
        _write_config(cfg_path, config_data)

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("ADMIN_CHANNEL_ID", "100")
    monkeypatch.setenv("ADMIN_USER_ID",   "200")
    monkeypatch.setenv("GEMINI_API_KEY",  "key")
    monkeypatch.delenv("CONFIG_BUCKET", raising=False)

    with patch.object(config_module, "CONFIG_PATH", cfg_path):
        return Config()


# ---------------------------------------------------------------------------
# Startup / env var validation
# ---------------------------------------------------------------------------

class TestConfigEnvVarValidation:
    def test_missing_discord_token_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setenv("ADMIN_CHANNEL_ID", "100")
        monkeypatch.setenv("ADMIN_USER_ID",    "200")
        monkeypatch.setenv("GEMINI_API_KEY",   "key")
        monkeypatch.delenv("CONFIG_BUCKET",    raising=False)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "c.json"):
            with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
                Config()

    def test_missing_gemini_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ADMIN_CHANNEL_ID",  "100")
        monkeypatch.setenv("ADMIN_USER_ID",     "200")
        monkeypatch.delenv("GEMINI_API_KEY",    raising=False)
        monkeypatch.delenv("CONFIG_BUCKET",     raising=False)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "c.json"):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                Config()

    def test_all_required_vars_set_succeeds(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        assert cfg.bot_token == "tok"
        assert cfg.admin_channel_id == 100
        assert cfg.admin_user_id == 200
        assert cfg.gemini_api_key == "key"


# ---------------------------------------------------------------------------
# Config._load — parsing & type coercion
# ---------------------------------------------------------------------------

class TestConfigLoad:
    def test_no_config_file_uses_defaults(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        assert cfg.active_channels == []
        assert cfg.bot_enabled is True

    def test_active_channels_loaded_as_ints(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"active_channels": [333, 444]})
        assert cfg.active_channels == [333, 444]

    def test_active_channels_with_string_ids_coerced(self, monkeypatch, tmp_path):
        """String channel IDs from manual edits should be coerced to int."""
        cfg = _make_config(monkeypatch, tmp_path, {"active_channels": ["555", "666"]})
        assert cfg.active_channels == [555, 666]

    def test_invalid_channel_id_skipped_with_warning(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"active_channels": [123, "bad!id", 456]})
        assert cfg.active_channels == [123, 456]

    def test_corrupted_root_resets_to_defaults(self, monkeypatch, tmp_path):
        """A config.json that is a JSON array instead of object must not crash."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ADMIN_CHANNEL_ID",  "100")
        monkeypatch.setenv("ADMIN_USER_ID",     "200")
        monkeypatch.setenv("GEMINI_API_KEY",    "key")
        monkeypatch.delenv("CONFIG_BUCKET",     raising=False)
        with patch.object(config_module, "CONFIG_PATH", cfg_path):
            cfg = Config()
        assert cfg.active_channels == []

    def test_default_channel_id_as_string_coerced(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"default_channel_id": "777"})
        assert cfg.default_channel_id == 777

    def test_log_channel_id_as_string_coerced(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"log_channel_id": "888"})
        assert cfg.log_channel_id == 888

    def test_bot_enabled_defaults_to_true(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {})
        assert cfg.bot_enabled is True

    def test_bot_enabled_false_loaded(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"bot_enabled": False})
        assert cfg.bot_enabled is False

    def test_old_exclusion_strings_migrated(self, monkeypatch, tmp_path):
        """Old string-only exclusion format should be migrated to dicts."""
        cfg = _make_config(monkeypatch, tmp_path, {"exclusion_list": ["drugs"]})
        assert cfg.exclusion_list[0] == {"topic": "drugs", "severity": 3}

    def test_interaction_channel_id_migrated_to_channel_ids(self, monkeypatch, tmp_path):
        """Old single channel_id key should become a channel_ids list."""
        data = {"interaction_settings": {"enabled": True, "channel_id": 9001}}
        cfg = _make_config(monkeypatch, tmp_path, data)
        assert cfg.interaction_settings.get("channel_ids") == [9001]
        assert "channel_id" not in cfg.interaction_settings

    def test_owner_always_in_admin_list(self, monkeypatch, tmp_path):
        """admin_user_id must always appear in admin_user_ids even if absent from JSON."""
        cfg = _make_config(monkeypatch, tmp_path, {"admin_user_ids": []})
        assert 200 in cfg.admin_user_ids  # admin_user_id = 200


# ---------------------------------------------------------------------------
# Channel management
# ---------------------------------------------------------------------------

class TestChannelManagement:
    def test_add_channel(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.add_channel(999) is True
        assert 999 in cfg.active_channels

    def test_add_channel_idempotent(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"active_channels": [999]})
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.add_channel(999) is False

    def test_remove_channel(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path, {"active_channels": [999]})
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.remove_channel(999) is True
        assert 999 not in cfg.active_channels

    def test_remove_channel_clears_default(self, monkeypatch, tmp_path):
        cfg = _make_config(
            monkeypatch, tmp_path,
            {"active_channels": [999], "default_channel_id": 999}
        )
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            cfg.remove_channel(999)
        assert cfg.default_channel_id is None

    def test_remove_nonexistent_channel_returns_false(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.remove_channel(404) is False


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------

class TestAdminManagement:
    def test_add_admin(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.add_admin(301) is True
        assert 301 in cfg.admin_user_ids

    def test_add_admin_idempotent(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        uid = cfg.admin_user_id  # already in list
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.add_admin(uid) is False

    def test_cannot_remove_owner(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        owner = cfg.admin_user_id
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            assert cfg.remove_admin(owner) is False
        assert owner in cfg.admin_user_ids


# ---------------------------------------------------------------------------
# set_bot_enabled
# ---------------------------------------------------------------------------

class TestSetBotEnabled:
    def test_disable_and_enable(self, monkeypatch, tmp_path):
        cfg = _make_config(monkeypatch, tmp_path)
        with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
            cfg.set_bot_enabled(False)
            assert cfg.bot_enabled is False
            cfg.set_bot_enabled(True)
            assert cfg.bot_enabled is True

    def test_persisted_to_disk(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg = _make_config(monkeypatch, tmp_path)
        with patch.object(config_module, "CONFIG_PATH", cfg_path):
            cfg.set_bot_enabled(False)
        saved = json.loads(cfg_path.read_text())
        assert saved["bot_enabled"] is False
