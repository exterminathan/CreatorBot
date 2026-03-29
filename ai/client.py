from __future__ import annotations

import logging

from google import genai
from google.genai import types
from google.genai.errors import APIError

log = logging.getLogger(__name__)


class GeminiGenerationError(Exception):
    """Raised when the Gemini API returns an unrecoverable error.

    Wraps the underlying SDK exception so callers receive a stable type
    without leaking raw SDK internals or sensitive API details.
    """


class GeminiClient:
    """Async client for Google AI Gemini inference."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash-lite",
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    async def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.8,
    ) -> str:
        """Accept chat-completion-style messages and return generated text.

        The ``messages`` list may contain ``system``, ``user``, and
        ``assistant`` roles.  The system message is extracted and passed as
        Gemini's ``system_instruction``; the rest become ``contents``.

        Raises:
            GeminiGenerationError: on API errors (rate limits, auth, service
                errors).  The original cause is chained via ``__cause__`` for
                logging but the message is sanitized for user display.
        """
        system_prompt = None
        contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_prompt = text
            else:
                genai_role = "model" if role == "assistant" else "user"
                contents.append(
                    types.Content(role=genai_role, parts=[types.Part.from_text(text=text)])
                )

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
        except APIError as exc:
            log.error("Gemini API error: status=%s message=%s", exc.code, exc.message)
            raise GeminiGenerationError(
                f"AI service error (code {exc.code}). Try again in a moment."
            ) from exc
        except Exception as exc:
            log.exception("Unexpected error calling Gemini API")
            raise GeminiGenerationError(
                "Unexpected error contacting the AI service."
            ) from exc

        if response.text is None:
            log.warning("Gemini returned empty response (finish_reason may be SAFETY or MAX_TOKENS)")
            raise GeminiGenerationError("AI returned an empty response.")

        return response.text

    async def close(self):
        """No persistent session to close."""
