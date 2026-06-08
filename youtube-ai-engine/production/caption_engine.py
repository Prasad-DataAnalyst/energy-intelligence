"""
production/caption_engine.py — Word-Level Caption Generator
============================================================
Generates SRT / ASS subtitle files from script voiceover text:
  1. Whisper (OpenAI local model) — word-level timestamps
  2. Script-based approximation — distribute words evenly by scene timing
  3. Auto-translation support (via LibreTranslate or Google Translate)

Also burns captions into video via FFmpeg subtitles filter.
"""
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.caption_engine")

HERE = Path(__file__).parent.parent


class CaptionEngine:

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size  # tiny|base|small|medium|large

    # ── Whisper transcription ──────────────────────────────────────────────

    def _transcribe_whisper(self, audio_path: str) -> Optional[List[Dict]]:
        """Return list of {start, end, text} word segments via Whisper."""
        try:
            import whisper
            model  = whisper.load_model(self.model_size)
            result = model.transcribe(audio_path, word_timestamps=True,
                                      language="en")
            segments = []
            for seg in result.get("segments", []):
                for w in seg.get("words", []):
                    segments.append({
                        "start": w["start"],
                        "end":   w["end"],
                        "text":  w["word"].strip(),
                    })
            log.info(f"Whisper: {len(segments)} words transcribed")
            return segments
        except ImportError:
            log.debug("Whisper not installed — using script approximation")
            return None
        except Exception as e:
            log.debug(f"Whisper failed: {e}")
            return None

    # ── Script-based approximation ─────────────────────────────────────────

    @staticmethod
    def _script_to_words(script: Dict) -> List[Dict]:
        """
        Distribute voiceover words across scene time windows.
        Assumes average speaking rate of 2.8 words/second.
        """
        WORDS_PER_SEC = 2.8
        segments = []
        cursor   = 0.0

        for scene in script.get("scenes", []):
            text     = scene.get("voiceover", "")
            duration = float(scene.get("duration", 5))
            words    = text.split()
            if not words:
                cursor += duration
                continue

            # Group into ~3-word caption chunks
            chunk_size = 3
            chunks     = [words[i:i+chunk_size]
                          for i in range(0, len(words), chunk_size)]
            chunk_dur  = duration / max(len(chunks), 1)

            for chunk in chunks:
                chunk_text = " ".join(chunk)
                end        = cursor + chunk_dur
                segments.append({
                    "start": round(cursor, 3),
                    "end":   round(end, 3),
                    "text":  chunk_text,
                })
                cursor = end
        return segments

    # ── SRT formatting ─────────────────────────────────────────────────────

    @staticmethod
    def _sec_to_srt_ts(s: float) -> str:
        h  = int(s // 3600)
        m  = int((s % 3600) // 60)
        sc = s % 60
        return f"{h:02d}:{m:02d}:{sc:06.3f}".replace(".", ",")

    def to_srt(self, segments: List[Dict]) -> str:
        lines = []
        for i, seg in enumerate(segments, 1):
            lines.append(str(i))
            lines.append(
                f"{self._sec_to_srt_ts(seg['start'])} --> "
                f"{self._sec_to_srt_ts(seg['end'])}"
            )
            lines.append(seg["text"])
            lines.append("")
        return "\n".join(lines)

    # ── ASS formatting (styled) ────────────────────────────────────────────

    @staticmethod
    def to_ass(segments: List[Dict], style: str = "tech-dark") -> str:
        colour = {"tech-dark": "&H00FFFFFF&",
                  "vlog-warm": "&H00FFFFF0&",
                  "news-clean": "&H00FFFFFF&",
                  "motivation-epic": "&H00FFD700&"}.get(style, "&H00FFFFFF&")
        header = (
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,Bold,Shadow,Alignment,MarginV\n"
            f"Style: Default,Arial,48,{colour},-1,1,2,60\n\n"
            "[Events]\n"
            "Format: Layer,Start,End,Style,Text\n"
        )

        def sec_to_ass(s: float) -> str:
            h  = int(s // 3600)
            m  = int((s % 3600) // 60)
            sc = s % 60
            return f"{h}:{m:02d}:{sc:05.2f}"

        events = []
        for seg in segments:
            events.append(
                f"Dialogue: 0,{sec_to_ass(seg['start'])},"
                f"{sec_to_ass(seg['end'])},Default,{seg['text']}"
            )
        return header + "\n".join(events)

    # ── Burn into video ────────────────────────────────────────────────────

    def burn_captions(self, video_path: str, srt_path: str,
                       out_path: str) -> bool:
        """Hard-burn SRT captions into video using FFmpeg subtitles filter."""
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"subtitles='{srt_path}':force_style='FontSize=36,Bold=1,"
                   f"PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
                   f"Outline=2,Shadow=1,MarginV=50'",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "copy",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            log.debug(f"Burn captions failed: {r.stderr[-200:]}")
        return r.returncode == 0

    # ── Translation ────────────────────────────────────────────────────────

    def translate_srt(self, srt_content: str, target_lang: str) -> str:
        """Translate SRT captions via LibreTranslate (self-hosted) or skip."""
        endpoint = "http://localhost:5000/translate"
        try:
            import requests
            lines   = srt_content.split("\n")
            out     = []
            chunk   = []
            for line in lines:
                ts_re = re.match(r"\d{2}:\d{2}:\d{2},\d{3} -->", line)
                if line.strip().isdigit() or ts_re or not line.strip():
                    if chunk:
                        text = " ".join(chunk)
                        r    = requests.post(endpoint, json={
                            "q": text, "source": "en", "target": target_lang
                        }, timeout=10, verify=False)
                        translated = r.json().get("translatedText", text) if r.ok else text
                        out.extend(translated.split(" "))
                        chunk = []
                    out.append(line)
                else:
                    chunk.append(line)
            return "\n".join(out)
        except Exception as e:
            log.debug(f"Translation failed: {e}")
            return srt_content

    # ── Main API ───────────────────────────────────────────────────────────

    def generate(self, script: Dict,
                  audio_path: Optional[str] = None,
                  output_dir: Optional[str] = None,
                  style: str = "tech-dark") -> Dict:
        """
        Generate SRT + ASS caption files.
        Returns {srt_path, ass_path, segments}.
        """
        out_dir = Path(output_dir) if output_dir \
                  else HERE / "output" / "captions"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Try Whisper on actual audio first
        segments = None
        if audio_path and Path(audio_path).exists():
            segments = self._transcribe_whisper(audio_path)

        # Fall back to script approximation
        if not segments:
            segments = self._script_to_words(script)
            log.info(f"Caption engine: {len(segments)} caption chunks (script approximation)")

        srt_path = str(out_dir / "captions.srt")
        ass_path = str(out_dir / "captions.ass")

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(self.to_srt(segments))
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(self.to_ass(segments, style=style))

        log.info(f"Captions: {srt_path}")
        return {"srt_path": srt_path, "ass_path": ass_path, "segments": segments}
