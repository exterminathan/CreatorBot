"""
Tests: ai/prompt_builder.py
============================

Covers build_post_messages() and build_interaction_messages() — the functions
that assemble the LLM message list from persona + admin config.

All tests run fully offline (no network, no disk I/O, no Discord).

Run:
    pytest tests/test_prompt_builder.py -v
"""

from __future__ import annotations

import pytest

from ai.prompt_builder import (
    _build_exclusion_instructions,
    _build_slang_instructions,
    build_post_messages,
    build_interaction_messages,
    build_messages,
)


# ---------------------------------------------------------------------------
# _build_exclusion_instructions
# ---------------------------------------------------------------------------

class TestBuildExclusionInstructions:
    def test_empty_list_returns_empty_string(self):
        assert _build_exclusion_instructions([]) == ""

    def test_none_returns_empty_string(self):
        assert _build_exclusion_instructions(None) == ""

    def test_severity1_topics_are_ignored(self):
        """Severity 1 = explicitly allowed, must NOT appear in instructions."""
        result = _build_exclusion_instructions([{"topic": "cats", "severity": 1}])
        assert result == ""

    def test_severity2_produces_avoid_instruction(self):
        result = _build_exclusion_instructions([{"topic": "politics", "severity": 2}])
        assert "steer away" in result
        assert "politics" in result

    def test_severity3_produces_never_instruction(self):
        result = _build_exclusion_instructions([{"topic": "drugs", "severity": 3}])
        assert "Do NOT" in result
        assert "drugs" in result

    def test_mixed_severities_produce_both_sections(self):
        exclusions = [
            {"topic": "violence", "severity": 3},
            {"topic": "politics", "severity": 2},
        ]
        result = _build_exclusion_instructions(exclusions)
        assert "Do NOT" in result
        assert "violence" in result
        assert "steer away" in result
        assert "politics" in result

    def test_missing_severity_defaults_to_3(self):
        """Entries without a 'severity' key should default to severity 3 (block)."""
        result = _build_exclusion_instructions([{"topic": "slur"}])
        assert "Do NOT" in result
        assert "slur" in result


# ---------------------------------------------------------------------------
# _build_slang_instructions
# ---------------------------------------------------------------------------

class TestBuildSlangInstructions:
    def test_none_returns_empty_string(self):
        assert _build_slang_instructions(None) == ""

    def test_empty_dict_returns_empty_string(self):
        assert _build_slang_instructions({}) == ""

    def test_slang_produces_glossary_header(self):
        result = _build_slang_instructions({"based": "cool / admirable"})
        assert "glossary" in result.lower()

    def test_each_word_and_definition_appears(self):
        slang = {"lit": "exciting", "mid": "mediocre"}
        result = _build_slang_instructions(slang)
        assert '"lit"' in result
        assert "exciting" in result
        assert '"mid"' in result
        assert "mediocre" in result


# ---------------------------------------------------------------------------
# build_post_messages
# ---------------------------------------------------------------------------

class TestBuildPostMessages:
    def test_returns_two_messages(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "talk about gaming")
        assert len(msgs) == 2

    def test_first_message_is_system(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "some prompt")
        assert msgs[0]["role"] == "system"

    def test_second_message_is_user(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "some prompt")
        assert msgs[1]["role"] == "user"

    def test_user_instruction_appears_in_user_message(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "write about pasta")
        assert "pasta" in msgs[1]["content"]

    def test_persona_name_appears_in_system_prompt(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "x")
        assert minimal_persona.name in msgs[0]["content"]

    def test_system_prompt_appended(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "x", system_prompt="DO NOT SWEAR")
        assert "DO NOT SWEAR" in msgs[0]["content"]

    def test_exclusion_block_appended_to_system(self, minimal_persona):
        exclusions = [{"topic": "gambling", "severity": 3}]
        msgs = build_post_messages(minimal_persona, "x", exclusion_list=exclusions)
        assert "gambling" in msgs[0]["content"]

    def test_slang_block_appended_to_system(self, minimal_persona):
        slang = {"ngl": "not gonna lie"}
        msgs = build_post_messages(minimal_persona, "x", slang_dict=slang)
        assert "ngl" in msgs[0]["content"]

    def test_custom_template_respected(self, minimal_persona):
        msgs = build_post_messages(minimal_persona, "x", template="You are {name}. CUSTOM.")
        assert "CUSTOM" in msgs[0]["content"]

    def test_backward_compat_alias(self, minimal_persona):
        """build_messages() must be identical to build_post_messages()."""
        msgs_alias = build_messages(minimal_persona, "test")
        msgs_direct = build_post_messages(minimal_persona, "test")
        assert msgs_alias == msgs_direct


# ---------------------------------------------------------------------------
# build_interaction_messages
# ---------------------------------------------------------------------------

class TestBuildInteractionMessages:
    def test_returns_two_messages(self, minimal_persona):
        msgs = build_interaction_messages(minimal_persona, "hello", "Alice")
        assert len(msgs) == 2

    def test_user_name_appears_in_user_message(self, minimal_persona):
        msgs = build_interaction_messages(minimal_persona, "yo", "Alice")
        assert "Alice" in msgs[1]["content"]

    def test_user_message_appears_in_user_message(self, minimal_persona):
        msgs = build_interaction_messages(minimal_persona, "what is up", "Bob")
        assert "what is up" in msgs[1]["content"]

    def test_system_contains_reply_instruction(self, minimal_persona):
        """System prompt must instruct the model to reply to a mention, not re-post."""
        msgs = build_interaction_messages(minimal_persona, "hey", "Bob")
        system = msgs[0]["content"]
        assert "replying" in system.lower() or "reply" in system.lower()

    def test_additional_system_prompt_appended(self, minimal_persona):
        msgs = build_interaction_messages(
            minimal_persona, "hello", "Bob", system_prompt="BE SNARKY"
        )
        assert "BE SNARKY" in msgs[0]["content"]

    def test_exclusion_list_appended(self, minimal_persona):
        exclusions = [{"topic": "crypto", "severity": 3}]
        msgs = build_interaction_messages(
            minimal_persona, "hello", "Bob", exclusion_list=exclusions
        )
        assert "crypto" in msgs[0]["content"]
