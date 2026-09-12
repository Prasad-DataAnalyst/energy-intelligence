"""
production/thumbnail_ai.py — CTR-Optimised Thumbnail Generator
===============================================================
Generates 3 thumbnail variants per video:
  Variant A — "Face + Text" style (credibility)
  Variant B — "Bold Stat" style (shock/curiosity)
  Variant C — "Cinematic Scene" style (aesthetic)

Uses memory CTR data to weight style selection.
Rendered at 1280×720 with Pillow; optionally enhances with AI generation.
"""
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.thumbnail_ai")

HERE = Path(__file__).parent.parent

# CTR-proven colour palettes per style
PALETTES = {
    "tech-dark":       {"bg": (5, 10, 25),   "acc": (0, 212, 255),  "text": (255, 255, 255)},
    "vlog-warm":       {"bg": (30, 15, 5),   "acc": (255, 140, 30), "text": (255, 240, 200)},
    "news-clean":      {"bg": (15, 15, 30),  "acc": (220, 40, 60),  "text": (255, 255, 255)},
    "motivation-epic": {"bg": (10, 5, 20),   "acc": (255, 200, 0),  "text": (255, 255, 255)},
}

# High-CTR title reduction patterns
HOOK_TEMPLATES = [
    "{number} Things About {topic}",
    "The Truth About {topic}",
    "Why {topic} Changes Everything",
    "{topic}: What Nobody Tells You",
    "I Tested {topic} For 30 Days",
]


