"""
tests/test_usage_ledger.py — Claude spend observability.

The health report could say "credit exists" but never "credit is draining
twice as fast as last week". Running out stops the channel as completely
as a dead daemon does, and just as quietly.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


def _response(input_tokens=100, output_tokens=50, model="test-model"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _call(days_ago, source="script_gen", tin=3000, tout=1200):
    """days_ago accepts fractions — tests sit mid-window, never on its edge,
    since timestamps are second-truncated and boundary membership is not a
    property worth pinning down."""
    return {
        "at": (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        "source": source, "model": "m", "input_tokens": tin, "output_tokens": tout,
    }


def _ledger(tmp_path, entries):
    from config.settings import settings
    settings.logs_dir = tmp_path
    (tmp_path / "claude_usage.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries))
    return tmp_path


class TestRecording:
    def test_a_call_is_appended(self, tmp_path):
        from config.settings import settings
        from monitor.usage_ledger import record, _ledger_path
        settings.logs_dir = tmp_path
        record(_response(), "script_gen")
        entry = json.loads(_ledger_path().read_text().strip())
        assert entry["source"] == "script_gen"
        assert entry["input_tokens"] == 100 and entry["output_tokens"] == 50

    def test_recording_never_raises(self, tmp_path):
        """
        Bookkeeping sits inside the pipeline's Claude calls. If it can throw,
        it can fail a publishing run — which is a strictly worse outcome than
        losing a usage number.
        """
        from config.settings import settings
        from monitor.usage_ledger import record
        settings.logs_dir = tmp_path
        record(None, "x")
        record(object(), "x")
        record(SimpleNamespace(usage="not-a-usage-object"), "x")
        record(_response(), "x")

    def test_an_unwritable_ledger_is_survivable(self, tmp_path):
        from config.settings import settings
        from monitor.usage_ledger import record
        blocked = tmp_path / "logs"
        blocked.write_text("this is a file, not a directory")
        settings.logs_dir = blocked
        record(_response(), "script_gen")     # must not raise

    def test_a_response_without_usage_is_skipped(self, tmp_path):
        from config.settings import settings
        from monitor.usage_ledger import record, _ledger_path
        settings.logs_dir = tmp_path
        record(SimpleNamespace(model="m"), "x")
        assert not _ledger_path().exists()


class TestBurnSummary:
    def test_totals_and_daily_rate(self, tmp_path):
        from monitor.usage_ledger import burn_summary
        _ledger(tmp_path, [_call(d + 0.5) for d in range(7)])
        summary = burn_summary(7)
        assert summary["calls"] == 7
        assert summary["tokens"] == 7 * 4200
        assert summary["tokens_per_day"] == 4200

    def test_the_prior_window_is_the_comparison(self, tmp_path):
        from monitor.usage_ledger import burn_summary
        entries = [_call(d + 0.5, tin=6000, tout=2400) for d in range(7)]
        entries += [_call(d + 7.5, tin=3000, tout=1200) for d in range(7)]
        _ledger(tmp_path, entries)
        assert burn_summary(7)["ratio"] == 2.0

    def test_no_prior_window_means_no_ratio(self, tmp_path):
        """A first week has nothing to be twice as much as."""
        from monitor.usage_ledger import burn_summary
        _ledger(tmp_path, [_call(d + 0.5) for d in range(3)])
        assert burn_summary(7)["ratio"] is None

    def test_usage_is_attributed_to_its_source(self, tmp_path):
        from monitor.usage_ledger import burn_summary
        _ledger(tmp_path, [_call(1, "script_gen", 9000, 0), _call(1, "title_gen", 1000, 0)])
        assert list(burn_summary(7)["by_source"]) == ["script_gen", "title_gen"]

    def test_older_calls_are_excluded(self, tmp_path):
        from monitor.usage_ledger import burn_summary
        _ledger(tmp_path, [_call(30)])
        assert burn_summary(7)["calls"] == 0

    def test_one_corrupt_line_does_not_hide_the_rest(self, tmp_path):
        from config.settings import settings
        from monitor.usage_ledger import burn_summary
        settings.logs_dir = tmp_path
        (tmp_path / "claude_usage.jsonl").write_text(
            json.dumps(_call(1)) + "\n{ broken\n" + json.dumps(_call(2)) + "\n")
        assert burn_summary(7)["calls"] == 2

    def test_missing_ledger_is_empty_not_an_error(self, tmp_path):
        from config.settings import settings
        from monitor.usage_ledger import burn_summary
        settings.logs_dir = tmp_path / "nothing-here"
        assert burn_summary(7)["calls"] == 0


class TestCostIsNotInvented:
    def test_no_rates_means_no_dollar_figure(self, monkeypatch):
        """
        Prices are not available from the API and change over time. A
        hardcoded table would go stale and produce confident wrong numbers.
        """
        from monitor.usage_ledger import estimated_cost
        monkeypatch.delenv("CLAUDE_COST_PER_MTOK_INPUT", raising=False)
        monkeypatch.delenv("CLAUDE_COST_PER_MTOK_OUTPUT", raising=False)
        assert estimated_cost({"input_tokens": 1_000_000, "output_tokens": 1_000_000}) is None

    def test_supplied_rates_are_applied(self, monkeypatch):
        from monitor.usage_ledger import estimated_cost
        monkeypatch.setenv("CLAUDE_COST_PER_MTOK_INPUT", "3")
        monkeypatch.setenv("CLAUDE_COST_PER_MTOK_OUTPUT", "15")
        assert estimated_cost({"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == 18.0


class TestBurnHealthCheck:
    def test_steady_burn_is_ok(self, tmp_path):
        from monitor import health_report
        _ledger(tmp_path, [_call(d + 0.5) for d in range(14)])
        assert health_report._check_claude_burn()[0] == health_report._OK

    def test_a_doubling_is_flagged(self, tmp_path):
        """What adding a second daily video looks like — and a retry loop too."""
        from monitor import health_report
        entries = [_call(d + 0.5, tin=6000, tout=2400) for d in range(7)]
        entries += [_call(d + 7.5, tin=3000, tout=1200) for d in range(7)]
        _ledger(tmp_path, entries)
        status, _, detail, _ = health_report._check_claude_burn()
        assert status == health_report._WARN
        assert "2.0x" in detail

    def test_an_empty_ledger_is_not_a_failure(self, tmp_path):
        from monitor import health_report
        _ledger(tmp_path, [])
        assert health_report._check_claude_burn()[0] == health_report._OK


class TestOptionalKeys:
    def test_missing_keys_are_reported_with_their_effect(self, monkeypatch):
        """
        Required credentials already fail loudly. These do not: with no key
        the feature disappears, the run still succeeds, and nothing says the
        video came out thinner than intended.
        """
        from monitor import health_report
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        monkeypatch.delenv("MARKETSTACK_API_KEY", raising=False)
        status, _, detail, lines = health_report._check_optional_keys()
        assert status == health_report._WARN
        assert any("PEXELS_API_KEY" in line for line in lines)
        assert any("B-roll" in line for line in lines)

    def test_all_present_is_ok(self, monkeypatch):
        from monitor import health_report
        monkeypatch.setenv("PEXELS_API_KEY", "k")
        monkeypatch.setenv("MARKETSTACK_API_KEY", "k")
        assert health_report._check_optional_keys()[0] == health_report._OK

    def test_blank_counts_as_missing(self, monkeypatch):
        """.env files carry empty assignments; an empty key is not a key."""
        from monitor import health_report
        monkeypatch.setenv("PEXELS_API_KEY", "   ")
        monkeypatch.setenv("MARKETSTACK_API_KEY", "k")
        assert health_report._check_optional_keys()[0] == health_report._WARN


class TestEveryClaudeCallIsCounted:
    def test_no_call_site_is_left_uninstrumented(self):
        """
        A burn number that silently omits a caller is worse than none — it
        reads as authoritative. This fails when a new Claude call is added
        without a record() beside it.
        """
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        missing = []
        for path in root.rglob("*.py"):
            if "test" in path.parts or path.name.startswith("test_"):
                continue
            if path.name in {"health_report.py", "usage_ledger.py"}:
                continue          # the credit probe is a health check, not spend
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            recorded = {
                node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "record"
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                    continue
                func = node.value.func
                owner = getattr(func, "value", None)
                if (isinstance(func, ast.Attribute) and func.attr == "create"
                        and isinstance(owner, ast.Attribute) and owner.attr == "messages"):
                    if node.end_lineno + 1 not in recorded:
                        missing.append(f"{path.relative_to(root)}:{node.lineno}")
        assert not missing, f"Claude calls with no record(): {missing}"
