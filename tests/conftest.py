"""
Test Configuration & Shared Fixtures
=====================================

This file is loaded automatically by pytest before any tests run.
It provides shared fixtures used across multiple test modules.

Fixtures defined here:
    minimal_persona      – A Persona instance with no disk I/O, seeded with
                           simple test data.
    minimal_config_env   – Monkeypatches os.environ with the minimum env vars
                           needed to construct a Config without a .env file.
"""

from __future__ import annotations

import os
import json
import pytest

from ai.persona import Persona


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_persona() -> Persona:
    """Return a Persona where all fields are set to predictable test values.

    Bypasses disk I/O by building the object manually after construction so
    the persona file on disk is never loaded.
    """
    p = Persona.__new__(Persona)
    p.name = "TestBot"
    p.bio = "A test persona."
    p.writing_style = "Very casual, lots of ellipses."
    p.vocabulary = ["lol", "rly"]
    p.facts = ["TestBot likes testing."]
    p.video_lines = ["okay so like..."]
    p.example_messages = ["sounds good ngl"]
    return p


@pytest.fixture()
def minimal_config_env(monkeypatch):
    """Patch os.environ with the minimum required env vars for Config().

    Usage in a test:
        def test_something(minimal_config_env, tmp_path):
            ...
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("ADMIN_USER_ID", "222222222222222222")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("CONFIG_BUCKET", raising=False)
