"""
tests/test_search_demand.py — titles aimed at measured demand.

30% of this channel's views already come from YouTube search, on a channel
that had done no SEO work at all. The title scorer rewarded a static
SEO_KEYWORDS list written months ago; nothing consulted what people were
actually searching for, even after the Trends scraper was wired in — it fed
the script's wording and never the title's, and the title is the half that
earns search traffic.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def logs(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    return tmp_path


def _cache(logs, queries):
    (logs / "trending_queries.json").write_text(json.dumps({
        "date": date.today().isoformat(), "queries": queries}))


class TestDemandTerms:

    def test_distinctive_words_are_extracted(self):
        from generators.title_gen import _demand_terms
        terms = _demand_terms(["nvidia earnings date", "cpi report today"])
        assert "nvidia" in terms and "earnings" in terms and "report" in terms

    def test_words_in_every_finance_title_are_ignored(self):
        """
        Matching on "stock" or "market" would score every title identically
        and tell us nothing.
        """
        from generators.title_gen import _demand_terms
        terms = _demand_terms(["stock market today", "the price of stocks now"])
        assert terms == set()

    def test_short_words_are_ignored(self):
        from generators.title_gen import _demand_terms
        assert _demand_terms(["fed cut", "is it up"]) == set()

    def test_no_queries_is_an_empty_set_not_an_error(self):
        from generators.title_gen import _demand_terms
        assert _demand_terms([]) == set()
        assert _demand_terms(None) == set()


class TestScoringRewardsMeasuredDemand:

    def test_a_title_matching_a_rising_query_outscores_one_that_does_not(self):
        from generators.title_gen import _score_title, _demand_terms
        terms = _demand_terms(["nvidia earnings"])
        with_demand = _score_title("Nvidia Earnings Land: S&P 500 Falls 0.55%", terms)
        without = _score_title("Wall Street Wobbles: S&P 500 Falls 0.55%", terms)
        assert with_demand.total_score > without.total_score

    def test_live_demand_is_worth_more_than_a_static_keyword(self):
        """
        SEO_KEYWORDS is an educated guess written once. Rising queries are
        measurement of this morning.
        """
        from generators.title_gen import (
            _STATIC_KEYWORD_POINTS, _LIVE_DEMAND_POINTS)
        assert _LIVE_DEMAND_POINTS > _STATIC_KEYWORD_POINTS

    def test_the_hundred_point_scale_is_preserved(self):
        """
        The feedback thresholds ("Excellent" at 80) are calibrated to 100.
        Adding a component instead of sharing the keyword budget would
        silently move every verdict.
        """
        from generators.title_gen import _score_title, _demand_terms
        terms = _demand_terms(["nvidia earnings guidance revenue outlook"])
        score = _score_title(
            "Why Nvidia Earnings Guidance Revenue Outlook Crashed Stocks 5%?",
            terms)
        assert score.total_score <= 100.0
        assert score.keyword_score <= 30.0

    def test_scoring_without_demand_data_still_works(self):
        """Trends is rate-limited; a title must still be scorable without it."""
        from generators.title_gen import _score_title
        assert _score_title("Stock Market Today: S&P 500 Falls 0.55%").total_score > 0

    def test_a_matched_term_is_reported_in_the_feedback(self):
        from generators.title_gen import _score_title, _demand_terms
        score = _score_title("Nvidia Slides After Guidance",
                             _demand_terms(["nvidia guidance"]))
        assert any("search demand" in f for f in score.feedback)


class TestQueriesReachClaude:

    def test_the_prompt_carries_the_rising_queries(self, monkeypatch):
        from generators import title_gen
        captured = {}

        class FakeClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    captured["prompt"] = kwargs["messages"][0]["content"]
                    raise RuntimeError("stop here — we only want the prompt")

            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(title_gen.anthropic, "Anthropic", FakeClient)
        title_gen._generate_titles_via_claude(
            topic="markets", anchor_number="-0.55%", video_type="weekday",
            rising_queries=["nvidia earnings", "cpi report"])
        assert "nvidia earnings" in captured["prompt"]
        assert "SEARCHING FOR RIGHT NOW" in captured["prompt"]

    def test_claude_is_told_not_to_force_a_query_in(self, monkeypatch):
        """
        A title that misdescribes the video costs more than the search
        traffic is worth.
        """
        from generators import title_gen
        captured = {}

        class FakeClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    captured["prompt"] = kwargs["messages"][0]["content"]
                    raise RuntimeError("stop")

            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(title_gen.anthropic, "Anthropic", FakeClient)
        title_gen._generate_titles_via_claude(
            topic="t", anchor_number="", video_type="weekday",
            rising_queries=["nvidia earnings"])
        assert "Never force" in captured["prompt"]

    def test_no_queries_leaves_the_prompt_untouched(self, monkeypatch):
        from generators import title_gen
        captured = {}

        class FakeClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    captured["prompt"] = kwargs["messages"][0]["content"]
                    raise RuntimeError("stop")

            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(title_gen.anthropic, "Anthropic", FakeClient)
        title_gen._generate_titles_via_claude(
            topic="t", anchor_number="", video_type="weekday")
        assert "SEARCHING FOR RIGHT NOW" not in captured["prompt"]


class TestOneCacheForBothConsumers:
    """
    The script and the title generator must agree about what today's queries
    were, and one rate-limited endpoint must not be hit twice.
    """

    def test_both_read_the_same_cache(self, logs, monkeypatch):
        from scrapers.trends_scraper import todays_rising_queries
        from generators.script_gen import _trending_context
        _cache(logs, ["nvidia earnings", "cpi report"])

        def explode(*args, **kwargs):
            raise AssertionError("should not have hit the network")

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", explode)
        assert "nvidia earnings" in todays_rising_queries()
        assert "nvidia earnings" in _trending_context()

    def test_script_gen_no_longer_keeps_its_own_copy(self):
        import inspect
        from generators import script_gen
        source = inspect.getsource(script_gen._trending_context)
        assert "todays_rising_queries" in source
        assert "trending_queries.json" not in source


class TestTopicChoiceFollowsDemand:
    """
    Which evergreen explainer gets made was a fixed weighted rotation.
    Evergreen content earns search traffic for years where a daily recap is
    worthless after a day, so it is worth deciding on measured demand.
    """

    @staticmethod
    def _topic(topic_id, title, tags=(), subtopics=()):
        return {"id": topic_id, "title": title, "tags": list(tags),
                "subtopics": list(subtopics), "key_concepts": [],
                "current_relevance": "", "estimated_views": "medium"}

    def test_a_topic_is_scored_across_its_whole_entry(self):
        """
        Someone searching "0dte" should reach the options explainer even
        though its title says "Calls, Puts, and How Wall Street Really Bets".
        """
        from scrapers.sunday_topic_library import _demand_score
        topic = self._topic("options", "Calls, Puts and How Wall Street Bets",
                            tags=["options trading"], subtopics=["0dte options"])
        assert _demand_score(topic, {"0dte"}) == 1
        assert _demand_score(topic, {"nvidia"}) == 0

    def test_the_best_matching_topics_are_kept(self):
        from scrapers.sunday_topic_library import _demand_ranked
        topics = [
            self._topic("bonds", "How Bonds Work"),
            self._topic("options", "Options Explained", tags=["0dte"]),
        ]
        ranked = _demand_ranked(topics, {"0dte"})
        assert [t["id"] for t in ranked] == ["options"]

    def test_ties_are_all_kept_so_the_rotation_stays_varied(self):
        """
        Collapsing to a single winner would make selection deterministic and
        the channel repetitive — the problem cooldowns exist to prevent.
        """
        from scrapers.sunday_topic_library import _demand_ranked
        topics = [
            self._topic("a", "Inflation Basics", tags=["inflation"]),
            self._topic("b", "Inflation And Your Savings", tags=["inflation"]),
            self._topic("c", "How Bonds Work"),
        ]
        ranked = _demand_ranked(topics, {"inflation"})
        assert {t["id"] for t in ranked} == {"a", "b"}

    def test_no_match_leaves_every_topic_eligible(self):
        """
        Narrowing to nothing, or to an arbitrary subset, would be worse than
        the fixed rotation it replaces.
        """
        from scrapers.sunday_topic_library import _demand_ranked
        topics = [self._topic("a", "How Bonds Work"),
                  self._topic("b", "What Is An ETF")]
        assert _demand_ranked(topics, {"nvidia", "earnings"}) == topics

    def test_no_demand_data_leaves_every_topic_eligible(self):
        from scrapers.sunday_topic_library import _demand_ranked
        topics = [self._topic("a", "How Bonds Work")]
        assert _demand_ranked(topics, set()) == topics

    def test_a_trends_failure_does_not_stop_topic_selection(self, monkeypatch):
        """Sunday publishes on a clock; Trends is rate-limited and optional."""
        from scrapers import sunday_topic_library

        def boom(*args, **kwargs):
            raise RuntimeError("429 rate limited")

        monkeypatch.setattr("scrapers.trends_scraper.todays_rising_queries", boom)
        assert sunday_topic_library._todays_demand_terms() == set()

    def test_the_topic_library_does_not_import_from_generators(self):
        """
        scrapers/ must not depend on generators/. demand_terms lives with the
        trends data so both layers can read it the same way.
        """
        import inspect
        from scrapers import sunday_topic_library
        source = inspect.getsource(sunday_topic_library)
        assert "generators" not in source


class TestDemandMatchingIsWholeWord:
    """
    A plain substring test looked right and was wrong. The term "rate", from
    the query "fed rate decision", matched "Corporate Bonds Explained" and
    scored that title 12 points — more than two static SEO keywords — for an
    accidental overlap of letters.
    """

    def test_a_term_inside_a_longer_word_is_not_a_match(self):
        from scrapers.trends_scraper import demand_terms, matches_demand
        terms = demand_terms(["fed rate decision"])
        assert matches_demand("Corporate Bonds Explained", terms) == set()
        assert matches_demand("Accurate Ratings And Corporate Debt", terms) == set()

    def test_a_real_word_match_still_counts(self):
        from scrapers.trends_scraper import demand_terms, matches_demand
        terms = demand_terms(["fed rate decision"])
        assert matches_demand("Fed Rate Decision Day", terms) == {"rate", "decision"}

    def test_punctuation_does_not_block_a_match(self):
        from scrapers.trends_scraper import demand_terms, matches_demand
        terms = demand_terms(["nvidia earnings"])
        assert "nvidia" in matches_demand("NVIDIA: Earnings, Explained!", terms)

    def test_title_scoring_no_longer_rewards_the_false_match(self):
        from generators.title_gen import _score_title, _demand_terms
        terms = _demand_terms(["fed rate decision"])
        false_match = _score_title("Corporate Bonds Explained For Beginners", terms)
        assert not any("search demand" in f for f in false_match.feedback)

    def test_topic_scoring_no_longer_rewards_the_false_match(self):
        from scrapers.sunday_topic_library import _demand_score
        topic = {"title": "Corporate Bonds Explained", "subtopics": [],
                 "key_concepts": [], "tags": ["corporate debt"],
                 "current_relevance": ""}
        assert _demand_score(topic, {"rate"}) == 0

    def test_topic_scoring_still_finds_a_genuine_match_in_a_tag(self):
        from scrapers.sunday_topic_library import _demand_score
        topic = {"title": "Calls, Puts and How Wall Street Bets",
                 "subtopics": ["0dte options"], "key_concepts": [],
                 "tags": ["options trading"], "current_relevance": ""}
        assert _demand_score(topic, {"0dte"}) == 1

    def test_no_terms_matches_nothing_rather_than_everything(self):
        from scrapers.trends_scraper import matches_demand
        assert matches_demand("Any Title At All", set()) == set()
