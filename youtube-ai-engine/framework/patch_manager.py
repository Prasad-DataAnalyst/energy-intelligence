"""
framework/patch_manager.py — Safe Code Patch Manager
=====================================================
Applies AI-generated code fixes with a full safety pipeline:

  1. Validate with CodeValidator (AST safety check)
  2. Create backup (write original_source to a timestamped temp file)
  3. Apply patch to ``{file_path}.pending``
  4. Syntax check (py_compile)
  5. Run test suite (timeout 120 s)
  6. If tests pass → rename .pending → real file, log to memory
  7. If tests fail → restore backup, log failure
  8. Full rollback via rollback(patch_id)

All patches are logged with patch_id for traceability.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import py_compile
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from framework.code_validator import CodeValidator, ValidationResult

log = logging.getLogger("brain.patch_manager")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Patch:
    """Describes a single proposed code change."""

    file_path: str           # relative to project root
    original_source: str     # current on-disk content
    patched_source: str      # proposed replacement
    description: str         # human-readable summary
    source: str              # "error_corrector" | "code_auditor" | "manual"
    patch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Backwards-compat alias used by the old API (update_pipeline reads new_source)
    @property
    def new_source(self) -> str:  # noqa: D401
        return self.patched_source

    @property
    def abs_path(self) -> Path:
        return PatchManager.PROJECT_ROOT / self.file_path


@dataclass
class PatchResult:
    """Records what happened when a Patch was applied."""

    patch_id: str
    success: bool
    applied: bool
    tests_passed: bool
    validation_errors: List[str]
    error_message: str
    duration_s: float
    rolled_back: bool

    # Legacy attribute used by the old update_pipeline
    @property
    def message(self) -> str:
        return self.error_message

    @property
    def errors(self) -> List[str]:
        return self.validation_errors

    def to_dict(self) -> Dict:
        return {
            "patch_id":          self.patch_id,
            "success":           self.success,
            "applied":           self.applied,
            "tests_passed":      self.tests_passed,
            "validation_errors": self.validation_errors,
            "error_message":     self.error_message,
            "duration_s":        round(self.duration_s, 3),
            "rolled_back":       self.rolled_back,
        }


# ── PatchManager ──────────────────────────────────────────────────────────────

class PatchManager:
    """
    Safe, reversible code patching with a mandatory test-gate.

    Parameters
    ----------
    memory:
        Optional ``core.memory.Memory`` instance for persistence.
    run_tests:
        When True (default) the full test suite is executed after a patch
        is staged.  Set to False in unit-testing contexts.
    test_timeout_s:
        Maximum seconds allowed for the test suite (default 120).
    """

    PROJECT_ROOT: Path = Path(__file__).parent.parent

    def __init__(
        self,
        memory=None,
        run_tests: bool = True,
        test_timeout_s: int = 120,
        # Legacy kwargs forwarded by UpdatePipeline (silently accepted)
        project_root: Optional[Path] = None,
        test_command: Optional[str] = None,
        auto_commit: bool = False,
    ) -> None:
        self.memory = memory
        self.run_tests = run_tests
        self.test_timeout_s = test_timeout_s
        if project_root is not None:
            PatchManager.PROJECT_ROOT = project_root
        # In-memory index: patch_id → backup file path
        self._backups: Dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def apply(self, patch: Patch) -> PatchResult:
        """
        Run the full safety pipeline for *patch* and return a PatchResult.

        Pipeline
        --------
        1. CodeValidator check
        2. Write backup
        3. Write ``.pending`` file
        4. py_compile check
        5. Optionally run test suite
        6. Promote pending → real, or rollback
        """
        t0 = time.monotonic()

        # Support old-style Patch objects that only have new_source
        if not hasattr(patch, "patched_source"):
            # Wrap legacy Patch into our Patch dataclass
            orig = ""
            real = self._resolve(patch.file_path)
            if real.exists():
                try:
                    orig = real.read_text(encoding="utf-8")
                except OSError:
                    pass
            patch = Patch(
                file_path=patch.file_path,
                original_source=orig,
                patched_source=getattr(patch, "new_source", ""),
                description=getattr(patch, "description", ""),
                source=getattr(patch, "author", "manual"),
            )

        # ── Step 1: validate ─────────────────────────────────────────────────
        vr: ValidationResult = CodeValidator().validate(
            patch.patched_source,
            original_source=patch.original_source,
            filename=patch.file_path,
        )
        if not vr.valid:
            log.warning(
                "[PatchManager] Patch %s rejected by validator: %s",
                patch.patch_id, vr.errors,
            )
            result = PatchResult(
                patch_id=patch.patch_id,
                success=False,
                applied=False,
                tests_passed=False,
                validation_errors=list(vr.errors),
                error_message="CodeValidator rejected patch",
                duration_s=time.monotonic() - t0,
                rolled_back=False,
            )
            self._log_patch(patch, result)
            return result

        real_path = self._resolve(patch.file_path)
        pending_path = Path(str(real_path) + ".pending")

        # ── Step 2: backup ───────────────────────────────────────────────────
        self._write_backup(patch)

        # ── Step 3: write pending ────────────────────────────────────────────
        try:
            real_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(patch.patched_source, encoding="utf-8")
        except OSError as exc:
            log.error("[PatchManager] Could not write pending file %s: %s",
                      pending_path, exc)
            result = PatchResult(
                patch_id=patch.patch_id,
                success=False,
                applied=False,
                tests_passed=False,
                validation_errors=[],
                error_message=f"Could not write pending file: {exc}",
                duration_s=time.monotonic() - t0,
                rolled_back=False,
            )
            self._log_patch(patch, result)
            return result

        # ── Step 4: py_compile ───────────────────────────────────────────────
        try:
            py_compile.compile(str(pending_path), doraise=True)
        except py_compile.PyCompileError as exc:
            log.warning("[PatchManager] py_compile failed for %s: %s",
                        pending_path, exc)
            pending_path.unlink(missing_ok=True)
            result = PatchResult(
                patch_id=patch.patch_id,
                success=False,
                applied=False,
                tests_passed=False,
                validation_errors=[],
                error_message=f"py_compile error: {exc}",
                duration_s=time.monotonic() - t0,
                rolled_back=False,
            )
            self._log_patch(patch, result)
            return result

        # ── Step 5: test suite ───────────────────────────────────────────────
        tests_passed = True
        test_output = ""

        if self.run_tests:
            # Temporarily promote pending → real so tests import the new code
            pre_test = real_path.with_suffix(".py.pre_test")
            if real_path.exists():
                shutil.copy2(real_path, pre_test)
            shutil.move(str(pending_path), str(real_path))

            tests_passed, test_output = self._run_tests(self.test_timeout_s)

            if not tests_passed:
                # Restore pre-test file
                if pre_test.exists():
                    shutil.move(str(pre_test), str(real_path))
                else:
                    backup_p = self._backups.get(patch.patch_id)
                    if backup_p and Path(backup_p).exists():
                        shutil.copy2(backup_p, real_path)
                pending_path.unlink(missing_ok=True)

                log.warning(
                    "[PatchManager] Tests FAILED for patch %s — rolled back. "
                    "Output: %s",
                    patch.patch_id, test_output[-300:],
                )
                result = PatchResult(
                    patch_id=patch.patch_id,
                    success=False,
                    applied=False,
                    tests_passed=False,
                    validation_errors=[],
                    error_message=f"Test suite failed: {test_output[-200:]}",
                    duration_s=time.monotonic() - t0,
                    rolled_back=True,
                )
                self._log_patch(patch, result)
                return result

            # Tests passed — clean up temporary artefacts
            pre_test.unlink(missing_ok=True)
        else:
            # No test run — just promote pending → real
            shutil.move(str(pending_path), str(real_path))

        # ── Step 6: success ──────────────────────────────────────────────────
        log.info("[PatchManager] Patch %s applied successfully to %s",
                 patch.patch_id, patch.file_path)
        result = PatchResult(
            patch_id=patch.patch_id,
            success=True,
            applied=True,
            tests_passed=tests_passed,
            validation_errors=[],
            error_message="",
            duration_s=time.monotonic() - t0,
            rolled_back=False,
        )
        self._log_patch(patch, result)
        return result

    def apply_many(self, patches: List[Patch]) -> List[PatchResult]:
        """Apply patches sequentially; stop on first failure."""
        results: List[PatchResult] = []
        for patch in patches:
            pr = self.apply(patch)
            results.append(pr)
            if not pr.success:
                log.warning(
                    "[PatchManager] Stopping batch after failure on %s",
                    patch.file_path,
                )
                break
        return results

    def rollback(self, patch_id: str) -> bool:
        """
        Restore the backup saved under *patch_id*.

        Returns True if the backup was found and restored, False otherwise.
        """
        backup_path = self._backups.get(patch_id)
        if not backup_path or not Path(backup_path).exists():
            log.warning("[PatchManager] No backup found for patch_id=%s", patch_id)
            return False
        try:
            # Backup naming: <real_path>.patch_<uuid>.bak
            bak = Path(backup_path)
            real_path_str = str(bak).replace(f".patch_{patch_id}.bak", "")
            real_path = Path(real_path_str)
            shutil.copy2(backup_path, real_path)
            log.info("[PatchManager] Rolled back patch %s → %s", patch_id, real_path)
            return True
        except Exception as exc:
            log.error("[PatchManager] Rollback failed for patch_id=%s: %s",
                      patch_id, exc)
            return False

    def get_history(self, n: int = 20) -> List[Dict]:
        """Return the last *n* patch records from memory."""
        if self.memory is None:
            return []
        try:
            return self.memory.get_patch_history(n=n)
        except AttributeError:
            pass
        try:
            # Fallback: return recent upgrades as a proxy
            rows = self.memory.upgrade_summary()
            return [rows] if rows else []
        except Exception:
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _resolve(self, relative_path: str) -> Path:
        p = Path(relative_path)
        return p if p.is_absolute() else self.PROJECT_ROOT / relative_path

    def _write_backup(self, patch: Patch) -> Optional[str]:
        """Write original_source to a .bak file; record in self._backups."""
        real_path = self._resolve(patch.file_path)
        backup_path = Path(str(real_path) + f".patch_{patch.patch_id}.bak")
        try:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(patch.original_source, encoding="utf-8")
            self._backups[patch.patch_id] = str(backup_path)
            return str(backup_path)
        except OSError as exc:
            log.warning("[PatchManager] Could not write backup for patch %s: %s",
                        patch.patch_id, exc)
            return None

    def _run_tests(self, timeout_s: int) -> Tuple[bool, str]:
        """
        Run ``python -m unittest tests.auto_test`` from the project root.

        Returns (passed, output_summary).
        """
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.auto_test"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.PROJECT_ROOT),
            )
            output = (result.stdout + result.stderr)[-1000:]
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            log.warning("[PatchManager] Test suite timed out after %d s", timeout_s)
            return False, f"Test suite timed out after {timeout_s}s"
        except Exception as exc:
            log.error("[PatchManager] Could not run test suite: %s", exc)
            return False, str(exc)

    def _log_patch(self, patch: Patch, result: PatchResult) -> None:
        """Persist the patch result to memory (if available)."""
        if self.memory is None:
            log.debug("[PatchManager] patch_id=%s file=%s success=%s",
                      result.patch_id, patch.file_path, result.success)
            return

        details = json.dumps({
            "file_path":         patch.file_path,
            "description":       patch.description,
            "source":            patch.source,
            "applied":           result.applied,
            "tests_passed":      result.tests_passed,
            "validation_errors": result.validation_errors,
            "error_message":     result.error_message,
            "duration_s":        round(result.duration_s, 3),
            "rolled_back":       result.rolled_back,
            "created_at":        patch.created_at.isoformat(),
        })

        # Try dedicated method first; fall back to log_upgrade.
        try:
            self.memory.log_patch_result(
                patch_id=result.patch_id,
                file_path=patch.file_path,
                success=result.success,
                description=patch.description,
                details=details,
            )
            return
        except AttributeError:
            pass

        try:
            orig_hash = hashlib.sha256(
                patch.original_source.encode()
            ).hexdigest()[:16]
            patch_hash = hashlib.sha256(
                patch.patched_source.encode()
            ).hexdigest()[:16]
            self.memory.log_upgrade(
                file_path=patch.file_path,
                reason=f"patch:{patch.source}:{patch.patch_id[:8]}",
                original_hash=orig_hash,
                improved_hash=patch_hash,
                test_passed=result.tests_passed,
                commit_sha=result.patch_id[:8],
            )
        except Exception as exc:
            log.warning("[PatchManager] Could not log patch to memory: %s", exc)
