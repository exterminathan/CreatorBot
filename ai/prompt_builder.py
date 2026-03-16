from __future__ import annotations

from ai.persona import Persona


def build_post_messages(
    persona: Persona,
    user_instruction: str,
    system_prompt: str = "",
    exclusion_list: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build messages for an admin-initiated post (the original pipeline)."""
    # Always start with persona base (example messages and persona are locked in)
    system = persona.system_prompt
    # Append user-provided additional constraints/behavior
    if system_prompt:
        system += "\n\n" + system_prompt
    if exclusion_list:
        system += (
            "\n\nNEVER mention, discuss, or reference any of the following "
            "topics/words: " + ", ".join(exclusion_list) + ". Avoid these completely."
        )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Write a Discord message as {persona.name} about the following. "
                f"Only output the message itself, nothing else.\n\n"
                f"{user_instruction}"
            ),
        },
    ]


def build_interaction_messages(
    persona: Persona,
    user_message: str,
    user_name: str,
    system_prompt: str = "",
    exclusion_list: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build messages for a user-initiated @Cy interaction reply."""
    # Always start with persona base + interaction-specific behavior (locked in)
    system = persona.system_prompt
    system += (
        "\n\nYou are replying to a Discord user who tagged you. "
        "Keep your reply natural and conversational \u2014 match their energy. "
        "Do NOT repeat their message back. Just respond like you would in a real chat."
    )
    # Append user-provided additional constraints/behavior
    if system_prompt:
        system += "\n\n" + system_prompt
    if exclusion_list:
        system += (
            "\n\nNEVER mention, discuss, or reference any of the following "
            "topics/words: " + ", ".join(exclusion_list) + ". Avoid these completely."
        )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"{user_name} says: {user_message}\n\n"
                f"Reply as {persona.name}. Only output the reply, nothing else."
            ),
        },
    ]


# Backward compat alias
def build_messages(persona: Persona, user_instruction: str) -> list[dict[str, str]]:
    return build_post_messages(persona, user_instruction)
