"""
framework/update_pipeline.py — Automated Update Pipeline
=========================================================
Runs scheduled code improvement cycles that are safe, tested,
and fully reversible.

Daily update cycle (runs at 03:30 UTC):
  1. Collect pending error corrections from memory
  2. Run CodeAuditor for quality/performance issues
  3. Prioritize: crash_fix > security > performance > quality
  4. Apply patches one at a time (stop on first test failure)
  5. Generate and persist UpdateReport
  6. Publish summary event

Manual trigger: python main.py --phase update
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.update_pipeline")

HERE = Path(__file__).parent.parent

# Priority map for issue types (lower number = higher priority)
_PRIORITY: Dict[str, int] = {
    "crash_fix":   1,
    "security":    2,
    "performance": 3,
    "quality":     4,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class UpdateIssue:
    """Represents a single improvement opportunity."""

    issue_id: str
    issue_type: str   # "crash_fix"|"security"|"performance"|"quality"
    file_path: str
    description: str
    priority: int     # 1 = highest
    source: str       # "error_log"|"code_auditor"|"health_monitor"


@dataclass
class UpdateReport:
    """Summary of one complete update cycle run."""

    run_id: str
    started_at: datetime
    completed_at: datetime
    issues_found: int
    patches_attempted: int
    patches_applied: int
    patches_failed: int
    issues: List[UpdateIssue] = field(default_factory=list)
    patch_results: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "run_id":            self.run_id,
            "started_at":        self.started_at.isoformat(),
            "completed_at":      self.completed_at.isoformat(),
            "issues_found":      self.issues_found,
            "patches_attempted": self.patches_attempted,
            "patches_applied":   self.patches_applied,
            "patches_failed":    self.patches_failed,
            "issues":            [
                {
                    "issue_id":    i.issue_id,
                    "issue_type":  i.issue_type,
                    "file_path":   i.file_path,
                    "description": i.description,
                    "priority":    i.priority,
                    "source":      i.source,
                }
                for i in self.issues
            ],
            "patch_results": self.patch_results,
        }


# ── UpdatePipeline ────────────────────────────────────────────────────────────

class UpdatePipeline:
    """
    Orchestrates full automated update cycles.

    Parameters
    ----------
    memory:
        Optional ``core.memory.Memory`` instance.
    dry_run:
        When True, issues are collected and reported but no patches are applied.
    """

    MAX_PATCHES_PER_CYCLE: int = 5

    def __init__(
        self,
        memory=None,
        dry_run: bool = False,
        # Legacy kwargs (silently accepted for backwards compat)
        project_root: Optional[Path] = None,
        test_command: Optional[str] = None,
        auto_commit: bool = False,
        stop_on_first_failure: bool = True,
    ) -> None:
        self.memory = memory
        self.dry_run = dry_run
        self._stop_on_first_failure = stop_on_first_failure
        self._last_report: Optional[Dict] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def run_update_cycle(self) -> UpdateReport:
        """
        Execute a full update cycle.

        1. Collect issues (crash_fix from memory, quality from code_auditor)
        2. Sort by priority
        3. Apply up to MAX_PATCHES_PER_CYCLE patches via ErrorCorrector
        4. Stop if a patch fails tests
        5. Log report to memory; publish event
        6. Return UpdateReport
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        log.info("[UpdatePipeline] Starting update cycle run_id=%s dry_run=%s",
                 run_id, self.dry_run)

        # ── Collect issues ────────────────────────────────────────────────
        issues = self.get_pending_issues()
        issues.sort(key=lambda i: i.priority)
        log.info("[UpdatePipeline] %d issues found", len(issues))

        patches_attempted = 0
        patches_applied = 0
        patches_failed = 0
        patch_results: List[Dict] = []

        if not self.dry_run:
            ec = self._get_error_corrector()
            pm = self._get_patch_manager()

            for issue in issues[: self.MAX_PATCHES_PER_CYCLE]:
                patches_attempted += 1
                log.info(
                    "[UpdatePipeline] Addressing issue %s (%s) in %s",
                    issue.issue_id, issue.issue_type, issue.file_path,
                )

                # Build a minimal Patch from the issue description
                patch = self._issue_to_patch(issue)
                if patch is None:
                    log.info(
                        "[UpdatePipeline] Cannot build patch for issue %s "
                        "(no Claude or no source) — skipping",
                        issue.issue_id,
                    )
                    patches_failed += 1
                    patch_results.append({
                        "issue_id": issue.issue_id,
                        "success": False,
                        "reason": "no_patch_generated",
                    })
                    continue

                result = pm.apply(patch)
                pr_dict = result.to_dict()
                pr_dict["issue_id"] = issue.issue_id
                patch_results.append(pr_dict)

                if result.success:
                    patches_applied += 1
                    log.info(
                        "[UpdatePipeline] Patch applied for issue %s", issue.issue_id
                    )
                else:
                    patches_failed += 1
                    log.warning(
                        "[UpdatePipeline] Patch FAILED for issue %s: %s",
                        issue.issue_id, result.error_message,
                    )
                    if self._stop_on_first_failure:
                        log.warning(
                            "[UpdatePipeline] Stopping cycle after first failure "
                            "(system may be unstable)"
                        )
                        break

        completed_at = datetime.now(timezone.utc)
        report = UpdateReport(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            issues_found=len(issues),
            patches_attempted=patches_attempted,
            patches_applied=patches_applied,
            patches_failed=patches_failed,
            issues=issues,
            patch_results=patch_results,
        )

        self._last_report = report.to_dict()

        # ── Log to memory ─────────────────────────────────────────────────
        if self.memory:
            try:
                self.memory.log_upgrade(
                    file_path="update_pipeline",
                    reason=f"update_cycle:{run_id[:8]}",
                    original_hash="",
                    improved_hash="",
                    test_passed=(patches_failed == 0),
                    commit_sha=run_id[:8],
                )
            except Exception as exc:
                log.warning("[UpdatePipeline] Could not log to memory: %s", exc)

        # ── Publish event ─────────────────────────────────────────────────
        self._publish("update.cycle_completed", report.to_dict())

        log.info(
            "[UpdatePipeline] Cycle complete: %d/%d patches applied, "
            "%d failed, duration=%.1fs",
            patches_applied,
            patches_attempted,
            patches_failed,
            (completed_at - started_at).total_seconds(),
        )
        return report

    def get_pending_issues(self) -> List[UpdateIssue]:
        """Collect all pending issues from memory and health monitor."""
        issues: List[UpdateIssue] = []
        issues.extend(self._collect_crash_fixes())
        issues.extend(self._collect_quality_issues())
        # De-duplicate by file_path + issue_type
        seen: set = set()
        deduped: List[UpdateIssue] = []
        for issue in issues:
            key = (issue.file_path, issue.issue_type)
            if key not in seen:
                seen.add(key)
                deduped.append(issue)
        return deduped

    def apply_single_issue(self, issue_id: str) -> "PatchResult":
        """Apply the patch for one specific issue by issue_id."""
        from framework.patch_manager import PatchResult  # noqa: PLC0415

        issues = self.get_pending_issues()
        matching = [i for i in issues if i.issue_id == issue_id]
        if not matching:
            log.warning("[UpdatePipeline] No issue found with id %s", issue_id)
            return PatchResult(
                patch_id="",
                success=False,
                applied=False,
                tests_passed=False,
                validation_errors=[],
                error_message=f"Issue {issue_id} not found",
                duration_s=0.0,
                rolled_back=False,
            )

        patch = self._issue_to_patch(matching[0])
        if patch is None:
            return PatchResult(
                patch_id="",
                success=False,
                applied=False,
                tests_passed=False,
                validation_errors=[],
                error_message="Could not generate patch",
                duration_s=0.0,
                rolled_back=False,
            )
        return self._get_patch_manager().apply(patch)

    def get_last_report(self) -> Optional[Dict]:
        """Return the most recent UpdateReport as a dict, or None."""
        return self._last_report

    # ── Legacy compatibility: run(patches) ───────────────────────────────────

    def run(self, patches: "List[Patch]") -> "PipelineRun":  # type: ignore[name-defined]
        """
        Legacy API — apply a pre-built list of Patch objects.

        Returns a ``PipelineRun``-compatible dict-based object.
        """
        from framework.patch_manager import PatchManager  # noqa: PLC0415

        @dataclass
        class PipelineRun:
            started_at: datetime = field(
                default_factory=lambda: datetime.now(timezone.utc)
            )
            finished_at: Optional[datetime] = None
            total: int = 0
            succeeded: int = 0
            failed: int = 0
            skipped: int = 0
            results: List = field(default_factory=list)

            @property
            def success_rate(self) -> float:
                return self.succeeded / self.total if self.total else 0.0

            def to_dict(self) -> Dict:
                return {
                    "started_at":   self.started_at.isoformat(),
                    "finished_at":  self.finished_at.isoformat()
                                    if self.finished_at else None,
                    "total":        self.total,
                    "succeeded":    self.succeeded,
                    "failed":       self.failed,
                    "skipped":      self.skipped,
                    "success_rate": round(self.success_rate, 3),
                    "results": [
                        r.to_dict() if hasattr(r, "to_dict") else r
                        for r in self.results
                    ],
                }

        pm = PatchManager(memory=self.memory, run_tests=not self.dry_run)
        run = PipelineRun(total=len(patches))
        for patch in patches:
            result = pm.apply(patch)
            run.results.append(result)
            if result.success:
                run.succeeded += 1
            else:
                run.failed += 1
                if self._stop_on_first_failure:
                    idx = patches.index(patch) + 1
                    run.skipped = len(patches) - idx
                    break
        run.finished_at = datetime.now(timezone.utc)
        self._publish("pipeline.run_complete", run.to_dict())
        return run

    def dry_run_check(self, patches: "List[Patch]") -> List[Dict]:
        """Validate patches without applying them."""
        from framework.code_validator import CodeValidator  # noqa: PLC0415
        validator = CodeValidator()
        summaries = []
        for patch in patches:
            original = ""
            abs_path = HERE / patch.file_path
            if abs_path.exists():
                try:
                    original = abs_path.read_text(encoding="utf-8")
                except OSError:
                    pass
            new_src = getattr(patch, "patched_source",
                               getattr(patch, "new_source", ""))
            vr = validator.validate(new_src, original_source=original,
                                    filename=patch.file_path)
            summaries.append({
                "file_path": patch.file_path,
                "valid":     vr.valid,
                "errors":    vr.errors,
                "warnings":  vr.warnings,
            })
        return summaries

    # ── Issue collectors ──────────────────────────────────────────────────────

    def _collect_crash_fixes(self) -> List[UpdateIssue]:
        """Pull unresolved errors from memory as crash_fix issues."""
        issues: List[UpdateIssue] = []
        if self.memory is None:
            return issues
        try:
            errors = self.memory.get_unfixed_errors()
        except Exception:
            return issues

        for err in errors[:10]:
            file_path = err.get("file_path", "")
            if not file_path or not Path(HERE / file_path).exists():
                continue
            issues.append(
                UpdateIssue(
                    issue_id=str(uuid.uuid4()),
                    issue_type="crash_fix",
                    file_path=file_path,
                    description=(
                        f"{err.get('error_type','Error')}: "
                        f"{err.get('error_msg','')[:120]}"
                    ),
                    priority=_PRIORITY["crash_fix"],
                    source="error_log",
                )
            )
        return issues

    def _collect_quality_issues(self) -> List[UpdateIssue]:
        """Ask CodeAuditor for quality suggestions (non-destructive scan)."""
        issues: List[UpdateIssue] = []
        try:
            from evolution.code_auditor import CodeAuditor  # noqa: PLC0415
            auditor = CodeAuditor(memory=self.memory)
            py_files = auditor.find_python_files()
            # Take at most 3 files so we don't flood the cycle
            for path in py_files[:3]:
                rel = str(path.relative_to(HERE))
                issues.append(
                    UpdateIssue(
                        issue_id=str(uuid.uuid4()),
                        issue_type="quality",
                        file_path=rel,
                        description=f"Routine quality audit of {path.name}",
                        priority=_PRIORITY["quality"],
                        source="code_auditor",
                    )
                )
        except ImportError:
            pass
        except Exception as exc:
            log.debug("[UpdatePipeline] CodeAuditor scan failed: %s", exc)
        return issues

    # ── Patch generation ──────────────────────────────────────────────────────

    def _issue_to_patch(self, issue: UpdateIssue) -> Optional["Patch"]:
        """Generate a Patch for *issue* using ErrorCorrector (if possible)."""
        from framework.patch_manager import Patch  # noqa: PLC0415

        abs_path = HERE / issue.file_path
        if not abs_path.exists():
            return None

        try:
            original_source = abs_path.read_text(encoding="utf-8")
        except OSError:
            return None

        # For quality issues use CodeAuditor; for crash_fix use ErrorCorrector
        if issue.issue_type == "quality":
            fixed_source = self._audit_file(abs_path, original_source)
        else:
            fixed_source = self._correct_error(
                issue.file_path, original_source, issue.description
            )

        if not fixed_source or fixed_source == original_source:
            return None

        return Patch(
            file_path=issue.file_path,
            original_source=original_source,
            patched_source=fixed_source,
            description=issue.description,
            source="update_pipeline",
        )

    def _audit_file(self, path: Path, original_source: str) -> Optional[str]:
        """Use CodeAuditor to improve a file; return improved source or None."""
        try:
            from evolution.code_auditor import CodeAuditor  # noqa: PLC0415
            auditor = CodeAuditor(memory=self.memory)
            improved = auditor.audit_file(path)
            if improved and auditor._meaningful_change(original_source, improved):
                return improved
        except Exception as exc:
            log.debug("[UpdatePipeline] _audit_file failed: %s", exc)
        return None

    def _correct_error(
        self, file_path: str, original_source: str, description: str
    ) -> Optional[str]:
        """Use ErrorCorrector to fix an error; return patched source or None."""
        import os
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            return None
        try:
            ec = self._get_error_corrector()
            result = ec.diagnose_and_fix(
                error=RuntimeError(description),
                module_name=Path(file_path).stem,
                file_path=file_path,
                traceback_str=description,
            )
            if result.corrected:
                # Read back what was written
                abs_path = HERE / file_path
                return abs_path.read_text(encoding="utf-8")
        except Exception as exc:
            log.debug("[UpdatePipeline] _correct_error failed: %s", exc)
        return None

    # ── Lazy component factories ──────────────────────────────────────────────

    def _get_error_corrector(self) -> "ErrorCorrector":
        from framework.error_corrector import ErrorCorrector  # noqa: PLC0415
        return ErrorCorrector(memory=self.memory)

    def _get_patch_manager(self) -> "PatchManager":
        from framework.patch_manager import PatchManager  # noqa: PLC0415
        return PatchManager(memory=self.memory, run_tests=not self.dry_run)

    # ── Event publishing ──────────────────────────────────────────────────────

    def _publish(self, event_type: str, payload: Dict) -> None:
        try:
            from core.event_bus import publish  # noqa: PLC0415
            publish(event_type, payload, source="update_pipeline")
        except ImportError:
            pass
        except Exception as exc:
            log.debug("[UpdatePipeline] Event publish failed: %s", exc)
