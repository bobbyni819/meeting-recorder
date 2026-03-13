"""Tests for automatic tag suggestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.auto_tag import (
    suggest_tags,
    suggest_tags_for_recording,
    _extract_keywords,
)


class TestSuggestTags:
    def test_empty_transcript(self):
        """Empty transcript should return no suggestions."""
        assert suggest_tags("") == []

    def test_short_transcript(self):
        """Very short transcript should return no suggestions."""
        assert suggest_tags("Hello everyone") == []

    def test_budget_topic(self):
        """Should detect budget topic from keywords."""
        text = (
            "We need to review the budget for Q3. The costs are "
            "increasing and we need to adjust our spending. The financial "
            "report shows revenue is up but expenses are growing. Let's "
            "look at the budget breakdown and discuss funding priorities. "
            "The invoice from the vendor is still pending."
        )
        tags = suggest_tags(text)
        assert "budget" in tags

    def test_engineering_topic(self):
        """Should detect engineering topic."""
        text = (
            "The deployment pipeline needs fixing. We have a bug in the "
            "API that's causing server errors. The database migration "
            "failed and we need to deploy a hotfix. Let's review the "
            "pull request for the infrastructure changes. The code "
            "review found several issues in the architecture."
        )
        tags = suggest_tags(text)
        assert "engineering" in tags

    def test_standup_topic(self):
        """Should detect standup meeting pattern."""
        text = (
            "Good morning everyone. Let's do standup. Yesterday I "
            "worked on the login page. Today I'm going to focus on "
            "the API integration. I'm blocked on the database access. "
            "Anyone have a blocker? Let me share my progress update. "
            "Tomorrow I plan to finish the feature."
        )
        tags = suggest_tags(text)
        assert "standup" in tags

    def test_excludes_existing_tags(self):
        """Should not suggest tags that already exist."""
        text = (
            "We need to review the budget for Q3. The costs are "
            "increasing and the budget needs adjustment. Financial "
            "review of spending and revenue forecast. Budget budget budget."
        )
        tags = suggest_tags(text, existing_tags=["budget"])
        assert "budget" not in tags

    def test_max_tags(self):
        """Should respect max_tags limit."""
        text = (
            "budget cost spending financial revenue "
            "deploy code api server database "
            "design mockup wireframe figma ui "
            "sales deal pipeline prospect lead " * 5
        )
        tags = suggest_tags(text, max_tags=3)
        assert len(tags) <= 3

    def test_multiple_topics(self):
        """Should detect multiple topics in one transcript."""
        text = (
            "The engineering team needs to deploy the new feature before "
            "the product launch. We should review the budget for hiring "
            "additional developers. The deployment pipeline code needs "
            "fixing. Budget for this quarter covers the product roadmap "
            "and API infrastructure. Let's look at product release plans."
        )
        tags = suggest_tags(text)
        assert len(tags) >= 2

    def test_keyword_extraction(self):
        """Should extract frequent keywords when no topic matches."""
        text = (
            "The quantum computing research shows promising results. "
            "Our quantum experiments have demonstrated coherence times. "
            "The quantum processor achieved quantum advantage in this "
            "benchmark. Computing power has increased significantly. "
            "Quantum quantum quantum computing computing research."
        )
        tags = suggest_tags(text)
        assert len(tags) > 0
        # "quantum" should be a suggested keyword
        assert any("quantum" in t.lower() for t in tags)


class TestExtractKeywords:
    def test_basic_extraction(self):
        """Should extract frequently occurring words."""
        text = "python python python java java ruby"
        keywords = _extract_keywords(text)
        assert len(keywords) > 0
        words = [w for w, c in keywords]
        assert "python" in words

    def test_filters_stop_words(self):
        """Should not include stop words."""
        text = "the the the and and and is is is but but but"
        keywords = _extract_keywords(text)
        words = [w for w, c in keywords]
        assert "the" not in words
        assert "and" not in words

    def test_minimum_frequency(self):
        """Words appearing less than 3 times should be excluded."""
        text = "unique singleton rare python python python"
        keywords = _extract_keywords(text)
        words = [w for w, c in keywords]
        assert "unique" not in words
        assert "python" in words

    def test_bigrams(self):
        """Should extract common bigrams."""
        text = (
            "machine learning is great. machine learning models. "
            "machine learning pipeline. deep learning too."
        )
        keywords = _extract_keywords(text)
        kw_strings = [w for w, c in keywords]
        assert any("machine" in k and "learning" in k for k in kw_strings)


class TestSuggestTagsForRecording:
    def test_from_transcript(self, tmp_path: Path):
        """Should read transcript from disk."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Budget review meeting. We discussed the budget allocation "
            "for Q3. The financial costs and spending report was reviewed. "
            "Budget adjustments needed for revenue targets.",
            encoding="utf-8",
        )
        tags = suggest_tags_for_recording(rec)
        assert "budget" in tags

    def test_from_summary(self, tmp_path: Path):
        """Should also consider summary.md content."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "## Engineering Discussion\n"
            "The team discussed the deployment pipeline and API "
            "architecture. Code review process and database migration "
            "were the main topics. Server infrastructure needs updating.",
            encoding="utf-8",
        )
        tags = suggest_tags_for_recording(rec)
        assert "engineering" in tags

    def test_excludes_existing(self, tmp_path: Path):
        """Should exclude tags already in metadata."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "metadata.json").write_text(
            json.dumps({"tags": ["budget"]}), encoding="utf-8")
        (rec / "transcript.txt").write_text(
            "Budget review. Budget allocation. Budget costs. Financial budget. "
            "Budget spending. Revenue budget forecast.",
            encoding="utf-8",
        )
        tags = suggest_tags_for_recording(rec)
        assert "budget" not in tags

    def test_no_transcript(self, tmp_path: Path):
        """Should return empty list without transcript."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        tags = suggest_tags_for_recording(rec)
        assert tags == []

    def test_with_provided_meta(self, tmp_path: Path):
        """Should use provided metadata."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Budget review. Budget allocation. Budget costs. Financial "
            "spending and revenue. Budget forecast needed.",
            encoding="utf-8",
        )
        tags = suggest_tags_for_recording(rec, meta={"tags": []})
        assert "budget" in tags
