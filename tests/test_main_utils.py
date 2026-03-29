"""
Tests: bot/main.py — URL & exclusion utilities
================================================

Covers the pure helper functions extracted from bot/main.py:
    _find_exclusion_violations()
    _extract_urls()
    _strip_urls()
    _surface_links()

These run fully offline with no network or Discord dependency.

Run:
    pytest tests/test_main_utils.py -v
"""

from __future__ import annotations

import pytest

# Import the private helpers directly from the module.
from bot.main import (
    _find_exclusion_violations,
    _extract_urls,
    _strip_urls,
    _surface_links,
)


# ---------------------------------------------------------------------------
# _find_exclusion_violations
# ---------------------------------------------------------------------------

class TestFindExclusionViolations:
    def test_no_violations_returns_empty_list(self):
        text = "I love pizza and games."
        exclusions = [{"topic": "drugs", "severity": 3}]
        assert _find_exclusion_violations(text, exclusions) == []

    def test_exact_match_detected(self):
        text = "Let's talk about gambling today."
        exclusions = [{"topic": "gambling", "severity": 3}]
        result = _find_exclusion_violations(text, exclusions)
        assert "gambling" in result

    def test_case_insensitive_match(self):
        text = "GAMBLING is bad."
        exclusions = [{"topic": "gambling", "severity": 3}]
        result = _find_exclusion_violations(text, exclusions)
        assert "gambling" in result

    def test_severity1_not_reported(self):
        """Severity 1 = explicitly allowed — should never be a violation."""
        text = "I love cats."
        exclusions = [{"topic": "cats", "severity": 1}]
        assert _find_exclusion_violations(text, exclusions) == []

    def test_severity2_is_reported(self):
        text = "politics is everywhere"
        exclusions = [{"topic": "politics", "severity": 2}]
        assert "politics" in _find_exclusion_violations(text, exclusions)

    def test_asterisk_censored_variant_detected(self):
        """'g*mbling' should match topic 'gambling'."""
        text = "g*mbling is risky"
        exclusions = [{"topic": "gambling", "severity": 3}]
        result = _find_exclusion_violations(text, exclusions)
        assert "gambling" in result

    def test_partial_word_not_matched(self):
        """'gamblingsite' should NOT trigger exclusion for 'gambling' (word boundary)."""
        text = "gamblingsite.com is a domain"
        exclusions = [{"topic": "gambling", "severity": 3}]
        # 'gambling' in 'gamblingsite' is not a word boundary match
        result = _find_exclusion_violations(text, exclusions)
        assert "gambling" not in result

    def test_empty_exclusion_list(self):
        assert _find_exclusion_violations("anything here", []) == []

    def test_empty_text_no_violations(self):
        exclusions = [{"topic": "drugs", "severity": 3}]
        assert _find_exclusion_violations("", exclusions) == []

    def test_multi_word_topic_detected(self):
        text = "I hate drug abuse in our community"
        exclusions = [{"topic": "drug abuse", "severity": 3}]
        result = _find_exclusion_violations(text, exclusions)
        assert "drug abuse" in result


# ---------------------------------------------------------------------------
# _extract_urls
# ---------------------------------------------------------------------------

class TestExtractUrls:
    def test_no_urls_returns_empty(self):
        assert _extract_urls("just some text") == []

    def test_single_http_url(self):
        urls = _extract_urls("check out http://example.com today")
        assert urls == ["http://example.com"]

    def test_single_https_url(self):
        urls = _extract_urls("visit https://discord.com/channels/123")
        assert "https://discord.com/channels/123" in urls

    def test_multiple_urls(self):
        text = "see https://a.com and https://b.com for details"
        urls = _extract_urls(text)
        assert len(urls) == 2
        assert "https://a.com" in urls
        assert "https://b.com" in urls

    def test_url_with_path_and_query(self):
        text = "here: https://example.com/path?key=value&other=1"
        urls = _extract_urls(text)
        assert "https://example.com/path?key=value&other=1" in urls

    def test_non_http_scheme_not_matched(self):
        assert _extract_urls("ftp://files.example.com") == []


# ---------------------------------------------------------------------------
# _strip_urls
# ---------------------------------------------------------------------------

class TestStripUrls:
    def test_url_removed_from_text(self):
        result = _strip_urls("Check https://example.com for info")
        assert "https://example.com" not in result
        assert "Check" in result
        assert "for info" in result

    def test_text_without_url_unchanged(self):
        text = "hello world"
        assert _strip_urls(text) == text

    def test_multiple_urls_removed(self):
        result = _strip_urls("visit https://a.com and https://b.com")
        assert "https://a.com" not in result
        assert "https://b.com" not in result

    def test_no_triple_blank_lines_after_strip(self):
        text = "line1\nhttps://example.com\n\nline2"
        result = _strip_urls(text)
        assert "\n\n\n" not in result


# ---------------------------------------------------------------------------
# _surface_links
# ---------------------------------------------------------------------------

class TestSurfaceLinks:
    def test_no_urls_text_unchanged(self):
        text = "plain message without links"
        assert _surface_links(text) == text

    def test_url_moved_to_end(self):
        text = "check https://example.com out"
        result = _surface_links(text)
        lines = result.strip().split("\n")
        # URL must be on its own line at the end
        assert "https://example.com" in lines[-1]
        # Body text should not contain the URL inline
        assert "https://example.com" not in lines[0]

    def test_extra_links_appended(self):
        text = "plain message"
        result = _surface_links(text, extra_links=["https://extra.com"])
        assert "https://extra.com" in result

    def test_no_duplicate_links(self):
        text = "see https://example.com for more"
        result = _surface_links(text, extra_links=["https://example.com"])
        assert result.count("https://example.com") == 1

    def test_multiple_urls_each_on_own_line(self):
        text = "https://a.com and https://b.com"
        result = _surface_links(text)
        assert "https://a.com" in result
        assert "https://b.com" in result
        # Each URL on its own line
        url_lines = [l for l in result.split("\n") if "http" in l]
        assert len(url_lines) == 2
