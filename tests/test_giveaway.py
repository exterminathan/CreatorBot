"""Tests for the giveaway system: GiveawayManager, parse_duration, Config giveaway methods."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.giveaway_manager import (
    GiveawayManager,
    parse_duration,
    _fmt_duration,
    _build_embed,
    GIVEAWAY_EMOJI,
)


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("30s") == 30

    def test_minutes(self):
        assert parse_duration("5m") == 300

    def test_hours(self):
        assert parse_duration("2h") == 7200

    def test_days(self):
        assert parse_duration("1d") == 86400

    def test_case_insensitive(self):
        assert parse_duration("10M") == 600

    def test_invalid_str(self):
        assert parse_duration("abc") is None

    def test_zero_rejected(self):
        assert parse_duration("0s") is None

    def test_over_30_days_rejected(self):
        assert parse_duration("31d") is None

    def test_30_days_ok(self):
        assert parse_duration("30d") == 30 * 86400

    def test_with_whitespace(self):
        assert parse_duration("  10m  ") == 600


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(45) == "45s"

    def test_minutes(self):
        result = _fmt_duration(90)
        assert "m" in result

    def test_hours(self):
        result = _fmt_duration(3661)
        assert "h" in result

    def test_days(self):
        result = _fmt_duration(86401)
        assert "d" in result

    def test_zero(self):
        assert _fmt_duration(0) == "0s"

    def test_negative_clamps_to_zero(self):
        assert _fmt_duration(-100) == "0s"


# ---------------------------------------------------------------------------
# Config giveaway persistence helpers
# ---------------------------------------------------------------------------


class TestConfigGiveawayMethods:
    @pytest.fixture()
    def cfg(self, minimal_config_env, tmp_path, monkeypatch):
        monkeypatch.setenv("CONFIG_BUCKET", "")
        # Redirect CONFIG_PATH to a temp dir so tests don't touch real disk
        from bot import config as cfg_module
        monkeypatch.setattr(cfg_module, "CONFIG_PATH", tmp_path / "config.json")
        from bot.config import Config
        return Config()

    def test_add_and_get_giveaway(self, cfg):
        g = {
            "message_id": "123",
            "channel_id": 1,
            "guild_id": 2,
            "prize": "Test Prize",
            "winner_count": 1,
            "end_time": time.time() + 60,
            "host_id": 99,
            "entries": [],
            "ended": False,
            "winners": [],
        }
        cfg.add_giveaway(g)
        assert cfg.get_giveaway("123") is not None
        assert cfg.get_giveaway("123")["prize"] == "Test Prize"

    def test_get_nonexistent_returns_none(self, cfg):
        assert cfg.get_giveaway("999") is None

    def test_update_giveaway(self, cfg):
        g = {
            "message_id": "456",
            "channel_id": 1,
            "guild_id": 2,
            "prize": "Prize",
            "winner_count": 1,
            "end_time": time.time() + 60,
            "host_id": 99,
            "entries": [],
            "ended": False,
            "winners": [],
        }
        cfg.add_giveaway(g)
        cfg.update_giveaway("456", {"entries": [100, 200]})
        assert cfg.get_giveaway("456")["entries"] == [100, 200]

    def test_remove_giveaway(self, cfg):
        g = {
            "message_id": "789",
            "channel_id": 1,
            "guild_id": 2,
            "prize": "Prize",
            "winner_count": 1,
            "end_time": time.time() + 60,
            "host_id": 99,
            "entries": [],
            "ended": False,
            "winners": [],
        }
        cfg.add_giveaway(g)
        result = cfg.remove_giveaway("789")
        assert result is True
        assert cfg.get_giveaway("789") is None

    def test_remove_nonexistent_returns_false(self, cfg):
        assert cfg.remove_giveaway("does_not_exist") is False

    def test_giveaway_settings_defaults(self, cfg):
        assert cfg.giveaway_settings.get("default_channel_id") is None
        assert isinstance(cfg.giveaway_settings.get("manager_role_ids"), list)

    def test_giveaways_persisted_on_save(self, cfg, tmp_path):
        import json as json_mod
        g = {
            "message_id": "save_test",
            "channel_id": 1,
            "guild_id": 2,
            "prize": "SavedPrize",
            "winner_count": 2,
            "end_time": time.time() + 60,
            "host_id": 99,
            "entries": [1, 2, 3],
            "ended": False,
            "winners": [],
        }
        cfg.add_giveaway(g)
        from bot.config import CONFIG_PATH
        # Re-read from disk
        data = json_mod.loads(CONFIG_PATH.read_text())
        assert any(x["message_id"] == "save_test" for x in data.get("giveaways", []))


# ---------------------------------------------------------------------------
# _build_embed
# ---------------------------------------------------------------------------


class TestBuildEmbed:
    def _make_giveaway(self, **kwargs):
        base = {
            "message_id": "111",
            "channel_id": 1,
            "guild_id": 2,
            "prize": "Steam Key",
            "winner_count": 2,
            "end_time": time.time() + 300,
            "host_id": 99,
            "entries": [1, 2, 3],
            "ended": False,
            "winners": [],
        }
        base.update(kwargs)
        return base

    def test_active_embed_has_prize_in_title(self):
        import discord
        g = self._make_giveaway()
        embed = _build_embed(g)
        assert "Steam Key" in embed.title
        assert GIVEAWAY_EMOJI in embed.title

    def test_ended_embed_title(self):
        g = self._make_giveaway(ended=True, winners=[42])
        embed = _build_embed(g)
        assert "Ended" in embed.title

    def test_active_embed_shows_entry_count(self):
        g = self._make_giveaway(entries=[1, 2, 3])
        embed = _build_embed(g)
        field_names = [f.name for f in embed.fields]
        assert "Entries" in field_names

    def test_ended_no_entries_shows_no_winner(self):
        g = self._make_giveaway(ended=True, entries=[], winners=[])
        embed = _build_embed(g)
        assert "no valid entries" in embed.description.lower() or "No valid" in embed.description


# ---------------------------------------------------------------------------
# GiveawayManager
# ---------------------------------------------------------------------------


def _make_giveaway_dict(message_id="msg1", ended=False, entries=None, winners=None):
    return {
        "message_id": message_id,
        "channel_id": 10,
        "guild_id": 20,
        "prize": "Cool Prize",
        "winner_count": 1,
        "end_time": time.time() + 600,
        "host_id": 99,
        "entries": entries if entries is not None else [],
        "ended": ended,
        "winners": winners if winners is not None else [],
    }


def _make_bot_and_cfg():
    """Return (mock_bot, mock_cfg) suitable for GiveawayManager."""
    cfg = MagicMock()
    cfg.giveaways = []
    cfg.get_giveaway = MagicMock(return_value=None)
    cfg.update_giveaway = MagicMock()
    cfg.add_giveaway = MagicMock()
    cfg.remove_giveaway = MagicMock(return_value=True)

    bot = MagicMock()
    bot.loop = asyncio.get_event_loop()
    bot.get_channel = MagicMock(return_value=None)
    return bot, cfg


class TestGiveawayManagerAddEntry:
    @pytest.mark.asyncio
    async def test_add_entry_to_active(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict()
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.update_giveaway = MagicMock()

        mgr = GiveawayManager(bot, cfg)
        mgr._refresh_message = AsyncMock()

        result = await mgr.add_entry("msg1", 42)
        assert result == "added"
        assert 42 in g["entries"]

    @pytest.mark.asyncio
    async def test_remove_entry_when_already_in(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(entries=[42])
        cfg.get_giveaway = MagicMock(return_value=g)

        mgr = GiveawayManager(bot, cfg)
        mgr._refresh_message = AsyncMock()

        result = await mgr.add_entry("msg1", 42)
        assert result == "removed"
        assert 42 not in g["entries"]

    @pytest.mark.asyncio
    async def test_entry_to_ended_returns_ended(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(ended=True)
        cfg.get_giveaway = MagicMock(return_value=g)

        mgr = GiveawayManager(bot, cfg)
        result = await mgr.add_entry("msg1", 42)
        assert result == "ended"

    @pytest.mark.asyncio
    async def test_entry_to_nonexistent_returns_ended(self):
        bot, cfg = _make_bot_and_cfg()
        cfg.get_giveaway = MagicMock(return_value=None)

        mgr = GiveawayManager(bot, cfg)
        result = await mgr.add_entry("missing", 42)
        assert result == "ended"


class TestGiveawayManagerEnd:
    @pytest.mark.asyncio
    async def test_end_picks_winner(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(entries=[1, 2, 3])
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.update_giveaway = MagicMock(side_effect=lambda mid, upd: g.update(upd))

        mgr = GiveawayManager(bot, cfg)
        mgr._post_end_result = AsyncMock()

        result = await mgr.end("msg1")
        assert result is not None
        assert result["ended"] is True
        assert len(result["winners"]) == 1
        assert result["winners"][0] in [1, 2, 3]

    @pytest.mark.asyncio
    async def test_end_no_entries_no_winner(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(entries=[])
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.update_giveaway = MagicMock(side_effect=lambda mid, upd: g.update(upd))

        mgr = GiveawayManager(bot, cfg)
        mgr._post_end_result = AsyncMock()

        result = await mgr.end("msg1")
        assert result["winners"] == []

    @pytest.mark.asyncio
    async def test_end_nonexistent_returns_none(self):
        bot, cfg = _make_bot_and_cfg()
        cfg.get_giveaway = MagicMock(return_value=None)

        mgr = GiveawayManager(bot, cfg)
        result = await mgr.end("ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_end_winner_count_respected(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(entries=[1, 2, 3, 4, 5])
        g["winner_count"] = 3
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.update_giveaway = MagicMock(side_effect=lambda mid, upd: g.update(upd))

        mgr = GiveawayManager(bot, cfg)
        mgr._post_end_result = AsyncMock()

        result = await mgr.end("msg1")
        assert len(result["winners"]) == 3

    @pytest.mark.asyncio
    async def test_end_deduplicates_entries(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict(entries=[1, 1, 1, 2, 2])
        g["winner_count"] = 2
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.update_giveaway = MagicMock(side_effect=lambda mid, upd: g.update(upd))

        mgr = GiveawayManager(bot, cfg)
        mgr._post_end_result = AsyncMock()

        result = await mgr.end("msg1")
        # Should not have more winners than unique entrants
        assert len(result["winners"]) <= 2
        assert len(set(result["winners"])) == len(result["winners"])


class TestGiveawayManagerDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self):
        bot, cfg = _make_bot_and_cfg()
        g = _make_giveaway_dict()
        cfg.get_giveaway = MagicMock(return_value=g)
        cfg.remove_giveaway = MagicMock(return_value=True)
        bot.get_channel = MagicMock(return_value=None)  # no channel = skip discord msg delete

        mgr = GiveawayManager(bot, cfg)
        result = await mgr.delete("msg1")
        assert result is True
        cfg.remove_giveaway.assert_called_once_with("msg1")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        bot, cfg = _make_bot_and_cfg()
        cfg.get_giveaway = MagicMock(return_value=None)

        mgr = GiveawayManager(bot, cfg)
        result = await mgr.delete("ghost")
        assert result is False


class TestGiveawayManagerGetActive:
    def test_get_active_filters_ended(self):
        bot, cfg = _make_bot_and_cfg()
        cfg.giveaways = [
            _make_giveaway_dict("active1", ended=False),
            _make_giveaway_dict("ended1", ended=True),
            _make_giveaway_dict("active2", ended=False),
        ]

        mgr = GiveawayManager(bot, cfg)
        active = mgr.get_active()
        assert len(active) == 2
        assert all(not g["ended"] for g in active)

    def test_get_all_returns_all(self):
        bot, cfg = _make_bot_and_cfg()
        cfg.giveaways = [
            _make_giveaway_dict("a", ended=False),
            _make_giveaway_dict("b", ended=True),
        ]

        mgr = GiveawayManager(bot, cfg)
        assert len(mgr.get_all()) == 2
