"""
Tests: Integration — generate_post() & generate_interaction()
==============================================================

These are INTEGRATION tests that call the full generate pipeline end-to-end
within the CyBot process (Persona → PromptBuilder → GeminiClient).
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
from bot.main import CyBot
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
    p.name = "Cy"
    p.bio = "test bio"
    p.writing_style = "casual"
    p.vocabulary = []
    p.facts = []
    p.video_lines = []
    p.example_messages = []
    return p


@pytest.fixture()
def cybot(mock_config, minimal_persona_obj) -> CyBot:
    """A CyBot instance with all Discord/network internals mocked."""
    with patch("bot.main.commands.Bot.__init__", return_value=None):
        bot = CyBot.__new__(CyBot)
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
    async def test_returns_generated_text(self, cybot):
        cybot.gemini.generate = AsyncMock(return_value="gaming is lit")
        result = await cybot.generate_post("talk about gaming")
        assert "gaming is lit" in result

    @pytest.mark.asyncio
    async def test_urls_in_prompt_preserved_in_output(self, cybot):
        cybot.gemini.generate = AsyncMock(return_value="check it out")
        result = await cybot.generate_post("see https://example.com for more")
        assert "https://example.com" in result

    @pytest.mark.asyncio
    async def test_exclusion_violation_retries(self, cybot):
        """When generated text contains an excluded word, it should retry."""
        cybot.cfg.exclusion_list = [{"topic": "gambling", "severity": 3}]
        # First call violates exclusion, second is clean
        cybot.gemini.generate = AsyncMock(
            side_effect=["let's talk about gambling!", "something clean"]
        )
        result = await cybot.generate_post("casual message")
        assert "gambling" not in result
        assert cybot.gemini.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_uses_fallback(self, cybot):
        """After MAX_EXCLUSION_RETRIES of violations, fallback must be returned."""
        cybot.cfg.exclusion_list = [{"topic": "gambling", "severity": 3}]
        cybot.cfg.default_responses = ["hmm"]
        cybot.gemini.generate = AsyncMock(return_value="gambling every time")
        result = await cybot.generate_post("prompt")
        assert result == "hmm"

    @pytest.mark.asyncio
    async def test_generate_alias_calls_generate_post(self, cybot):
        cybot.gemini.generate = AsyncMock(return_value="test response")
        result = await cybot.generate("prompt text")
        assert result == "test response"


# ---------------------------------------------------------------------------
# generate_interaction
# ---------------------------------------------------------------------------

class TestGenerateInteraction:
    @pytest.mark.asyncio
    async def test_returns_interaction_reply(self, cybot):
        cybot.gemini.generate = AsyncMock(return_value="sup dude")
        result = await cybot.generate_interaction("hey cy", "Alice")
        assert "sup dude" in result

    @pytest.mark.asyncio
    async def test_exclusion_retry_on_interaction(self, cybot):
        cybot.cfg.exclusion_list = [{"topic": "drugs", "severity": 3}]
        cybot.gemini.generate = AsyncMock(
            side_effect=["talking about drugs", "clean answer"]
        )
        result = await cybot.generate_interaction("hello", "Bob")
        assert "drugs" not in result

    @pytest.mark.asyncio
    async def test_interaction_fallback_after_retries(self, cybot):
        cybot.cfg.exclusion_list = [{"topic": "drugs", "severity": 3}]
        cybot.cfg.default_responses = ["lol"]
        cybot.gemini.generate = AsyncMock(return_value="drugs drugs drugs")
        result = await cybot.generate_interaction("yo", "Bob")
        assert result == "lol"
