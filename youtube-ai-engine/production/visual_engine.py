"""
production/visual_engine.py — AI Image & B-Roll Generator
==========================================================
Produces one image per scene using this priority stack:
  1. Runway ML / Kling AI  (video generation, if API available)
  2. Replicate SDXL         (image generation)
  3. Stability AI           (image generation)
  4. Pexels / Pixabay       (stock photo search)
  5. PIL mock               (always available procedural graphics)

Also applies Ken Burns motion effect (zoom + pan) via FFmpeg zoompan filter.
"""
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.visual_engine")

HERE = Path(__file__).parent.parent

# Style colour palettes for PIL mock fallback
STYLE_PALETTES = {
    "tech-dark":       {"bg": (5, 10, 25),   "bg2": (10, 20, 50),  "acc": (0, 212, 255)},
    "vlog-warm":       {"bg": (40, 20, 10),  "bg2": (70, 40, 15),  "acc": (255, 170, 50)},
    "news-clean":      {"bg": (20, 20, 35),  "bg2": (35, 35, 60),  "acc": (255, 60, 80)},
    "motivation-epic": {"bg": (15, 5, 30),   "bg2": (30, 10, 50),  "acc": (255, 200, 0)},
}


def _make_session():
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        sess = requests.Session()
        sess.verify = False
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
        adapter = HTTPAdapter(max_retries=retry)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        return sess
    except ImportError:
        return None


