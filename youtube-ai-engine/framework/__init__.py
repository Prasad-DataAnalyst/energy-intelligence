"""
framework/ — Self-Modification & Auto-Update Framework
=======================================================
Safe, tested, and reversible code self-modification.

Components:
  code_validator  — AST-based safety validation before any patch is applied
  patch_manager   — Applies patches with test-gate and rollback
  error_corrector — AI-powered error diagnosis and fix generation
  update_pipeline — Orchestrates full automated update cycles
"""
from framework.code_validator import CodeValidator, ValidationResult
from framework.patch_manager import PatchManager, Patch, PatchResult
from framework.error_corrector import ErrorCorrector, CorrectionResult
from framework.update_pipeline import UpdatePipeline, UpdateReport, UpdateIssue

__all__ = [
    "CodeValidator", "ValidationResult",
    "PatchManager", "Patch", "PatchResult",
    "ErrorCorrector", "CorrectionResult",
    "UpdatePipeline", "UpdateReport", "UpdateIssue",
]
