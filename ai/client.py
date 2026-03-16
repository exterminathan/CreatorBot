from __future__ import annotations

import logging

import vertexai
from vertexai.generative_models import Content, GenerativeModel, Part

log = logging.getLogger(__name__)


class GeminiClient:
    """Async client for Vertex AI Gemini inference."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        model_name: str = "gemini-2.0-flash-001",
    ):
        vertexai.init(project=project_id, location=location)
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
        """
        system_prompt = None
        contents: list[Content] = []

        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_prompt = text
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append(
                    Content(role=gemini_role, parts=[Part.from_text(text)])
                )

        model = GenerativeModel(
            model_name=self.model_name,
            system_instruction=[system_prompt] if system_prompt else None,
        )

        response = await model.generate_content_async(
            contents,
            generation_config={
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        return response.text

    async def close(self):
        """No persistent session to close for Vertex AI."""
