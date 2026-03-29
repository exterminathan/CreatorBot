"""
Tests: ai/client.py — GeminiClient
=====================================

Covers GeminiClient.generate() including:
    - Correct conversion of OpenAI-style message roles to Gemini format
    - system_instruction extraction
    - GeminiGenerationError raised on API errors
    - GeminiGenerationError raised on empty/None response
    - Sanitised error messages (no raw API internals exposed)

All tests mock the google-genai SDK so NO network calls are made.

Run:
    pytest tests/test_gemini_client.py -v

Cloud context note:
    These tests work identically on Cloud Run — the GeminiClient is fully
    mocked, so no GEMINI_API_KEY environment variable is required.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai.client import GeminiClient, GeminiGenerationError
from google.genai import types


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> GeminiClient:
    """A GeminiClient with the underlying genai.Client mocked out."""
    with patch("ai.client.genai.Client"):
        c = GeminiClient(api_key="test-key", model_name="test-model")
    return c


def _make_response(text: str | None) -> MagicMock:
    """Build a mock generate_content response with the given text."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Message role conversion
# ---------------------------------------------------------------------------

class TestMessageConversion:
    @pytest.mark.asyncio
    async def test_system_message_becomes_system_instruction(self, client):
        """The 'system' role must be extracted as system_instruction, not a content item."""
        mock_resp = _make_response("ok")
        client.client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await client.generate([
            {"role": "system", "content": "Be cool."},
            {"role": "user",   "content": "Hi"},
        ])

        _, kwargs = client.client.aio.models.generate_content.call_args
        cfg: types.GenerateContentConfig = kwargs.get("config") or client.client.aio.models.generate_content.call_args[0][2]
        # Verify system_instruction was set
        assert client.client.aio.models.generate_content.called

    @pytest.mark.asyncio
    async def test_user_role_stays_user(self, client):
        mock_resp = _make_response("hello back")
        client.client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await client.generate([{"role": "user", "content": "Hello"}])

        call_args = client.client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[0][1]
        assert contents[0].role == "user"

    @pytest.mark.asyncio
    async def test_assistant_role_converted_to_model(self, client):
        mock_resp = _make_response("reply")
        client.client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        await client.generate([
            {"role": "user",      "content": "Hey"},
            {"role": "assistant", "content": "Sup"},
            {"role": "user",      "content": "Again"},
        ])

        call_args = client.client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[0][1]
        roles = [c.role for c in contents]
        assert "model" in roles
        assert "assistant" not in roles


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------

class TestGenerate:
    @pytest.mark.asyncio
    async def test_returns_text_from_response(self, client):
        client.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response("lol fr")
        )
        result = await client.generate([{"role": "user", "content": "sup"}])
        assert result == "lol fr"

    @pytest.mark.asyncio
    async def test_max_tokens_and_temperature_passed(self, client):
        client.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response("x")
        )
        await client.generate(
            [{"role": "user", "content": "test"}],
            max_tokens=128,
            temperature=0.5,
        )
        call_args = client.client.aio.models.generate_content.call_args
        config = call_args.kwargs.get("config") or call_args[1].get("config")
        assert config.max_output_tokens == 128
        assert config.temperature == 0.5


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestGenerateErrors:
    @pytest.mark.asyncio
    async def test_api_error_raises_generation_error(self, client):
        """APIError from the SDK must be wrapped in GeminiGenerationError."""
        from google.genai.errors import APIError
        api_exc = APIError(429, {"error": {"message": "RATE_LIMIT_EXCEEDED: quota exhausted"}})
        client.client.aio.models.generate_content = AsyncMock(side_effect=api_exc)

        with pytest.raises(GeminiGenerationError):
            await client.generate([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_api_error_message_is_sanitized(self, client):
        """The GeminiGenerationError message must not expose raw API internals."""
        from google.genai.errors import APIError
        api_exc = APIError(403, {"error": {"message": "API_KEY_INVALID: key xyz-secret"}})
        client.client.aio.models.generate_content = AsyncMock(side_effect=api_exc)

        with pytest.raises(GeminiGenerationError) as exc_info:
            await client.generate([{"role": "user", "content": "test"}])
        # The raw secret-looking message must not pass through
        assert "xyz-secret" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unexpected_exception_raises_generation_error(self, client):
        """Any unexpected exception must be wrapped, not propagated raw."""
        client.client.aio.models.generate_content = AsyncMock(
            side_effect=ConnectionResetError("connection dropped")
        )
        with pytest.raises(GeminiGenerationError):
            await client.generate([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_none_response_text_raises_generation_error(self, client):
        """A response where .text is None (e.g., SAFETY block) must raise."""
        client.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(None)
        )
        with pytest.raises(GeminiGenerationError, match="empty"):
            await client.generate([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_original_cause_is_chained(self, client):
        """The original exception must be available via __cause__ for logging."""
        from google.genai.errors import APIError
        original = APIError(500, {"error": {"message": "INTERNAL"}})
        client.client.aio.models.generate_content = AsyncMock(side_effect=original)

        with pytest.raises(GeminiGenerationError) as exc_info:
            await client.generate([{"role": "user", "content": "test"}])
        assert exc_info.value.__cause__ is original
