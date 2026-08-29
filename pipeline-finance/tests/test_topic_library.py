"""
tests/test_topic_library.py — the evergreen explainer pool.

Daily recaps expire in a day and compete with CNBC. Explainers accumulate
search traffic for years. The pool is the channel's only asset with a
shelf life longer than a news cycle, and its rotation state is what keeps
the same explainer from running twice in a quarter.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def state_dir(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    return tmp_path


class TestLibraryIsWellFormed:
    @staticmethod
    def _topics():
        from scrapers.sunday_topic_library import _load
        return _load()["topics"]

    def test_ids_are_unique(self):
        """A duplicate id would let two topics share one cooldown slot."""
        ids = [t["id"] for t in self._topics()]
        assert len(ids) == len(set(ids))

    def test_every_topic_has_the_full_schema(self):
        required = {"id", "title", "subtopics", "key_concepts",
                    "current_relevance", "target_audience", "estimated_views", "tags"}
        for topic in self._topics():
            assert set(topic) == required, topic["id"]

    def test_the_pool_outlasts_the_cooldown(self):
        """
        With a 12-week cooldown, a pool smaller than 12 forces repeats. The
        point of expanding it was a year of Sundays without one.
        """
        from scrapers.sunday_topic_library import _load
        library = _load()
        cooldown = library["rotation_schedule"]["cooldown_weeks"]
        assert len(library["topics"]) > cooldown * 4

    def test_weights_cover_every_view_estimate(self):
        from scrapers.sunday_topic_library import _load
        library = _load()
        weights = library["rotation_schedule"]["weights"]
        for topic in library["topics"]:
            assert topic["estimated_views"] in weights, topic["id"]


class TestRotationStateSurvivesDeploys:
    """
    Rotation history used to be written back into the library JSON, which
    git tracks — and update.sh does `git reset --hard`, so every deploy
    silently wiped it and the cooldown restarted from nothing.
    """

    def test_state_is_written_outside_the_repo(self, state_dir):
        from scrapers.sunday_topic_library import mark_used, _LIBRARY_PATH
        before = _LIBRARY_PATH.read_text(encoding="utf-8")
        mark_used("options_basics")
        assert (state_dir / "sunday_topic_state.json").exists()
        assert _LIBRARY_PATH.read_text(encoding="utf-8") == before, \
            "the git-tracked library must not be modified — a deploy would wipe it"

    def test_state_is_in_the_backup_set(self):
        """Rotation history is exactly what the weekly backup exists for."""
        from scheduler.backup import BACKUP_SET
        assert "sunday_topic_state.json" in BACKUP_SET

    def test_history_in_the_old_location_still_reads(self, state_dir, monkeypatch):
        """A machine upgrading keeps whatever rotation survived."""
        from scrapers import sunday_topic_library as lib
        monkeypatch.setattr(lib, "_load", lambda: {
            "topics": [], "last_used": {"legacy_topic": "2026-08-01"}})
        assert lib._load_state() == {"legacy_topic": "2026-08-01"}

    def test_a_corrupt_state_file_is_survivable(self, state_dir):
        from scrapers.sunday_topic_library import _load_state
        (state_dir / "sunday_topic_state.json").write_text("{ not json")
        assert _load_state() == {}

    def test_recording_never_raises(self, tmp_path):
        """Losing a cooldown entry must not fail a publish."""
        from config.settings import settings
        from scrapers.sunday_topic_library import mark_used
        blocked = tmp_path / "logs"
        blocked.write_text("a file where a directory should be")
        settings.logs_dir = blocked
        mark_used("options_basics")      # must not raise


class TestCooldown:
    def test_a_used_topic_leaves_the_pool(self, state_dir):
        from scrapers.sunday_topic_library import get_available_topics, mark_used
        before = len(get_available_topics())
        mark_used("options_basics")
        after = get_available_topics()
        assert len(after) == before - 1
        assert all(t["id"] != "options_basics" for t in after)

    def test_a_topic_returns_after_the_cooldown(self, state_dir):
        from scrapers.sunday_topic_library import (
            get_available_topics, _save_state)
        stale = (date.today() - timedelta(weeks=20)).isoformat()
        _save_state({"options_basics": stale})
        assert any(t["id"] == "options_basics" for t in get_available_topics())

    def test_picking_records_the_choice(self, state_dir):
        from scrapers.sunday_topic_library import pick_topic, _load_state
        chosen = pick_topic()
        assert chosen["id"] in _load_state()

    def test_picking_twice_gives_two_different_topics(self, state_dir):
        from scrapers.sunday_topic_library import pick_topic
        first, second = pick_topic(), pick_topic()
        assert first["id"] != second["id"]

    def test_a_year_of_sundays_never_repeats(self, state_dir):
        """The whole point of the expansion."""
        from scrapers.sunday_topic_library import pick_topic
        seen = {pick_topic()["id"] for _ in range(52)}
        assert len(seen) == 52

    def test_an_exhausted_pool_falls_back_rather_than_failing(self, state_dir):
        from scrapers.sunday_topic_library import (
            _load, _save_state, get_available_topics)
        today = date.today().isoformat()
        _save_state({t["id"]: today for t in _load()["topics"]})
        assert len(get_available_topics()) == len(_load()["topics"])

    def test_reset_clears_history(self, state_dir):
        from scrapers.sunday_topic_library import mark_used, reset_cooldowns, _load_state
        mark_used("options_basics")
        reset_cooldowns()
        assert _load_state() == {}


class TestThemeRouting:
    def test_each_theme_returns_a_usable_pool(self):
        from scrapers.sunday_topic_library import get_topics_for_theme
        for theme in ("investment_banking", "insurance_protection",
                      "savings_wealth", "rotating_bonus"):
            assert len(get_topics_for_theme(theme)) >= 10, theme

    def test_most_topics_are_reachable_by_some_theme(self):
        """
        Theme routing matches the id and the tags. Matching the id alone left
        two thirds of the expanded pool unreachable.
        """
        from scrapers.sunday_topic_library import get_topics_for_theme, _load
        reachable = set()
        for theme in ("investment_banking", "insurance_protection",
                      "savings_wealth", "rotating_bonus"):
            reachable |= {t["id"] for t in get_topics_for_theme(theme)}
        assert len(reachable) >= 0.75 * len(_load()["topics"])

    def test_an_unknown_theme_returns_everything(self):
        from scrapers.sunday_topic_library import get_topics_for_theme, _load
        assert len(get_topics_for_theme("nonsense")) == len(_load()["topics"])
