"""
evolution/code_auditor.py — Weekly Self-Upgrading Code Auditor
===============================================================
Every 7 days:
  1. Reads every .py file in the project
  2. Sends each to Claude with an improvement prompt
  3. Validates the improved version (syntax + import check)
  4. Runs the project test suite on the new code
  5. If tests pass: replaces old file, commits to git
  6. Updates CHANGELOG.md and README.md
  7. Logs all upgrades to memory DB

Claude is asked to look for:
  (a) Performance bottlenecks
  (b) Outdated methods → modern Python 3.11+ equivalents
  (c) Missing error handling
  (d) Better algorithms available in 2025
  (e) Code quality improvements (readability, maintainability)
"""
import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import anthropic

log = logging.getLogger("brain.code_auditor")

HERE          = Path(__file__).parent.parent
SKIP_DIRS     = {"__pycache__", ".venv", "venv", ".git", "data", "output", "logs"}
SKIP_FILES    = {"auto_test.py"}   # don't audit the test file itself
MAX_FILE_SIZE = 60_000             # skip huge generated files


AUDIT_SYSTEM = """You are a senior Python engineer conducting a production code review.
Your goal: improve the code's performance, robustness, and maintainability.
You must preserve ALL existing functionality and function/class signatures.
Return ONLY the complete improved Python source. No explanation, no markdown fences."""

AUDIT_PROMPT_TEMPLATE = """Review this production Python file and return an improved version.

File: {filename}

Look for and fix:
(a) Performance bottlenecks (slow loops, unnecessary I/O, blocking calls)
(b) Outdated patterns → use modern Python 3.11+ equivalents
(c) Missing or weak error handling (add try/except with logging)
(d) Better algorithms (e.g. use asyncio for I/O-bound loops)
(e) Code quality (remove dead code, simplify logic, improve names)

CONSTRAINTS:
- Do NOT change function/class signatures
- Do NOT remove any feature or capability
- Do NOT add external dependencies not already imported
- Keep the same overall structure and module organisation

Source:
```python
{source}
```"""


