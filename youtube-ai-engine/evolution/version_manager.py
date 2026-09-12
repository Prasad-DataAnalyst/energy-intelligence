"""
evolution/version_manager.py — Git-Based Auto Version Control
=============================================================
Manages automatic version snapshots of the entire ai-engine codebase:
  - Takes git snapshots every 6 hours
  - Tags significant milestones (first 100 videos, code audits)
  - Maintains a CHANGELOG.md with auto-generated entries
  - Rolls back to last-known-good on catastrophic failure
"""
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("brain.version_manager")

HERE = Path(__file__).parent.parent


class VersionManager:
    def __init__(self, repo_dir: Path = HERE):
        self.repo = repo_dir

    def _git(self, *args, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            check=check,
        )

    # ── Status ────────────────────────────────────────────────────────────

    def has_changes(self) -> bool:
        r = self._git("status", "--porcelain")
        return bool(r.stdout.strip())

    def current_sha(self) -> str:
        r = self._git("rev-parse", "--short", "HEAD")
        return r.stdout.strip()

    def current_branch(self) -> str:
        r = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return r.stdout.strip()

    # ── Snapshot ──────────────────────────────────────────────────────────

    def snapshot(self, message: str = "") -> Optional[str]:
        """
        Stage all tracked changes and create a commit.
        Returns commit SHA or None if nothing to commit.
        """
        if not self.has_changes():
            log.debug("Version snapshot: no changes to commit")
            return None

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg   = message or f"auto: snapshot {stamp}"

        self._git("add", "-u")
        result = self._git("commit", "-m", msg)

        if result.returncode == 0:
            sha = self.current_sha()
            log.info(f"Version snapshot: {sha} — {msg[:60]}")
            return sha

        log.debug(f"Snapshot commit failed: {result.stderr}")
        return None

    # ── Tags ─────────────────────────────────────────────────────────────

    def tag_milestone(self, tag: str, message: str) -> bool:
        """Create an annotated git tag for a milestone."""
        result = self._git("tag", "-a", tag, "-m", message)
        if result.returncode == 0:
            log.info(f"Tagged milestone: {tag}")
            return True
        log.debug(f"Tag failed: {result.stderr}")
        return False

    def list_tags(self) -> List[str]:
        r = self._git("tag", "--sort=-version:refname")
        return [t.strip() for t in r.stdout.strip().splitlines() if t.strip()]

    # ── Rollback ──────────────────────────────────────────────────────────

    def rollback_to(self, sha_or_tag: str) -> bool:
        """
        Roll back all tracked files to the specified commit.
        Creates a new commit rather than rewriting history.
        """
        log.warning(f"ROLLBACK requested to {sha_or_tag}")
        result = self._git("revert", "--no-commit", f"{sha_or_tag}..HEAD")
        if result.returncode != 0:
            log.error(f"Revert failed: {result.stderr}")
            return False
        commit_result = self._git(
            "commit", "-m",
            f"rollback: reverting to {sha_or_tag} due to failure"
        )
        return commit_result.returncode == 0

    def last_good_sha(self, before_sha: str) -> Optional[str]:
        """Return the commit SHA just before a given bad commit."""
        r = self._git("rev-parse", f"{before_sha}~1")
        return r.stdout.strip() if r.returncode == 0 else None

    # ── Changelog ─────────────────────────────────────────────────────────

    def append_changelog(self, entries: List[str], version: str = "") -> None:
        """Append entries to CHANGELOG.md."""
        cl_path = HERE / "CHANGELOG.md"
        date    = datetime.now().strftime("%Y-%m-%d")
        ver     = version or self.current_sha()
        header  = f"\n## [{date}] {ver}\n"
        body    = "\n".join(f"- {e}" for e in entries)

        if cl_path.exists():
            old = cl_path.read_text()
        else:
            old = "# CHANGELOG\n"

        cl_path.write_text(old + header + body + "\n")
        log.info(f"Changelog updated: {len(entries)} entries")

    # ── Push ──────────────────────────────────────────────────────────────

    def push(self, remote: str = "origin") -> bool:
        """Push current branch to remote."""
        branch = self.current_branch()
        result = self._git("push", "-u", remote, branch)
        if result.returncode == 0:
            log.info(f"Pushed {branch} → {remote}")
            return True
        log.warning(f"Push failed: {result.stderr[-200:]}")
        return False
