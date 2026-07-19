"""
builders/fallback_builder.py — DriftWire326 reliability layer
Emergency fallback video: when the main pipeline cannot run (Claude outage,
market data unavailable, TTS failure), this builds a minimal-but-publishable
video so the channel never goes dark.

Design constraints — every dependency here must be nearly unbreakable:
  - Script: template text filled from the newest cached market JSON if one
    exists (no Claude call); otherwise a generic evergreen educational script.
  - Audio: gTTS (network) with pyttsx3 offline fallback.
  - Visual: single static branded frame rendered with PIL.
  - Video: ffmpeg loop of the static frame + audio (no moviepy dependency).

The result still passes the compliance rules: disclaimer and AI disclosure
are baked into the script template and the upload description.
"""
import json
import logging
import subprocess
from datetime import date, datetime
from glob import glob
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

FALLBACK_DIR = settings.output_dir / "fallback"

_EVERGREEN_SCRIPT = (
    "Hey everyone, welcome back to DriftWire. "
    "We're keeping it short today with a quick reminder about the single most "
    "powerful force in investing: compound growth. "
    "When your returns start earning returns of their own, time does the heavy lifting. "
    "A dollar invested early can outweigh many dollars invested late. "
    "That's why consistency beats timing for most long-term investors. "
    "Markets move every day, but the fundamentals of building wealth don't: "
    "spend less than you earn, invest the difference, and give it time. "
    "We'll be back with the full market recap in the next video. "
    f"{settings.disclaimer_text} "
    "Narration is AI-generated. Thanks for watching DriftWire."
)

_CACHED_DATA_SCRIPT = (
    "Hey everyone, welcome back to DriftWire. "
    "Quick update — full coverage returns in the next video. "
    "As of the most recent close, the S&P 500 stood at {sp500_price}, "
    "moving {sp500_change} percent. "
    "The Nasdaq changed {nasdaq_change} percent, "
    "and the VIX fear index was at {vix_level}. "
    "Remember, day-to-day moves matter less than the trend — "
    "long-term investors win by staying consistent, not by reacting to every headline. "
    "We'll be back with the complete market recap shortly. "
    f"{settings.disclaimer_text} "
    "Narration is AI-generated. Thanks for watching DriftWire."
)


def _latest_market_json() -> Optional[dict]:
    """Return the newest cached market JSON from output/scripts/, if any."""
    pattern = str(settings.output_dir / "scripts" / "market_*.json")
    files = sorted(glob(pattern), reverse=True)
    for f in files[:3]:
        try:
            return json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _marketstack_snapshot() -> Optional[dict]:
    """
    Disaster-day backup: fresh EOD closes from Marketstack (free tier,
    100 requests/month — used ONLY when no cached market data exists,
    i.e. when yfinance itself is the thing that broke). 3 requests/incident.
    """
    key = getattr(settings, "marketstack_api_key", "")
    if not key:
        return None
    try:
        import requests as _requests
        out: dict = {}
        for symbol, slot in (("SPY", "sp500"), ("QQQ", "nasdaq"), ("VIXY", "vix")):
            resp = _requests.get(
                "https://api.marketstack.com/v1/eod/latest",
                params={"access_key": key, "symbols": symbol},
                timeout=20,
            )
            resp.raise_for_status()
            data = (resp.json().get("data") or [{}])[0]
            close, open_ = data.get("close"), data.get("open")
            if close:
                pct = ((close - open_) / open_ * 100) if open_ else 0.0
                out[slot] = {"price": round(close, 2), "change_pct": round(pct, 2)}
        if "sp500" in out:
            logger.info("Marketstack backup snapshot used (3 of 100 monthly requests)")
            return out
    except Exception as exc:
        logger.warning("Marketstack backup failed: %s", exc)
    return None


def build_fallback_script() -> str:
    """Fill the cached-data template if market data exists, else evergreen."""
    data = _latest_market_json() or _marketstack_snapshot()
    if data:
        try:
            def _num(d: dict, *keys, default="unchanged"):
                cur = d
                for k in keys:
                    if not isinstance(cur, dict) or k not in cur:
                        return default
                    cur = cur[k]
                return cur

            return _CACHED_DATA_SCRIPT.format(
                sp500_price=_num(data, "sp500", "price", default="its recent level"),
                sp500_change=_num(data, "sp500", "change_pct", default="a fraction of a"),
                nasdaq_change=_num(data, "nasdaq", "change_pct", default="a fraction of a"),
                vix_level=_num(data, "vix", "price", default="a moderate level"),
            )
        except Exception as exc:
            logger.warning("Cached-data script failed (%s) — using evergreen", exc)
    return _EVERGREEN_SCRIPT


