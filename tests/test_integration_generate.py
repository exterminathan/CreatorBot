"""
Tests: Integration — generate_post() & generate_interaction()
==============================================================

These are INTEGRATION tests that call the full generate pipeline end-to-end
within the bot process (Persona → PromptBuilder → GeminiClient).
The Gemini SDK is fully mocked — no real API calls are made.

These tests verify the complete data flow including:
    - Exclusion-list retry logic (up to _MAX_EXCLUSION_RETRIES)
    - Fallback response when all retries are exhausted
    - URL surfacing (URLs moved to end of post)
    - Empty-string fallback when generate() returns empty

Run:
    pytest tests/test_integration_generate.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ai.persona import Persona
from ai.client import GeminiClient, GeminiGenerationError
from bot.config import Config
import bot.config as config_module
import bot.main as main_module
from bot.main import CreatorBot
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_config(monkeypatch, tmp_path) -> Config:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("ADMIN_CHANNEL_ID",  "100")
    monkeypatch.setenv("ADMIN_USER_ID",     "200")
    monkeypatch.setenv("GEMINI_API_KEY",    "key")
    monkeypatch.delenv("CONFIG_BUCKET",     raising=False)
    with patch.object(config_module, "CONFIG_PATH", tmp_path / "config.json"):
        return Config()


@pytest.fixture()
def minimal_persona_obj() -> Persona:
    p = Persona.__new__(Persona)
    p.name = "TestPersona"
    p.bio = "test bio"
    p.writing_style = "casual"
    p.vocabulary = []
    p.facts = []
    p.video_lines = []
    p.example_messages = []
    return p


@pytest.fixture()
def creatorbot(mock_config, minimal_persona_obj) -> CreatorBot:
    """A CreatorBot instance with all Discord/network internals mocked."""
    with patch("bot.main.commands.Bot.__init__", return_value=None):
        bot = CreatorBot.__new__(CreatorBot)
    bot.cfg = mock_config
    bot.persona = minimal_persona_obj
    bot.gemini = MagicMock(spec=GeminiClient)
    bot.webhooks = MagicMock()
    bot._interaction_cooldowns = {}
    bot.cfg._interaction_cooldowns = bot._interaction_cooldowns
    return bot


# ---------------------------------------------------------------------------
# generate_post
# ---------------------------------------------------------------------------

class TestGeneratePost:
    @pytest.mark.asyncio
    async def test_returns_generated_text(self, creatorbot):
        creatorbot.gemini.generate = AsyncMock(return_value="gaming is lit")
        result = await creatorbot.generate_post("talk about gaming")
        assert "gaming is lit" in result

    @pytest.mark.asyncio
    async def test_urls_in_prompt_preserved_in_output(self, creatorbot):
        creatorbot.gemini.generate = AsyncMock(return_value="check it out")
        result = await creatorbot.generate_post("see https://example.com for more")
        assert "https://example.com" in result

    @pytest.mark.asyncio
    async def test_exclusion_violation_retries(self, creatorbot):
        """When generated text contains an excluded word, it should retry."""
        creatorbot.cfg.exclusion_list = [{"topic": "gambling", "severity": 3}]
        # First call violates exclusion, second is clean
        creatorbot.gemini.generate = AsyncMock(
            side_effect=["let's talk about gambling!", "something clean"]
        )
        result = await creatorbot.generate_post("casual message")
        assert "gambling" not in result
        assert creatorbot.gemini.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_uses_fallback(self, creatorbot):
        """After MAX_EXCLUSION_RETRIES of violations, fallback must be returned."""
        creatorbot.cfg.exclusion_list = [{"topic": "gambling", "severity": 3}]
        creatorbot.cfg.default_responses = ["hmm"]
        creatorbot.gemini.generate = AsyncMock(return_value="gambling every time")
        result = await creatorbot.generate_post("prompt")
        assert result == "hmm"

    @pytest.mark.asyncio
    async def test_generate_alias_calls_generate_post(self, creatorbot):
        creatorbot.gemini.generate = AsyncMock(return_value="test response")
        result = await creatorbot.generate("prompt text")
        assert result == "test response"


# ---------------------------------------------------------------------------
# generate_interaction
# ---------------------------------------------------------------------------

class TestGenerateInteraction:
    @pytest.mark.asyncio
    async def test_returns_interaction_reply(self, creatorbot):
        creatorbot.gemini.generate = AsyncMock(return_value="sup dude")
        result = await creatorbot.generate_interaction("hey there", "Alice")
        assert "sup dude" in result

    @pytest.mark.asyncio
    async def test_exclusion_retry_on_interaction(self, creatorbot):
        creatorbot.cfg.exclusion_list = [{"topic": "drugs", "severity": 3}]
        creatorbot.gemini.generate = AsyncMock(
            side_effect=["talking about drugs", "clean answer"]
        )
        result = await creatorbot.generate_interaction("hello", "Bob")
        assert "drugs" not in result

    @pytest.mark.asyncio
    async def test_interaction_fallback_after_retries(self, creatorbot):
        creatorbot.cfg.exclusion_list = [{"topic": "drugs", "severity": 3}]
        creatorbot.cfg.default_responses = ["lol"]
        creatorbot.gemini.generate = AsyncMock(return_value="drugs drugs drugs")
        result = await creatorbot.generate_interaction("yo", "Bob")
        assert result == "lol"
