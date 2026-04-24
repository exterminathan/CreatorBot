"""
Tests: bot/webhook_manager.py — WebhookManager
===============================================

Covers:
    - get_or_create() cache hit
    - get_or_create() fetches existing webhook from Discord
    - get_or_create() creates a new webhook when none exists
    - get_or_create() race-condition fallback (create fails → re-fetch)
    - send_as_persona() delegates to webhook.send() with correct username/avatar
    - cleanup() deletes cached webhook

No real Discord connection is made — all discord objects are mocked.

Run:
    pytest tests/test_webhook_manager.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.webhook_manager import WebhookManager


TEST_WEBHOOK_NAME = "TestBot-Hook"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_config() -> MagicMock:
    cfg = MagicMock()
    cfg.bot_display_name = "TestBot"
    cfg.bot_avatar_url = "https://example.com/avatar.png"
    cfg.webhook_name = TEST_WEBHOOK_NAME
    return cfg


@pytest.fixture()
def manager(mock_config) -> WebhookManager:
    return WebhookManager(mock_config)


def _make_channel(channel_id: int = 1, existing_webhooks: list | None = None) -> MagicMock:
    """Create a mock TextChannel."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = f"channel-{channel_id}"

    existing = existing_webhooks or []
    channel.webhooks = AsyncMock(return_value=existing)

    new_wh = MagicMock(spec=discord.Webhook)
    new_wh.name = TEST_WEBHOOK_NAME
    channel.create_webhook = AsyncMock(return_value=new_wh)
    return channel


def _make_webhook(name: str = TEST_WEBHOOK_NAME, wh_id: int = 9001) -> MagicMock:
    wh = MagicMock(spec=discord.Webhook)
    wh.name = name
    wh.id = wh_id
    wh.send = AsyncMock(return_value=MagicMock(spec=discord.WebhookMessage))
    wh.delete = AsyncMock()
    return wh


# ---------------------------------------------------------------------------
# get_or_create — cache
# ---------------------------------------------------------------------------

class TestGetOrCreateCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_discord_api(self, manager):
        cached_wh = _make_webhook()
        channel = _make_channel(channel_id=1)
        manager._cache[1] = cached_wh

        result = await manager.get_or_create(channel)

        assert result is cached_wh
        channel.webhooks.assert_not_called()
        channel.create_webhook.assert_not_called()


# ---------------------------------------------------------------------------
# get_or_create — existing webhook discovery
# ---------------------------------------------------------------------------

class TestGetOrCreateExisting:
    @pytest.mark.asyncio
    async def test_finds_existing_webhook_by_name(self, manager):
        existing_wh = _make_webhook(name=TEST_WEBHOOK_NAME)
        channel = _make_channel(channel_id=2, existing_webhooks=[existing_wh])

        result = await manager.get_or_create(channel)

        assert result is existing_wh
        channel.create_webhook.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_webhooks_with_different_name(self, manager):
        other_wh = _make_webhook(name="SomeOtherHook")
        channel = _make_channel(channel_id=3, existing_webhooks=[other_wh])

        result = await manager.get_or_create(channel)

        # Should have created a new one since the existing name doesn't match
        channel.create_webhook.assert_called_once_with(name=TEST_WEBHOOK_NAME)

    @pytest.mark.asyncio
    async def test_stores_found_webhook_in_cache(self, manager):
        existing_wh = _make_webhook(name=TEST_WEBHOOK_NAME)
        channel = _make_channel(channel_id=4, existing_webhooks=[existing_wh])

        await manager.get_or_create(channel)

        assert manager._cache[4] is existing_wh


# ---------------------------------------------------------------------------
# get_or_create — creation
# ---------------------------------------------------------------------------

class TestGetOrCreateNew:
    @pytest.mark.asyncio
    async def test_creates_new_webhook_when_none_exist(self, manager):
        channel = _make_channel(channel_id=5, existing_webhooks=[])
        new_wh = channel.create_webhook.return_value

        result = await manager.get_or_create(channel)

        channel.create_webhook.assert_called_once_with(name=TEST_WEBHOOK_NAME)
        assert result is new_wh

    @pytest.mark.asyncio
    async def test_new_webhook_stored_in_cache(self, manager):
        channel = _make_channel(channel_id=6, existing_webhooks=[])

        await manager.get_or_create(channel)

        assert 6 in manager._cache


