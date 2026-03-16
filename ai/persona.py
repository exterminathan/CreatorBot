from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PERSONA_PATH = DATA_DIR / "cy_persona.json"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"


class Persona:
    """Loads Cy's persona definition + transcript samples and builds a
    system prompt string for the LLM."""

    def __init__(self):
        self.name: str = "Cy"
        self.bio: str = ""
        self.writing_style: str = ""
        self.vocabulary: list[str] = []
        self.topics: list[str] = []
        self.example_messages: list[str] = []
        self.transcript_excerpts: list[str] = []
        self.reload()

    def reload(self):
        """(Re)load persona data from disk."""
        self._load_persona_json()
        self._load_transcripts()
        log.info(
            "Persona loaded: %d examples, %d transcript excerpts",
            len(self.example_messages),
            len(self.transcript_excerpts),
        )

    def _load_persona_json(self):
        if not PERSONA_PATH.exists():
            log.warning("Persona file not found at %s — using defaults", PERSONA_PATH)
            return
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.name = data.get("name", self.name)
        self.bio = data.get("bio", "")
        self.writing_style = data.get("writing_style", "")
        self.vocabulary = data.get("vocabulary", [])
        self.topics = data.get("topics", [])
        self.example_messages = data.get("example_messages", [])

    def _load_transcripts(self):
        self.transcript_excerpts = []
        if not TRANSCRIPTS_DIR.is_dir():
            return
        for path in sorted(TRANSCRIPTS_DIR.glob("*.txt")):
            text = path.read_text(encoding="utf-8").strip()
            if text:
                self.transcript_excerpts.append(text)

    @property
    def system_prompt(self) -> str:
        """Build the full system prompt that tells the LLM to behave as Cy."""
        parts: list[str] = []

        parts.append(
            f"You are {self.name}. You must write EXACTLY like {self.name} — "
            f"same tone, same vocabulary, same vibe. Never break character. "
            f"Never say you are an AI. You ARE {self.name}."
        )

        if self.bio:
            parts.append(f"Bio: {self.bio}")

        if self.writing_style:
            parts.append(f"Writing style: {self.writing_style}")

        if self.vocabulary:
            parts.append(
                "Words/phrases Cy commonly uses: " + ", ".join(self.vocabulary)
            )

        if self.topics:
            parts.append(
                "Topics Cy frequently talks about: " + ", ".join(self.topics)
            )

        if self.example_messages:
            parts.append("Examples of how Cy writes:")
            for i, msg in enumerate(self.example_messages, 1):
                parts.append(f"  {i}. {msg}")

        if self.transcript_excerpts:
            parts.append("Excerpts from Cy's videos/posts (study the tone):")
            for excerpt in self.transcript_excerpts[:10]:  # cap to avoid giant prompts
                # Truncate long excerpts
                if len(excerpt) > 500:
                    excerpt = excerpt[:500] + "…"
                parts.append(f"  - {excerpt}")

        parts.append(
            "IMPORTANT: Keep responses casual and natural. "
            "Match the length and energy of the prompt. "
            "Do NOT use hashtags or emojis unless Cy typically does. "
            "Write as a Discord message, not an essay."
        )

        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        """Return persona data as a plain dict."""
        return {
            "name": self.name,
            "bio": self.bio,
            "writing_style": self.writing_style,
            "vocabulary": list(self.vocabulary),
            "topics": list(self.topics),
            "example_messages": list(self.example_messages),
        }

    def apply_overrides(self, data: dict):
        """Apply overrides from a dict (e.g. from config or web API)."""
        for key in ("name", "bio", "writing_style", "vocabulary", "topics", "example_messages"):
            if key in data:
                setattr(self, key, data[key])