class VisualEngine:
    def __init__(self):
        self._sess = _make_session()

    # ── Provider: Replicate SDXL ───────────────────────────────────────────

    def _replicate(self, prompt: str, style: str,
                   width: int, height: int, out_path: str) -> bool:
        api_key = os.getenv("REPLICATE_API_TOKEN", "")
        if not api_key or not self._sess:
            return False
        try:
            r = self._sess.post(
                "https://api.replicate.com/v1/predictions",
                headers={"Authorization": f"Token {api_key}",
                         "Content-Type": "application/json"},
                json={"version": "ac732df83cea7fff18b8472768c88ad041fa750d7579c" +
                                  "de07f95f4cdf044ddbcc",
                      "input": {"prompt": f"{prompt}, {style} style, cinematic",
                                "width": width, "height": height,
                                "num_outputs": 1}},
                timeout=10,
            )
            if not r.ok:
                return False
            pred_id = r.json().get("id")
            # Poll for result (max 60s)
            for _ in range(20):
                time.sleep(3)
                pr = self._sess.get(
                    f"https://api.replicate.com/v1/predictions/{pred_id}",
                    headers={"Authorization": f"Token {api_key}"},
                    timeout=10,
                )
                if not pr.ok:
                    continue
                status = pr.json().get("status")
                if status == "succeeded":
                    url = pr.json().get("output", [None])[0]
                    if url:
                        img_r = self._sess.get(url, timeout=30)
                        if img_r.ok:
                            with open(out_path, "wb") as f:
                                f.write(img_r.content)
                            return True
                elif status in ("failed", "canceled"):
                    return False
            return False
        except Exception as e:
            log.debug(f"Replicate failed: {e}")
            return False

    # ── Provider: Stability AI ─────────────────────────────────────────────

    def _stability(self, prompt: str, style: str,
                   width: int, height: int, out_path: str) -> bool:
        api_key = os.getenv("STABILITY_API_KEY", "")
        if not api_key or not self._sess:
            return False
        try:
            r = self._sess.post(
                "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json",
                         "Accept": "application/json"},
                json={"text_prompts": [{"text": f"{prompt}, {style}", "weight": 1}],
                      "cfg_scale": 7, "width": min(width, 1024),
                      "height": min(height, 1024), "samples": 1, "steps": 30},
                timeout=60,
            )
            if not r.ok:
                return False
            import base64
            img_data = r.json()["artifacts"][0]["base64"]
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(img_data))
            return True
        except Exception as e:
            log.debug(f"Stability failed: {e}")
            return False

    # ── Provider: Pexels stock photos ─────────────────────────────────────

    def _pexels(self, query: str, out_path: str) -> bool:
        api_key = os.getenv("PEXELS_API_KEY", "")
        if not api_key or not self._sess:
            return False
        try:
            r = self._sess.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={"query": query[:100], "per_page": 5, "orientation": "landscape"},
                timeout=15,
            )
            if not r.ok:
                return False
            photos = r.json().get("photos", [])
            if not photos:
                return False
            url = photos[0]["src"]["large2x"]
            img_r = self._sess.get(url, timeout=30)
            if img_r.ok:
                with open(out_path, "wb") as f:
                    f.write(img_r.content)
                return True
            return False
        except Exception as e:
            log.debug(f"Pexels failed: {e}")
            return False

    # ── Provider: PIL mock (always available) ─────────────────────────────

    def _pil_mock(self, prompt: str, style: str, width: int, height: int,
                   scene_id: int, out_path: str) -> bool:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
            import random, math
            rng = random.Random(scene_id * 7919)

            pal = STYLE_PALETTES.get(style, STYLE_PALETTES["tech-dark"])
            img = Image.new("RGB", (width, height), pal["bg"])
            arr = img.load()

            # Gradient
            for y in range(height):
                t = y / height
                r = int(pal["bg"][0] * (1-t) + pal["bg2"][0] * t)
                g = int(pal["bg"][1] * (1-t) + pal["bg2"][1] * t)
                b = int(pal["bg"][2] * (1-t) + pal["bg2"][2] * t)
                for x in range(width):
                    arr[x, y] = (r, g, b)

            draw = ImageDraw.Draw(img)

            # Accent glow circles
            for _ in range(3):
                cx  = rng.randint(0, width)
                cy  = rng.randint(0, height)
                rad = rng.randint(50, 200)
                for r2 in range(rad, 0, -10):
                    alpha = int(40 * (1 - r2/rad))
                    draw.ellipse(
                        [cx-r2, cy-r2, cx+r2, cy+r2],
                        outline=(*pal["acc"], alpha),
                    )

            # Grid lines
            for x in range(0, width, width // 8):
                draw.line([(x, 0), (x, height)],
                          fill=(*pal["acc"][:3], 20), width=1)
            for y in range(0, height, height // 6):
                draw.line([(0, y), (width, y)],
                          fill=(*pal["acc"][:3], 20), width=1)

            # Text overlay
            words  = prompt[:50]
            fs     = max(16, width // 20)
            tx, ty = width // 8, height * 2 // 3
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fs)
            except Exception:
                font = ImageFont.load_default()
            draw.text((tx+2, ty+2), words, fill=(0, 0, 0, 180), font=font)
            draw.text((tx, ty),     words, fill=pal["acc"],      font=font)

            img.save(out_path, "JPEG", quality=90)
            return True
        except Exception as e:
            log.debug(f"PIL mock failed: {e}")
            return False

    # ── Ken Burns motion ───────────────────────────────────────────────────

    @staticmethod
    def apply_ken_burns(image_path: str, out_path: str,
                         duration: float, width: int, height: int,
                         zoom_in: bool = True) -> bool:
        """Apply Ken Burns zoom+pan effect via FFmpeg zoompan."""
        fps    = 15
        nf     = max(1, int(duration * fps))
        z_expr = (f"min(zoom+0.0015,1.5)" if zoom_in
                  else "if(eq(on,1),1.5,max(zoom-0.0015,1))")
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", (f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)':d={nf}:s={width}x{height},"
                    f"fps={fps}"),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        return result.returncode == 0

    # ── Main API ───────────────────────────────────────────────────────────

    def produce_scene(self, scene: Dict, style: str,
                       output_dir: Path, width: int, height: int) -> Optional[str]:
        """Generate image for one scene. Returns path to JPEG or None."""
        scene_id    = scene.get("id", 1)
        broll       = scene.get("broll_description", scene.get("visual", ""))
        prompt      = f"{broll}, {style} aesthetic"
        out_path    = str(output_dir / f"scene_{scene_id:03d}.jpg")

        providers = [
            lambda: self._replicate(prompt, style, width, height, out_path),
            lambda: self._stability(prompt, style, width, height, out_path),
            lambda: self._pexels(broll, out_path),
            lambda: self._pil_mock(broll, style, width, height, scene_id, out_path),
        ]

        for prov in providers:
            try:
                if prov():
                    log.debug(f"  Scene {scene_id}: image ready")
                    return out_path
            except Exception:
                continue
        return None

    def produce(self, script: Dict, style: str = "tech-dark",
                 output_dir: Optional[str] = None,
                 width: int = 1280, height: int = 720) -> List[str]:
        """
        Produce one image per scene in the script.
        Returns list of image paths in scene order.
        """
        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        slug   = re.sub(r"[^a-z0-9]+", "_",
                        script.get("title", "video")[:40].lower()) if isinstance(script, dict) else "video"

        out_dir = Path(output_dir) if output_dir else HERE / "output" / slug / "frames"
        out_dir.mkdir(parents=True, exist_ok=True)

        frames = []
        for scene in scenes:
            path = self.produce_scene(scene, style, out_dir, width, height)
            frames.append(path or "")
            time.sleep(0.1)   # avoid hammering APIs

        log.info(f"VisualEngine: {sum(1 for f in frames if f)} / {len(frames)} scenes rendered")
        return frames