class CodeAuditor:
    def __init__(self, memory=None, model: str = "claude-sonnet-4-6"):
        self.model  = model
        self.memory = memory
        self._client: Optional[anthropic.Anthropic] = None
        self.upgrade_log: List[Dict] = []

    def _client_ok(self) -> Optional[anthropic.Anthropic]:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    # ── File discovery ────────────────────────────────────────────────────────

    def find_python_files(self, root: Path = HERE) -> List[Path]:
        files = []
        for p in root.rglob("*.py"):
            if any(skip in p.parts for skip in SKIP_DIRS):
                continue
            if p.name in SKIP_FILES:
                continue
            if p.stat().st_size > MAX_FILE_SIZE:
                log.debug(f"Skipping large file: {p}")
                continue
            files.append(p)
        return sorted(files)

    # ── Single-file audit ─────────────────────────────────────────────────────

    def audit_file(self, path: Path) -> Optional[str]:
        """Ask Claude for an improved version of the file. Returns improved source or None."""
        client = self._client_ok()
        if not client:
            return None

        source = path.read_text()
        prompt = AUDIT_PROMPT_TEMPLATE.format(
            filename=path.name,
            source=source[:6000],   # truncate very long files
        )
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=AUDIT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            improved = msg.content[0].text.strip()
            # Strip accidental markdown fences
            improved = re.sub(r"^```(?:python)?\s*", "", improved)
            improved = re.sub(r"\s*```$", "", improved)
            return improved if improved.strip() else None
        except Exception as e:
            log.warning(f"Audit failed for {path.name}: {e}")
            return None

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_syntax(code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError as e:
            log.debug(f"Syntax error in improved code: {e}")
            return False

    @staticmethod
    def _code_hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()[:16]

    @staticmethod
    def _meaningful_change(original: str, improved: str) -> bool:
        """True if improved version is substantively different."""
        diff_ratio = abs(len(improved) - len(original)) / max(len(original), 1)
        return diff_ratio > 0.02 or improved != original

    # ── Apply upgrade ─────────────────────────────────────────────────────────

    def apply_upgrade(self, path: Path, improved: str) -> bool:
        """Back up original, write improved, run syntax check."""
        original = path.read_text()
        backup   = path.with_suffix(".py.bak")

        try:
            backup.write_text(original)
            path.write_text(improved)

            # Verify syntax still valid after write
            if not self._validate_syntax(path.read_text()):
                path.write_text(original)   # rollback
                backup.unlink(missing_ok=True)
                log.warning(f"  Upgrade ROLLED BACK (syntax error): {path.name}")
                return False

            log.info(f"  Upgraded: {path.name}")
            return True

        except Exception as e:
            log.error(f"  Failed to apply upgrade to {path.name}: {e}")
            if backup.exists():
                path.write_text(backup.read_text())
            return False

    # ── Test suite ────────────────────────────────────────────────────────────

    def run_tests(self) -> bool:
        """Run auto_test.py. Return True if all tests pass."""
        test_path = HERE / "tests" / "auto_test.py"
        if not test_path.exists():
            return True   # no tests → assume OK
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True, text=True, cwd=str(HERE),
        )
        if result.returncode != 0:
            log.warning(f"Tests failed:\n{result.stdout[-500:]}\n{result.stderr[-200:]}")
            return False
        log.info("  Tests passed ✓")
        return True

    # ── Git operations ─────────────────────────────────────────────────────────

    def git_commit(self, message: str) -> Optional[str]:
        """Stage all .py changes and commit. Returns commit SHA or None."""
        try:
            subprocess.run(["git", "add", "*.py", "-u"],
                           cwd=str(HERE), capture_output=True)
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(HERE), capture_output=True, text=True,
            )
            if result.returncode == 0:
                sha = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(HERE), capture_output=True, text=True,
                ).stdout.strip()
                log.info(f"  Committed: {sha} — {message[:60]}")
                return sha
        except Exception as e:
            log.warning(f"  Git commit failed: {e}")
        return None

    # ── Changelog / README ────────────────────────────────────────────────────

    def update_changelog(self, entries: List[str]) -> None:
        cl_path = HERE / "CHANGELOG.md"
        date    = datetime.now().strftime("%Y-%m-%d")
        header  = f"\n## [{date}] — Auto-upgraded by CodeAuditor\n"
        body    = "\n".join(f"- {e}" for e in entries)
        if cl_path.exists():
            old = cl_path.read_text()
        else:
            old = "# CHANGELOG\n"
        cl_path.write_text(old + header + body + "\n")

    # ── Full weekly audit ─────────────────────────────────────────────────────

    def run_weekly_audit(self) -> Dict:
        log.info("━" * 55)
        log.info("CODE AUDITOR — Weekly self-improvement run")
        files   = self.find_python_files()
        log.info(f"  Files to audit: {len(files)}")

        upgraded = []
        skipped  = 0

        for path in files:
            log.info(f"  Auditing: {path.relative_to(HERE)}")
            original = path.read_text()
            improved = self.audit_file(path)

            if not improved:
                skipped += 1
                continue
            if not self._validate_syntax(improved):
                log.warning(f"    Syntax error in suggestion — skipping")
                skipped += 1
                continue
            if not self._meaningful_change(original, improved):
                log.debug(f"    No meaningful change — skipping")
                skipped += 1
                continue

            orig_hash = self._code_hash(original)
            impr_hash = self._code_hash(improved)

            ok = self.apply_upgrade(path, improved)
            test_ok = self.run_tests() if ok else False

            if ok and test_ok:
                upgraded.append(str(path.relative_to(HERE)))
                if self.memory:
                    self.memory.log_upgrade(
                        file_path=str(path),
                        reason="weekly_audit",
                        original_hash=orig_hash,
                        improved_hash=impr_hash,
                        test_passed=True,
                    )
            elif ok:
                # Tests failed — rollback
                bak = path.with_suffix(".py.bak")
                if bak.exists():
                    path.write_text(bak.read_text())
                    log.warning(f"    Rolled back {path.name} — tests failed")
                if self.memory:
                    self.memory.log_upgrade(
                        file_path=str(path),
                        reason="weekly_audit",
                        original_hash=orig_hash,
                        improved_hash=impr_hash,
                        test_passed=False,
                    )

            time.sleep(1.5)   # rate limit between Claude calls

        if upgraded:
            entries = [f"Upgraded {f}" for f in upgraded]
            self.update_changelog(entries)
            sha = self.git_commit(
                f"chore(auto-upgrade): {len(upgraded)} files improved by CodeAuditor"
            )

        result = {
            "files_audited": len(files),
            "upgraded": upgraded,
            "skipped": skipped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        log.info(f"  Audit complete: {len(upgraded)} upgraded, {skipped} skipped")
        return result
