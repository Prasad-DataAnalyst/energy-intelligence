"""
framework/error_corrector.py — AI Error Corrector
==================================================
Subscribes to error events, diagnoses failures with Claude Haiku,
generates minimal targeted patches, and submits to PatchManager.

Pipeline per error:
  1. Receive error event (module name, exception, traceback, file_path)
  2. Read source file
  3. Locate the failing function/class in the AST
  4. Build structured prompt for Claude Haiku
  5. Parse suggested fix from response
  6. Submit Patch to PatchManager
  7. Log result (fixed/rejected/failed)

Design principle: generate the SMALLEST possible fix.
Never rewrite whole files. Only change what's broken.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.error_corrector")

HERE = Path(__file__).parent.parent


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class CorrectionResult:
    """Records the outcome of a single error-correction attempt."""

    patch_id: Optional[str]
    corrected: bool
    reason: str     # "fixed"|"no_api_key"|"ai_failed"|"validation_failed"|
                    # "tests_failed"|"skipped"
    description: str


# ── ErrorCorrector ────────────────────────────────────────────────────────────

class ErrorCorrector:
    """
    Diagnoses runtime exceptions with Claude Haiku and submits patches.

    Parameters
    ----------
    memory:
        Optional ``core.memory.Memory`` instance for logging.
    patch_manager:
        Optional pre-constructed ``PatchManager``.  A default one (with
        ``run_tests=True``) is created lazily if omitted.
    """

    MODEL: str = "claude-haiku-4-5-20251001"
    MAX_SOURCE_CHARS: int = 8_000   # truncate large files before sending
    COOLDOWN_S: int = 300           # don't retry same module+error within 5 min

    def __init__(
        self,
        memory=None,
        patch_manager=None,
    ) -> None:
        self.memory = memory
        self._patch_manager = patch_manager
        self._cooldown: Dict[str, float] = {}   # key → last-attempt timestamp
        self._stats: Dict[str, int] = {
            "total_corrections": 0,
            "successful": 0,
            "failed": 0,
        }
        self._subscription_id: Optional[str] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def diagnose_and_fix(
        self,
        error: Exception,
        module_name: str,
        file_path: str,
        traceback_str: str,
        context: Dict = None,
    ) -> CorrectionResult:
        """
        Attempt to diagnose *error* in *file_path* and apply a fix.

        Steps
        -----
        1. Check cooldown (skip if module+error retried within COOLDOWN_S)
        2. Read source from file_path (truncate if > MAX_SOURCE_CHARS)
        3. Call ``_ask_claude()`` for a corrected file
        4. Create Patch, submit to PatchManager
        5. Record cooldown; return CorrectionResult
        """
        self._stats["total_corrections"] += 1
        cooldown_key = f"{module_name}:{type(error).__name__}"

        # ── 1. Cooldown check ────────────────────────────────────────────────
        last = self._cooldown.get(cooldown_key, 0.0)
        if time.monotonic() - last < self.COOLDOWN_S:
            log.debug(
                "[ErrorCorrector] Cooldown active for %s — skipping", cooldown_key
            )
            self._stats["failed"] += 1
            return CorrectionResult(
                patch_id=None,
                corrected=False,
                reason="skipped",
                description=f"Cooldown active for {cooldown_key}",
            )

        # ── 2. Read source ───────────────────────────────────────────────────
        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            abs_path = HERE / file_path

        try:
            source = abs_path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("[ErrorCorrector] Cannot read %s: %s", file_path, exc)
            self._stats["failed"] += 1
            return CorrectionResult(
                patch_id=None,
                corrected=False,
                reason="ai_failed",
                description=f"Cannot read source file: {exc}",
            )

        original_source = source
        if len(source) > self.MAX_SOURCE_CHARS:
            source = source[: self.MAX_SOURCE_CHARS]
            log.debug(
                "[ErrorCorrector] Source truncated to %d chars for %s",
                self.MAX_SOURCE_CHARS, file_path,
            )

        error_type = type(error).__name__
        error_msg = f"{error_type}: {error}"

        # ── 3. Ask Claude ────────────────────────────────────────────────────
        suggested_fix = self._ask_claude(source, traceback_str, error_msg)
        if suggested_fix is None:
            self._stats["failed"] += 1
            # Determine whether it's a missing key or an AI failure
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            reason = "no_api_key" if not api_key else "ai_failed"
            self._cooldown[cooldown_key] = time.monotonic()
            return CorrectionResult(
                patch_id=None,
                corrected=False,
                reason=reason,
                description=f"Claude did not return a fix for {module_name}",
            )

        # ── 4. Parse and create Patch ────────────────────────────────────────
        fixed_code = self._extract_python(suggested_fix)
        if not fixed_code:
            log.warning("[ErrorCorrector] Could not extract Python from response")
            self._stats["failed"] += 1
            self._cooldown[cooldown_key] = time.monotonic()
            return CorrectionResult(
                patch_id=None,
                corrected=False,
                reason="ai_failed",
                description="Could not extract Python code from Claude response",
            )

        from framework.patch_manager import Patch  # avoid circular at module load

        patch = Patch(
            file_path=str(abs_path.relative_to(HERE)) if abs_path.is_absolute() else file_path,
            original_source=original_source,
            patched_source=fixed_code,
            description=f"Auto-fix {error_type} in {module_name}: {str(error)[:120]}",
            source="error_corrector",
        )

        # ── 5. Apply via PatchManager ────────────────────────────────────────
        pm = self._get_patch_manager()
        result = pm.apply(patch)

        # ── 6. Record cooldown ───────────────────────────────────────────────
        self._cooldown[cooldown_key] = time.monotonic()

        if result.success:
            self._stats["successful"] += 1
            log.info(
                "[ErrorCorrector] Fixed %s in %s (patch_id=%s)",
                error_type, module_name, patch.patch_id,
            )
            return CorrectionResult(
                patch_id=patch.patch_id,
                corrected=True,
                reason="fixed",
                description=patch.description,
            )
        else:
            self._stats["failed"] += 1
            reason = "tests_failed" if result.rolled_back else "validation_failed"
            return CorrectionResult(
                patch_id=patch.patch_id,
                corrected=False,
                reason=reason,
                description=result.error_message,
            )

    def subscribe_to_errors(self) -> None:
        """Register ``_on_error_event`` with the global EventBus."""
        try:
            from core.event_bus import subscribe  # noqa: PLC0415
            self._subscription_id = subscribe("error.detected", self._on_error_event)
            log.info("[ErrorCorrector] Subscribed to error.detected events")
        except ImportError:
            log.warning("[ErrorCorrector] EventBus not available — skipping subscription")
        except Exception as exc:
            log.warning("[ErrorCorrector] Could not subscribe to EventBus: %s", exc)

    def get_stats(self) -> Dict:
        """Return correction statistics."""
        return dict(self._stats)

    # ── Legacy compatibility: generate_fix() ─────────────────────────────────

    def generate_fix(
        self,
        file_path: str,
        error_text: str,
        traceback_text: str = "",
        context: str = "",
    ) -> Optional["Patch"]:  # type: ignore[name-defined]  # noqa: F821
        """
        Legacy API — wraps diagnose_and_fix() returning a Patch or None.

        Compatible with callers that used the old ErrorCorrector interface.
        """
        try:
            exc = RuntimeError(error_text)
            result = self.diagnose_and_fix(
                error=exc,
                module_name=Path(file_path).stem,
                file_path=file_path,
                traceback_str=traceback_text,
                context={"extra": context} if context else {},
            )
            if not result.corrected or result.patch_id is None:
                return None
            # Return a minimal Patch-like object
            from framework.patch_manager import Patch  # noqa: PLC0415
            abs_path = HERE / file_path
            try:
                new_src = abs_path.read_text(encoding="utf-8")
            except OSError:
                return None
            return Patch(
                file_path=file_path,
                original_source="",
                patched_source=new_src,
                description=result.description,
                source="error_corrector",
                patch_id=result.patch_id,
            )
        except Exception as exc2:
            log.warning("[ErrorCorrector] generate_fix failed: %s", exc2)
            return None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ask_claude(
        self,
        source: str,
        traceback_str: str,
        error_msg: str,
    ) -> Optional[str]:
        """
        Call Claude Haiku with the error context.

        Returns the raw response text, or None on any failure.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None

        try:
            import anthropic  # noqa: PLC0415
        except ImportError:
            log.warning("[ErrorCorrector] anthropic package not installed")
            return None

        system_msg = (
            "You are a Python debugging expert. "
            "Return ONLY the corrected Python source code for the file. "
            "Preserve all function signatures, class names, and public APIs. "
            "Fix only what is broken."
        )
        user_msg = (
            f"File source:\n{source}\n\n"
            f"Error:\n{traceback_str}\n\n"
            "Return the complete corrected file."
        )

        try:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=self.MODEL,
                max_tokens=4096,
                system=system_msg,
                messages=[{"role": "user", "content": user_msg}],
            )
            if msg.content and hasattr(msg.content[0], "text"):
                return msg.content[0].text.strip()
            return None
        except Exception as exc:
            log.warning("[ErrorCorrector] Claude API call failed: %s", exc)
            return None

    def _extract_python(self, text: str) -> str:
        """
        Extract Python source from *text*.

        Tries fenced code blocks first (``python ... ``),
        then falls back to the raw text.
        """
        # Match ```python ... ``` or ``` ... ```
        m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # If no fences — assume the whole response is code
        stripped = text.strip()
        # Rough heuristic: must contain at least one def/class/import
        if re.search(r"\b(?:def |class |import |from )\b", stripped):
            return stripped
        return ""

    def _on_error_event(self, event) -> None:
        """EventBus handler for ``error.detected`` events."""
        payload = getattr(event, "payload", {}) or {}
        module_name = payload.get("module", "")
        file_path = payload.get("file_path", "")
        traceback_str = payload.get("traceback", "")
        error_msg = payload.get("error", "")

        if not file_path:
            return

        error_obj = RuntimeError(error_msg)
        try:
            self.diagnose_and_fix(
                error=error_obj,
                module_name=module_name,
                file_path=file_path,
                traceback_str=traceback_str,
            )
        except Exception as exc:
            log.warning(
                "[ErrorCorrector] Unhandled error in _on_error_event: %s", exc
            )

    def _get_patch_manager(self):
        """Return the PatchManager, constructing a default one if needed."""
        if self._patch_manager is None:
            from framework.patch_manager import PatchManager  # noqa: PLC0415
            self._patch_manager = PatchManager(
                memory=self.memory,
                run_tests=True,
            )
        return self._patch_manager
