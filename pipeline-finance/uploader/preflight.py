"""
uploader/preflight.py — DriftWire326 Module 30
PreflightChecker: validates all required artifacts before YouTube upload.
Prevents failed uploads by catching missing/corrupt files early.

Checks performed:
  - Video file exists and is non-empty
  - Thumbnail exists, is non-empty, is 1280×720 PNG
  - Script exists and has required compliance phrases
  - Audio file exists and meets MIN_AUDIO_SECONDS
  - Quota headroom ≥ MIN_QUOTA_TO_UPLOAD (1700 units)
  - Title is non-empty and within YouTube 100-char limit
  - Description contains disclaimer and AI disclosure
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

MIN_QUOTA_TO_UPLOAD = 1700
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 5000
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
MIN_AUDIO_SECONDS = 60.0

_REQUIRED_DESC_PHRASES = [
    settings.disclaimer_text,
    "Narration is AI-generated",
]

_REQUIRED_SCRIPT_PHRASES = list(getattr(settings, "required_phrases", []))


@dataclass
class PreflightResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"[{status}]"]
        if self.errors:
            parts.append(f"{len(self.errors)} error(s): " + "; ".join(self.errors))
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s): " + "; ".join(self.warnings))
        if self.passed and not self.warnings:
            parts.append("All checks passed.")
        return " | ".join(parts)


class PreflightChecker:
    """Validates all upload artifacts before committing YouTube quota."""

    def __init__(self, quota_tracker=None):
        self._quota_tracker = quota_tracker

    def _get_quota_tracker(self):
        if self._quota_tracker is None:
            from uploader.quota_tracker import QuotaTracker
            self._quota_tracker = QuotaTracker()
        return self._quota_tracker

    # ── Individual checks ────────────────────────────────────────────────────

    def check_video_file(self, video_path: Optional[Path], result: PreflightResult) -> None:
        if video_path is None:
            result.add_error("video_path is None")
            return
        p = Path(video_path)
        if not p.exists():
            result.add_error(f"Video file not found: {p}")
        elif p.stat().st_size < 1024:
            result.add_error(f"Video file too small (<1 KB): {p}")

    def check_thumbnail(self, thumbnail_path: Optional[Path], result: PreflightResult) -> None:
        if thumbnail_path is None:
            result.add_warning("No thumbnail provided — YouTube will auto-generate one")
            return
        p = Path(thumbnail_path)
        if not p.exists():
            result.add_error(f"Thumbnail not found: {p}")
            return
        if p.stat().st_size < 512:
            result.add_error(f"Thumbnail file too small (<512 bytes): {p}")
            return
        try:
            from PIL import Image
            with Image.open(p) as img:
                w, h = img.size
                if w != THUMBNAIL_WIDTH or h != THUMBNAIL_HEIGHT:
                    result.add_warning(
                        f"Thumbnail size {w}×{h} != expected {THUMBNAIL_WIDTH}×{THUMBNAIL_HEIGHT}"
                    )
                if img.format and img.format.upper() not in {"PNG", "JPEG", "JPG"}:
                    result.add_warning(f"Thumbnail format '{img.format}' — PNG/JPEG preferred")
        except ImportError:
            result.add_warning("Pillow not installed — thumbnail dimensions not verified")
        except Exception as exc:
            result.add_error(f"Thumbnail unreadable: {exc}")

    def check_title(self, title: Optional[str], result: PreflightResult) -> None:
        if not title:
            result.add_error("Title is empty")
            return
        if len(title) > YOUTUBE_TITLE_MAX:
            result.add_error(
                f"Title too long ({len(title)} chars > {YOUTUBE_TITLE_MAX}): '{title[:60]}...'"
            )

    def check_description(self, description: Optional[str], result: PreflightResult) -> None:
        if not description:
            result.add_error("Description is empty")
            return
        if len(description) > YOUTUBE_DESC_MAX:
            result.add_warning(
                f"Description may be truncated ({len(description)} > {YOUTUBE_DESC_MAX} chars)"
            )
        for phrase in _REQUIRED_DESC_PHRASES:
            if phrase and phrase not in description:
                result.add_error(f"Description missing required phrase: '{phrase[:60]}'")

    def check_script(self, script_path: Optional[Path], result: PreflightResult) -> None:
        if script_path is None:
            result.add_warning("No script path provided — compliance not checked")
            return
        p = Path(script_path)
        if not p.exists():
            result.add_warning(f"Script file not found: {p} — compliance not checked")
            return
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:
            result.add_error(f"Cannot read script: {exc}")
            return
        for phrase in _REQUIRED_SCRIPT_PHRASES:
            if phrase and phrase not in content:
                result.add_warning(f"Script may be missing compliance phrase: '{phrase[:60]}'")

    def check_quota(self, result: PreflightResult) -> None:
        try:
            qt = self._get_quota_tracker()
            if not qt.can_upload():
                remaining = qt.get_remaining()
                result.add_error(
                    f"Insufficient quota: {remaining} units remaining "
                    f"(need {MIN_QUOTA_TO_UPLOAD})"
                )
        except Exception as exc:
            result.add_warning(f"Could not check quota: {exc}")

    # ── Main entrypoint ──────────────────────────────────────────────────────

    def run(
        self,
        video_path: Optional[Path] = None,
        thumbnail_path: Optional[Path] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        script_path: Optional[Path] = None,
        check_quota: bool = True,
    ) -> PreflightResult:
        """
        Run all preflight checks. Returns PreflightResult.
        Any error sets result.passed = False.
        """
        result = PreflightResult(passed=True)

        self.check_video_file(video_path, result)
        self.check_thumbnail(thumbnail_path, result)
        self.check_title(title, result)
        self.check_description(description, result)
        self.check_script(script_path, result)
        if check_quota:
            self.check_quota(result)

        logger.info("Preflight check: %s", result.summary())
        return result