def _tts_to_file(text: str, out_path: Path) -> bool:
    """gTTS first, pyttsx3 offline as last resort. Returns True on success."""
    try:
        from gtts import gTTS
        gTTS(text=text, lang="en", slow=False).save(str(out_path))
        if out_path.exists() and out_path.stat().st_size > 1024:
            logger.info("Fallback audio via gTTS: %s", out_path.name)
            return True
    except Exception as exc:
        logger.warning("gTTS failed (%s) — trying pyttsx3", exc)

    try:
        import pyttsx3
        engine = pyttsx3.init()
        wav_path = out_path.with_suffix(".wav")
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
        if wav_path.exists() and wav_path.stat().st_size > 1024:
            # Convert to mp3 via ffmpeg for consistency
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), str(out_path)],
                capture_output=True, timeout=120,
            )
            wav_path.unlink(missing_ok=True)
            if proc.returncode == 0 and out_path.exists():
                logger.info("Fallback audio via pyttsx3: %s", out_path.name)
                return True
    except Exception as exc:
        logger.error("pyttsx3 fallback failed: %s", exc)
    return False


def _render_static_frame(out_path: Path, headline: str) -> bool:
    """Render a single 1920x1080 branded frame with PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (1920, 1080), color=(12, 17, 28))   # dark navy
        draw = ImageDraw.Draw(img)

        def _font(size: int):
            for candidate in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ):
                try:
                    return ImageFont.truetype(candidate, size)
                except Exception:
                    continue
            return ImageFont.load_default()

        draw.text((960, 380), "DriftWire", font=_font(120), fill=(240, 244, 250), anchor="mm")
        draw.text((960, 540), headline, font=_font(56), fill=(120, 190, 255), anchor="mm")
        draw.text(
            (960, 960),
            "Not financial advice · Narration is AI-generated",
            font=_font(32), fill=(140, 150, 165), anchor="mm",
        )
        img.save(out_path)
        return True
    except Exception as exc:
        logger.error("Static frame render failed: %s", exc)
        return False


def build_fallback_video() -> Optional[Path]:
    """
    Build the complete emergency video. Returns the mp4 path, or None if
    even the fallback path failed (which means ffmpeg/PIL are broken).
    """
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    script_text = build_fallback_script()
    (FALLBACK_DIR / f"fallback_script_{stamp}.txt").write_text(script_text, encoding="utf-8")

    audio_path = FALLBACK_DIR / f"fallback_audio_{stamp}.mp3"
    if not _tts_to_file(script_text, audio_path):
        logger.error("Fallback video aborted — no TTS engine available")
        return None

    frame_path = FALLBACK_DIR / f"fallback_frame_{stamp}.png"
    headline = f"Market Update — {date.today().strftime('%B %d, %Y')}"
    if not _render_static_frame(frame_path, headline):
        return None

    video_path = FALLBACK_DIR / f"fallback_{stamp}.mp4"
    # Channel logo badge (best-effort even on disaster days)
    logo_args: list[str] = []
    try:
        from builders.logo_overlay import get_round_logo
        logo_png = get_round_logo(140)
        if logo_png:
            logo_args = ["-i", str(logo_png), "-filter_complex",
                         "[0:v][2:v]overlay=main_w-overlay_w-30:30"]
    except Exception:
        pass
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(frame_path),
                "-i", str(audio_path),
                *logo_args,
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            logger.error("Fallback ffmpeg build failed: %s", proc.stderr[-400:])
            return None
    except FileNotFoundError:
        logger.error("ffmpeg not installed — cannot build fallback video")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Fallback ffmpeg build timed out")
        return None

    logger.info("Emergency fallback video built: %s", video_path.name)
    return video_path


def build_and_upload_fallback() -> Optional[str]:
    """
    Full emergency path: build the fallback video and upload it
    (quota- and preflight-gated like any other upload).
    Returns the video_id or None.
    """
    video_path = build_fallback_video()
    if not video_path:
        return None

    try:
        from uploader.quota_tracker import QuotaTracker
        from uploader.uploader import upload_full, UploadConfig

        quota = QuotaTracker()
        if not quota.can_upload():
            logger.warning("Fallback upload skipped — quota exceeded")
            return None

        today = date.today().strftime("%B %d, %Y")
        config = UploadConfig(
            title=f"Quick Market Note — {today} | DriftWire",
            description=(
                f"A quick update from DriftWire for {today}. "
                "Full market recap returns in the next video.\n\n"
                f"⚠️ {settings.disclaimer_text}\n\nNarration is AI-generated."
            ),
            tags=["market update", "investing", "DriftWire326", "finance"],
            video_type="weekday",
        )
        result = upload_full(video_path=video_path, config=config, quota_tracker=quota)
        if result.video_id:
            logger.info("Emergency fallback uploaded: %s", result.video_id)
            from uploader.uploader import record_upload
            record_upload(result, config)
        return result.video_id
    except Exception as exc:
        logger.error("Fallback upload failed: %s", exc)
        return None
