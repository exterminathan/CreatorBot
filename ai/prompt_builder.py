from __future__ import annotations

from ai.persona import Persona


def build_messages(persona: Persona, user_instruction: str) -> list[dict[str, str]]:
    """Build a chat-completion messages list from the persona and the user's
    instruction (the topic / prompt for the message to generate)."""
    return [
        {"role": "system", "content": persona.system_prompt},
        {
            "role": "user",
            "content": (
                f"Write a Discord message as {persona.name} about the following. "
                f"Only output the message itself, nothing else.\n\n"
                f"{user_instruction}"
            ),
        },
    ]