class ThumbnailAI:
    def __init__(self, memory=None, width: int = 1280, height: int = 720):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self.W      = width
        self.H      = height

    def _load_font(self, size: int):
        try:
            from PIL import ImageFont
            for path in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            ]:
                if Path(path).exists():
                    return ImageFont.truetype(path, size)
            return ImageFont.load_default()
        except Exception:
            try:
                from PIL import ImageFont
                return ImageFont.load_default()
            except Exception:
                return None

    # ── Variant A: Face + Text ─────────────────────────────────────────────

    def _variant_face_text(self, topic: Dict, style: str,
                            out_path: str) -> bool:
        """Bold text on gradient background with accent bar — CTR workhorse."""
        try:
            from PIL import Image, ImageDraw, ImageFilter
            pal   = PALETTES.get(style, PALETTES["tech-dark"])
            img   = Image.new("RGB", (self.W, self.H), pal["bg"])
            draw  = ImageDraw.Draw(img)

            # Gradient overlay
            for y in range(self.H):
                t = y / self.H
                r = int(pal["bg"][0] * (1-t) + min(pal["bg"][0]+30, 255) * t)
                g = int(pal["bg"][1] * (1-t) + min(pal["bg"][1]+30, 255) * t)
                b = int(pal["bg"][2] * (1-t) + min(pal["bg"][2]+40, 255) * t)
                draw.line([(0, y), (self.W, y)], fill=(r, g, b))

            # Accent bar (left side)
            bar_w = self.W // 16
            draw.rectangle([0, 0, bar_w, self.H], fill=pal["acc"])

            # Main title text
            title  = topic.get("title", "")[:60]
            words  = title.split()
            line1  = " ".join(words[:4])
            line2  = " ".join(words[4:]) if len(words) > 4 else ""

            fs1    = self.H // 6
            font1  = self._load_font(fs1)
            x_text = bar_w + self.W // 12
            y1     = self.H // 4

            # Shadow
            if font1:
                draw.text((x_text+4, y1+4), line1, fill=(0,0,0), font=font1)
                draw.text((x_text,   y1),   line1, fill=pal["text"], font=font1)
                if line2:
                    fs2   = int(fs1 * 0.85)
                    font2 = self._load_font(fs2)
                    y2    = y1 + fs1 + 10
                    draw.text((x_text+3, y2+3), line2, fill=(0,0,0), font=font2)
                    draw.text((x_text,   y2),   line2, fill=pal["acc"], font=font2)

            # Corner badge: "NEW" or "2025"
            badge_fs = self.H // 12
            badge_f  = self._load_font(badge_fs)
            bx, by   = self.W - self.W//5, self.H // 10
            draw.ellipse([bx-50, by-30, bx+90, by+50], fill=pal["acc"])
            if badge_f:
                draw.text((bx-30, by-15), "2025", fill=(0,0,0), font=badge_f)

            img.save(out_path, "JPEG", quality=95)
            return True
        except Exception as e:
            log.debug(f"Thumbnail A failed: {e}")
            return False

    # ── Variant B: Bold Stat ───────────────────────────────────────────────

    def _variant_bold_stat(self, topic: Dict, style: str,
                            out_path: str) -> bool:
        """Centred giant number or shocking claim on dark background."""
        try:
            from PIL import Image, ImageDraw
            pal   = PALETTES.get(style, PALETTES["tech-dark"])
            img   = Image.new("RGB", (self.W, self.H), pal["bg"])
            draw  = ImageDraw.Draw(img)

            # Radial glow
            cx, cy = self.W // 2, self.H // 2
            for rad in range(min(self.W, self.H) // 2, 0, -20):
                alpha = int(80 * (1 - rad / (min(self.W, self.H) // 2)))
                col   = (*pal["acc"][:3],)
                draw.ellipse([cx-rad, cy-rad, cx+rad, cy+rad],
                             outline=col, width=1)

            # Huge central text (stat or keyword)
            title  = topic.get("title", "")
            # Extract number or use abbreviated title
            m      = re.search(r"\d+[\.,]?\d*[%x]?", title)
            center = m.group() if m else title.split()[0].upper()

            fs_big = self.H // 2
            font_b = self._load_font(fs_big)
            if font_b:
                bbox = draw.textbbox((0, 0), center, font=font_b)
                tw   = bbox[2] - bbox[0]
                th   = bbox[3] - bbox[1]
                tx   = (self.W - tw) // 2
                ty   = (self.H - th) // 2 - self.H // 10
                # Multi-pass glow
                for offset in [8, 5, 3, 0]:
                    col = pal["acc"] if offset == 0 else (*pal["acc"][:3],)
                    draw.text((tx+offset, ty+offset), center, fill=col, font=font_b)

            # Subtitle
            fs_sub = self.H // 12
            font_s = self._load_font(fs_sub)
            subtitle = " ".join(title.split()[-5:]) if len(title.split()) > 3 else title
            if font_s:
                bbox2 = draw.textbbox((0, 0), subtitle, font=font_s)
                tw2   = bbox2[2] - bbox2[0]
                draw.text(((self.W - tw2) // 2, cy + self.H // 4),
                          subtitle, fill=pal["text"], font=font_s)

            img.save(out_path, "JPEG", quality=95)
            return True
        except Exception as e:
            log.debug(f"Thumbnail B failed: {e}")
            return False

    # ── Variant C: Cinematic Scene ─────────────────────────────────────────

    def _variant_cinematic(self, topic: Dict, style: str,
                            frame_path: Optional[str],
                            out_path: str) -> bool:
        """Use a video frame as background, add title bar at bottom."""
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
            pal = PALETTES.get(style, PALETTES["tech-dark"])

            if frame_path and Path(frame_path).exists():
                bg = Image.open(frame_path).convert("RGB").resize((self.W, self.H))
                bg = ImageEnhance.Brightness(bg).enhance(0.6)
                bg = ImageEnhance.Contrast(bg).enhance(1.3)
            else:
                bg = Image.new("RGB", (self.W, self.H), pal["bg"])

            draw = ImageDraw.Draw(bg)

            # Bottom gradient bar
            bar_h = self.H // 3
            for y in range(bar_h, 0, -1):
                alpha = int(220 * (1 - y / bar_h))
                iy    = self.H - y
                draw.line([(0, iy), (self.W, iy)],
                          fill=(*pal["bg"][:3],))

            # Title in bar
            title  = topic.get("title", "")[:55]
            fs     = self.H // 9
            font   = self._load_font(fs)
            if font:
                draw.text((self.W // 16, self.H - bar_h + 20),
                          title, fill=pal["text"], font=font)

            # Accent underline
            line_y = self.H - bar_h + 20 + fs + 8
            draw.rectangle([self.W // 16, line_y, self.W // 16 + 120, line_y + 6],
                           fill=pal["acc"])

            bg.save(out_path, "JPEG", quality=95)
            return True
        except Exception as e:
            log.debug(f"Thumbnail C failed: {e}")
            return False

    # ── Main API ───────────────────────────────────────────────────────────

    def generate_variants(self, topic: Dict,
                           style: str = "tech-dark",
                           output_dir: Optional[str] = None,
                           first_frame: Optional[str] = None) -> List[str]:
        """
        Generate 3 thumbnail variants. Returns list of valid paths.
        Memory CTR data influences which variant gets generated first.
        """
        slug    = re.sub(r"[^a-z0-9]+", "_",
                         topic.get("title", "thumb")[:30].lower())
        out_dir = Path(output_dir) if output_dir \
                  else HERE / "output" / slug / "thumbnails"
        out_dir.mkdir(parents=True, exist_ok=True)

        best_variant = self.memory.get_best_config("thumbnail_variant") or "A"
        variants_order = ["A", "B", "C"]
        if best_variant in variants_order:
            variants_order.remove(best_variant)
            variants_order.insert(0, best_variant)

        paths = []
        for v in variants_order:
            out_path = str(out_dir / f"thumbnail_{v}.jpg")
            ok = False
            if v == "A":
                ok = self._variant_face_text(topic, style, out_path)
            elif v == "B":
                ok = self._variant_bold_stat(topic, style, out_path)
            elif v == "C":
                ok = self._variant_cinematic(topic, style, first_frame, out_path)
            if ok:
                paths.append(out_path)
                log.info(f"Thumbnail variant {v} → {out_path}")

        if not paths:
            log.error("All thumbnail variants failed")
        return paths
