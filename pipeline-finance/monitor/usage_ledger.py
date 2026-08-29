"""
monitor/usage_ledger.py — what the pipeline is spending on Claude.

Every call site already had the token counts and threw them away, so the
health report could say "credit exists" but never "credit is draining
twice as fast as last week". Running out of credit stops the channel as
completely as a dead daemon does, and it does it just as quietly.

Deliberately reports tokens rather than dollars. Per-model prices change
and are not available from the API, so a hardcoded price table would
quietly go stale and produce confident, wrong numbers. Tokens are a fact
this process observes directly; set CLAUDE_COST_PER_MTOK_* if you want
the ledger to convert them.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

LEDGER_NAME = "claude_usage.jsonl"

# A week's burn compared against the week before it. Doubling is the signal
# that matters here: it is what adding a second daily video looks like, and
# what a runaway retry loop looks like too.
BURN_ALERT_RATIO = 2.0


def _ledger_path() -> Path:
    return settings.logs_dir / LEDGER_NAME


def record(response, source: str) -> None:
    """
    Append one Claude call's usage to the ledger.

    Never raises and never blocks the caller: bookkeeping must not be able
    to fail a pipeline run. Callers add one line and can ignore the result.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        entry = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "model": getattr(response, "model", None) or settings.claude_model,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        path = _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("Usage not recorded for %s (non-fatal): %s", source, exc)


def _entries(since: datetime, until: "datetime | None" = None) -> list:
    path = _ledger_path()
    if not path.exists():
        return []
    found = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                when = datetime.fromisoformat(entry["at"])
            except Exception:
                continue        # one bad line must not hide the rest
            if when >= since and (until is None or when < until):
                found.append(entry)
    except Exception as exc:
        logger.warning("Usage ledger unreadable: %s", exc)
    return found


def burn_summary(days: int = 7) -> dict:
    """Totals over the last N days, and the same window before it."""
    now = datetime.now()
    window = _entries(now - timedelta(days=days))
    previous = _entries(now - timedelta(days=days * 2), now - timedelta(days=days))

    def totals(entries: list) -> dict:
        return {
            "calls": len(entries),
            "input_tokens": sum(e.get("input_tokens", 0) for e in entries),
            "output_tokens": sum(e.get("output_tokens", 0) for e in entries),
        }

    current, prior = totals(window), totals(previous)
    current["tokens"] = current["input_tokens"] + current["output_tokens"]
    prior["tokens"] = prior["input_tokens"] + prior["output_tokens"]
    current["tokens_per_day"] = round(current["tokens"] / max(days, 1))
    current["days"] = days
    current["previous_tokens"] = prior["tokens"]
    # Only meaningful with a prior window to compare against.
    current["ratio"] = (
        round(current["tokens"] / prior["tokens"], 2) if prior["tokens"] else None
    )
    by_source: dict[str, int] = {}
    for entry in window:
        by_source[entry.get("source", "?")] = by_source.get(entry.get("source", "?"), 0) + \
            entry.get("input_tokens", 0) + entry.get("output_tokens", 0)
    current["by_source"] = dict(sorted(by_source.items(), key=lambda kv: -kv[1]))
    return current


def estimated_cost(summary: dict) -> "float | None":
    """
    Dollars, but only when someone has supplied the rates. Prices are not
    available from the API and change over time, so the ledger will not
    invent them.
    """
    try:
        per_in = float(os.getenv("CLAUDE_COST_PER_MTOK_INPUT", "") or 0)
        per_out = float(os.getenv("CLAUDE_COST_PER_MTOK_OUTPUT", "") or 0)
    except ValueError:
        return None
    if not per_in and not per_out:
        return None
    return round(summary.get("input_tokens", 0) / 1_000_000 * per_in
                 + summary.get("output_tokens", 0) / 1_000_000 * per_out, 2)