# ---------------------------------------------------------------------------
# get_or_create — race condition
# ---------------------------------------------------------------------------

class TestGetOrCreateRaceCondition:
    @pytest.mark.asyncio
    async def test_http_error_on_create_falls_back_to_refetch(self, manager):
        """If create_webhook raises HTTPException, manager should re-fetch webhooks
        and return the one that was already created by a concurrent call."""
        race_wh = _make_webhook(name=TEST_WEBHOOK_NAME, wh_id=7777)
        channel = _make_channel(channel_id=7, existing_webhooks=[])

        # First webhooks() call returns empty (no hook yet)
        # After create fails, second call returns the race-created hook
        channel.webhooks = AsyncMock(side_effect=[[], [race_wh]])
        channel.create_webhook = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "already exists")
        )

        result = await manager.get_or_create(channel)

        assert result is race_wh

    @pytest.mark.asyncio
    async def test_http_error_re_raises_if_no_fallback(self, manager):
        """If create_webhook fails and re-fetch also returns nothing, re-raise."""
        channel = _make_channel(channel_id=8, existing_webhooks=[])
        channel.webhooks = AsyncMock(side_effect=[[], []])  # both calls empty
        channel.create_webhook = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "Missing Permissions")
        )

        with pytest.raises(discord.HTTPException):
            await manager.get_or_create(channel)


# ---------------------------------------------------------------------------
# send_as_persona
# ---------------------------------------------------------------------------

class TestSendAsPersona:
    @pytest.mark.asyncio
    async def test_sends_with_correct_username(self, manager, mock_config):
        wh = _make_webhook()
        channel = _make_channel(channel_id=10, existing_webhooks=[wh])

        await manager.send_as_persona(channel, "hello world")

        wh.send.assert_called_once()
        _, kwargs = wh.send.call_args
        assert kwargs["username"] == mock_config.bot_display_name

    @pytest.mark.asyncio
    async def test_sends_with_avatar_url(self, manager, mock_config):
        wh = _make_webhook()
        channel = _make_channel(channel_id=11, existing_webhooks=[wh])

        await manager.send_as_persona(channel, "test")

        _, kwargs = wh.send.call_args
        assert kwargs["avatar_url"] == mock_config.bot_avatar_url

    @pytest.mark.asyncio
    async def test_sends_message_content(self, manager):
        wh = _make_webhook()
        channel = _make_channel(channel_id=12, existing_webhooks=[wh])

        await manager.send_as_persona(channel, "my message content")

        _, kwargs = wh.send.call_args
        assert kwargs["content"] == "my message content"

    @pytest.mark.asyncio
    async def test_send_uses_wait_true(self, manager):
        """wait=True is required so we get the WebhookMessage back (for jump_url)."""
        wh = _make_webhook()
        channel = _make_channel(channel_id=13, existing_webhooks=[wh])

        await manager.send_as_persona(channel, "text")

        _, kwargs = wh.send.call_args
        assert kwargs.get("wait") is True


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_cached_webhook(self, manager):
        wh = _make_webhook()
        channel = _make_channel(channel_id=20)
        manager._cache[20] = wh

        await manager.cleanup(channel)

        wh.delete.assert_called_once()
        assert 20 not in manager._cache

    @pytest.mark.asyncio
    async def test_cleanup_finds_and_deletes_uncached_webhook(self, manager):
        wh = _make_webhook(name=TEST_WEBHOOK_NAME)
        channel = _make_channel(channel_id=21, existing_webhooks=[wh])

        await manager.cleanup(channel)

        wh.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_tolerates_already_deleted_webhook(self, manager):
        """If the webhook is already gone on Discord, NotFound must not crash."""
        wh = _make_webhook()
        wh.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(), "unknown"))
        channel = _make_channel(channel_id=22)
        manager._cache[22] = wh

        await manager.cleanup(channel)  # should not raise
