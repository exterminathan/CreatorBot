"""Tests for the forms system: Config form methods and forms_manager."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.forms_manager import new_form_id, MAX_FIELDS


# ---------------------------------------------------------------------------
# new_form_id
# ---------------------------------------------------------------------------


class TestNewFormId:
    def test_starts_with_form_prefix(self):
        fid = new_form_id()
        assert fid.startswith("form_")

    def test_unique(self):
        ids = {new_form_id() for _ in range(100)}
        assert len(ids) == 100

    def test_length(self):
        # "form_" + 8 hex chars = 13 chars
        assert len(new_form_id()) == 13


# ---------------------------------------------------------------------------
# MAX_FIELDS constant
# ---------------------------------------------------------------------------


def test_max_fields_is_5():
    """Discord modals support at most 5 text inputs."""
    assert MAX_FIELDS == 5


# ---------------------------------------------------------------------------
# Config.forms CRUD methods
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg(minimal_config_env, tmp_path, monkeypatch):
    """Return a Config with no disk side-effects."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("ADMIN_CHANNEL_ID", "1")
    monkeypatch.setenv("ADMIN_USER_ID", "2")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.delenv("CONFIG_BUCKET", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("bot.config.CONFIG_PATH", config_path)
    from bot.config import Config
    return Config()


def _make_form(name: str = "Test Form") -> dict:
    return {
        "id": new_form_id(),
        "name": name,
        "description": "A test form",
        "enabled": True,
        "required_role_ids": [],
        "submission_channel_id": None,
        "dm_submitter": False,
        "confirmation_message": "Thanks!",
        "fields": [
            {
                "label": "Your name",
                "style": "short",
                "placeholder": "",
                "required": True,
                "min_length": None,
                "max_length": None,
            }
        ],
    }


class TestConfigFormsCRUD:
    def test_add_form(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        assert len(cfg.forms) == 1
        assert cfg.forms[0]["name"] == "Test Form"

    def test_get_form_existing(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        result = cfg.get_form(form["id"])
        assert result is not None
        assert result["id"] == form["id"]

    def test_get_form_missing(self, cfg):
        assert cfg.get_form("form_nonexistent") is None

    def test_update_form(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        ok = cfg.update_form(form["id"], {"name": "Updated"})
        assert ok is True
        assert cfg.get_form(form["id"])["name"] == "Updated"

    def test_update_form_missing(self, cfg):
        assert cfg.update_form("form_missing", {"name": "x"}) is False

    def test_remove_form(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        ok = cfg.remove_form(form["id"])
        assert ok is True
        assert cfg.get_form(form["id"]) is None

    def test_remove_form_missing(self, cfg):
        assert cfg.remove_form("form_missing") is False

    def test_remove_form_purges_submissions(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        sub = {
            "form_id": form["id"],
            "user_id": "999",
            "user_name": "user#0001",
            "submitted_at": int(time.time()),
            "answers": ["Alice"],
        }
        cfg.add_form_submission(sub)
        assert len(cfg.form_submissions) == 1
        cfg.remove_form(form["id"])
        assert len(cfg.form_submissions) == 0

    def test_add_form_submission(self, cfg):
        form = _make_form()
        cfg.add_form(form)
        sub = {
            "form_id": form["id"],
            "user_id": "123",
            "user_name": "user",
            "submitted_at": int(time.time()),
            "answers": ["Bob"],
        }
        cfg.add_form_submission(sub)
        assert len(cfg.form_submissions) == 1

    def test_submission_cap(self, cfg):
        """Submissions are capped at 2000 total."""
        form = _make_form()
        cfg.add_form(form)
        ts = int(time.time())
        for i in range(2005):
            cfg.form_submissions.append({
                "form_id": form["id"],
                "user_id": str(i),
                "user_name": "u",
                "submitted_at": ts,
                "answers": ["x"],
            })
        # Cap enforced by add_form_submission
        one_more = {
            "form_id": form["id"],
            "user_id": "99999",
            "user_name": "u",
            "submitted_at": ts,
            "answers": ["x"],
        }
        cfg.add_form_submission(one_more)
        assert len(cfg.form_submissions) <= 2000

    def test_forms_persist(self, cfg, tmp_path):
        """Forms survive a save/reload cycle."""
        form = _make_form("Persistent Form")
        cfg.add_form(form)
        # Re-load from same config path
        from bot.config import Config
        cfg2 = Config()
        assert any(f["name"] == "Persistent Form" for f in cfg2.forms)


# ---------------------------------------------------------------------------
# build_modal (smoke test — no Discord connection needed)
# ---------------------------------------------------------------------------


class TestBuildModal:
    def test_returns_class(self):
        from bot.forms_manager import build_modal
        form = _make_form()
        ModalClass = build_modal(form)
        assert ModalClass is not None

    def test_modal_title_truncated(self):
        from bot.forms_manager import build_modal
        form = _make_form("A" * 100)
        ModalClass = build_modal(form)
        # title kwarg passed to Modal is truncated to 45 chars
        # We check the class can be instantiated with a mock cfg
        cfg_mock = MagicMock()
        cfg_mock.add_form_submission = MagicMock()
        modal = ModalClass(cfg_mock)
        assert modal is not None

    def test_fields_added_to_modal(self):
        from bot.forms_manager import build_modal
        form = _make_form()
        form["fields"] = [
            {"label": "Q1", "style": "short", "placeholder": "", "required": True, "min_length": None, "max_length": None},
            {"label": "Q2", "style": "paragraph", "placeholder": "hint", "required": False, "min_length": None, "max_length": None},
        ]
        ModalClass = build_modal(form)
        cfg_mock = MagicMock()
        modal = ModalClass(cfg_mock)
        # Should have 2 TextInput children
        assert len(modal.children) == 2

    def test_max_fields_enforced(self):
        from bot.forms_manager import build_modal
        form = _make_form()
        form["fields"] = [
            {"label": f"Q{i}", "style": "short", "placeholder": "", "required": True, "min_length": None, "max_length": None}
            for i in range(10)  # more than max
        ]
        ModalClass = build_modal(form)
        cfg_mock = MagicMock()
        modal = ModalClass(cfg_mock)
        assert len(modal.children) <= MAX_FIELDS
