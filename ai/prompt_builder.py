from __future__ import annotations

from ai.persona import Persona


def _build_exclusion_instructions(exclusion_list: list[dict] | None) -> str:
    """Build system prompt instructions from severity-rated exclusions.

    Severity 1: allowed through (no instruction generated)
    Severity 2: restrict direct discussion, allow tangential jokes/statements
    Severity 3: complete block of all discussion
    """
    if not exclusion_list:
        return ""
    sev2 = [e["topic"] for e in exclusion_list if e.get("severity") == 2]
    sev3 = [e["topic"] for e in exclusion_list if e.get("severity", 3) == 3]
    parts: list[str] = []
    if sev3:
        parts.append(
            "NEVER mention, discuss, or reference any of the following "
            "topics/words under any circumstances: " + ", ".join(sev3) + "."
        )
    if sev2:
        parts.append(
            "Avoid directly discussing the following topics, but brief "
            "tangential jokes or statements that are NOT themselves focused "
            "on these topics are OK: " + ", ".join(sev2) + "."
        )
    return "\n\n".join(parts)


def build_post_messages(
    persona: Persona,
    user_instruction: str,
    system_prompt: str = "",
    exclusion_list: list[dict] | None = None,
    template: str = "",
) -> list[dict[str, str]]:
    """Build messages for an admin-initiated post (the original pipeline)."""
    system = persona.render_system_prompt(template)
    # Append user-provided additional constraints/behavior
    if system_prompt:
        system += "\n\n" + system_prompt
    exclusion_text = _build_exclusion_instructions(exclusion_list)
    if exclusion_text:
        system += "\n\n" + exclusion_text
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
    exclusion_list: list[dict] | None = None,
    template: str = "",
) -> list[dict[str, str]]:
    """Build messages for a user-initiated @Cy interaction reply."""
    system = persona.render_system_prompt(template)
    system += (
        "\n\nYou are replying to a Discord user who tagged you. "
        "Keep your reply natural and conversational \u2014 match their energy. "
        "Do NOT repeat their message back. Just respond like you would in a real chat."
    )
    # Append user-provided additional constraints/behavior
    if system_prompt:
        system += "\n\n" + system_prompt
    exclusion_text = _build_exclusion_instructions(exclusion_list)
    if exclusion_text:
        system += "\n\n" + exclusion_text
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
