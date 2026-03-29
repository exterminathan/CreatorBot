"""
Tests: ai/persona.py
=====================

Covers Persona.render_system_prompt(), Persona.apply_overrides(), and
Persona.to_dict().  No disk I/O — the fixture builds the object in-memory.

Run:
    pytest tests/test_persona.py -v
"""

from __future__ import annotations

import json
import pytest

from ai.persona import Persona, DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# render_system_prompt
# ---------------------------------------------------------------------------

class TestRenderSystemPrompt:
    def test_default_template_substituted(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert "TestBot" in result

    def test_bio_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert minimal_persona.bio in result

    def test_writing_style_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert minimal_persona.writing_style in result

    def test_vocabulary_word_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert "lol" in result

    def test_fact_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert "TestBot likes testing." in result

    def test_example_message_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert "sounds good ngl" in result

    def test_video_line_included(self, minimal_persona):
        result = minimal_persona.render_system_prompt()
        assert "okay so like..." in result

    def test_custom_template_used_when_provided(self, minimal_persona):
        result = minimal_persona.render_system_prompt("You are {name}. Custom.")
        assert "You are TestBot. Custom." in result

    def test_no_placeholder_leakage(self, minimal_persona):
        """No raw {placeholder} should remain in the rendered output."""
        result = minimal_persona.render_system_prompt()
        import re
        remaining = re.findall(r'\{[a-z_]+\}', result)
        assert remaining == [], f"Unresolved placeholders: {remaining}"

    def test_empty_sections_cleaned_up(self, minimal_persona):
        """Sections with empty content should not leave multiple blank lines."""
        minimal_persona.video_lines = []
        result = minimal_persona.render_system_prompt()
        assert "\n\n\n" not in result

    def test_empty_persona_renders_without_error(self):
        """A completely empty Persona should not raise."""
        p = Persona.__new__(Persona)
        p.name = "Empty"
        p.bio = ""
        p.writing_style = ""
        p.vocabulary = []
        p.facts = []
        p.video_lines = []
        p.example_messages = []
        result = p.render_system_prompt()
        assert "Empty" in result


# ---------------------------------------------------------------------------
# apply_overrides
# ---------------------------------------------------------------------------

class TestApplyOverrides:
    def test_name_override(self, minimal_persona):
        minimal_persona.apply_overrides({"name": "Zara"})
        assert minimal_persona.name == "Zara"

    def test_bio_override(self, minimal_persona):
        minimal_persona.apply_overrides({"bio": "New bio text"})
        assert minimal_persona.bio == "New bio text"

    def test_vocabulary_override(self, minimal_persona):
        minimal_persona.apply_overrides({"vocabulary": ["based", "slay"]})
        assert minimal_persona.vocabulary == ["based", "slay"]

    def test_partial_override_does_not_clobber_other_fields(self, minimal_persona):
        original_bio = minimal_persona.bio
        minimal_persona.apply_overrides({"name": "Other"})
        assert minimal_persona.bio == original_bio

    def test_unknown_keys_are_ignored(self, minimal_persona):
        """Extra keys in override dict should not raise."""
        minimal_persona.apply_overrides({"unknown_field": "value"})


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    def test_round_trip(self, minimal_persona):
        """to_dict() output should be JSON-serialisable."""
        d = minimal_persona.to_dict()
        json.dumps(d)  # raises if not serialisable

    def test_name_in_dict(self, minimal_persona):
        assert minimal_persona.to_dict()["name"] == "TestBot"

    def test_all_keys_present(self, minimal_persona):
        d = minimal_persona.to_dict()
        for key in ("name", "bio", "writing_style", "vocabulary",
                    "facts", "video_lines", "example_messages"):
            assert key in d, f"Missing key: {key}"
