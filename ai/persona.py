from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Filename of the committed persona template. User's private override lives in
# `persona.local.json` (gitignored) and takes priority when present.
DEFAULT_PERSONA_FILENAME = "persona.json"
LOCAL_PERSONA_FILENAME = "persona.local.json"

DEFAULT_TEMPLATE = (
    "You are {name}. You must write EXACTLY like {name} — "
    "same tone, same vocabulary, same vibe. Never break character. "
    "Never say you are an AI. You ARE {name}.\n\n"
    "{bio}\n\n"
    "{facts}\n\n"
    "{writing_style}\n\n"
    "{vocabulary}\n\n"
    "{example_messages}\n\n"
    "{video_lines}\n\n"
    "IMPORTANT: Keep responses casual, natural, and varied. "
    "Match the length and energy of the prompt. "
    "Do NOT use hashtags or emojis unless {name} typically does. "
    "Write as a Discord message, not an essay. "
    "Do NOT just string slang words together — form real thoughts and opinions."
)


def _resolve_persona_path(filename: str = DEFAULT_PERSONA_FILENAME) -> Path:
    """Return the path to the persona file, preferring local override if present."""
    local = DATA_DIR / LOCAL_PERSONA_FILENAME
    if local.exists():
        return local
    return DATA_DIR / filename


class Persona:
    """Loads persona definition and builds a system prompt string for the LLM."""

    def __init__(self, filename: str = DEFAULT_PERSONA_FILENAME):
        self._filename = filename
        self.name: str = "YourBot"
        self.bio: str = ""
        self.writing_style: str = ""
        self.vocabulary: list[str] = []
        self.facts: list[str] = []
        self.video_lines: list[str] = []
        self.example_messages: list[str] = []
        self.reload()

    def reload(self):
        """(Re)load persona data from disk."""
        self._load_persona_json()
        log.info(
            "Persona loaded: %d facts, %d examples, %d video lines",
            len(self.facts),
            len(self.example_messages),
            len(self.video_lines),
        )

    def _load_persona_json(self):
        path = _resolve_persona_path(self._filename)
        if not path.exists():
            log.warning("Persona file not found at %s — using defaults", path)
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.name = data.get("name", self.name)
        self.bio = data.get("bio", "")
        self.writing_style = data.get("writing_style", "")
        self.vocabulary = data.get("vocabulary", [])
        self.facts = data.get("facts", [])
        self.video_lines = data.get("video_lines", [])
        self.example_messages = data.get("example_messages", [])

    def _format_facts(self) -> str:
        """Format known facts about the persona."""
        if not self.facts:
            return ""
        lines = [f"Known facts about {self.name} (use these to inform responses):"]
        for fact in self.facts:
            lines.append(f"  - {fact}")
        return "\n".join(lines)

    def _format_examples(self) -> str:
        """Format example messages for template substitution."""
        if not self.example_messages:
            return ""
        lines = [f"Examples of how {self.name} writes:"]
        for i, msg in enumerate(self.example_messages, 1):
            lines.append(f"  {i}. {msg}")
        return "\n".join(lines)

    def _format_video_lines(self) -> str:
        """Format video lines for template substitution."""
        if not self.video_lines:
            return ""
        lines = [f"Direct lines from {self.name}'s videos (study the tone and speech patterns):"]
        for line in self.video_lines:
            lines.append(f"  - {line}")
        return "\n".join(lines)

    def render_system_prompt(self, template: str = "") -> str:
        """Render the system prompt by filling persona data into a template."""
        if not template:
            template = DEFAULT_TEMPLATE

        facts_text = self._format_facts()
        video_lines_text = self._format_video_lines()
        replacements = {
            "name": self.name,
            "bio": f"Bio: {self.bio}" if self.bio else "",
            "facts": facts_text,
            "topics": facts_text,  # alias used in some custom templates
            "writing_style": f"Writing style: {self.writing_style}" if self.writing_style else "",
            "vocabulary": (
                f"Vocabulary that subtly colors how {self.name} speaks "
                f"(use sparingly to flavor the tone, NOT as the primary words in responses): "
                + ", ".join(self.vocabulary)
            ) if self.vocabulary else "",
            "example_messages": self._format_examples(),
            "video_lines": video_lines_text,
            "transcript_excerpts": video_lines_text,  # alias used in some custom templates
        }

        result = template
        for key, value in replacements.items():
            result = result.replace("{" + key + "}", value)

        # Clean up blank lines left by empty sections
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        return result

    @property
    def system_prompt(self) -> str:
        """Build the full system prompt (backward compat, uses default template)."""
        return self.render_system_prompt(DEFAULT_TEMPLATE)

    def to_dict(self) -> dict:
        """Return persona data as a plain dict."""
        return {
            "name": self.name,
            "bio": self.bio,
            "writing_style": self.writing_style,
            "vocabulary": list(self.vocabulary),
            "facts": list(self.facts),
            "video_lines": list(self.video_lines),
            "example_messages": list(self.example_messages),
        }

    def apply_overrides(self, data: dict):
        """Apply overrides from a dict (e.g. from config or web API)."""
        for key in ("name", "bio", "writing_style", "vocabulary", "facts", "video_lines", "example_messages"):
            if key in data:
                setattr(self, key, data[key])
