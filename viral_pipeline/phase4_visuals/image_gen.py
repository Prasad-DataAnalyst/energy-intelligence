"""
Phase 4 — AI Image Generator
==============================
Priority:
  1. Replicate API  (Stable Diffusion XL — best quality)
  2. Stability AI   (via direct REST API)
  3. PIL mock       (always works — gradient + text overlay)
"""
import logging
import math
import os
import re
import textwrap
import urllib.request
from typing import Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from utils.api_client import get_session, download_file
from utils.models import Scene, VisualAsset

log = logging.getLogger("viral_pipeline.visuals.image_gen")


# ── Replicate ─────────────────────────────────────────────────────────────────

def gen_replicate(prompt: str, negative: str, width: int, height: int,
                  out_path: str, model_id: str) -> bool:
    api_key = os.getenv("REPLICATE_API_TOKEN", "")
    if not api_key:
        return False
    try:
        import replicate
        client = replicate.Client(api_token=api_key)
        output = client.run(
            model_id,
            input={
                "prompt": prompt,
                "negative_prompt": negative,
                "width": width, "height": height,
                "num_inference_steps": 30,
                "guidance_scale": 7.5,
                "scheduler": "K_EULER",
            }
        )
        # output is a list of URLs
        url = output[0] if isinstance(output, list) else str(output)
        download_file(url, out_path)
        log.info(f"  Replicate image: {os.path.getsize(out_path)//1024} KB")
        return True
    except Exception as e:
        log.warning(f"Replicate failed: {e}")
        return False


# ── Stability AI ──────────────────────────────────────────────────────────────

def gen_stability(prompt: str, negative: str, width: int, height: int,
                  out_path: str) -> bool:
    api_key = os.getenv("STABILITY_API_KEY", "")
    if not api_key:
        return False
    try:
        import base64
        sess = get_session()
        r = sess.post(
            "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "text_prompts": [
                    {"text": prompt, "weight": 1.0},
                    {"text": negative, "weight": -1.0},
                ],
                "cfg_scale": 7,
                "height": height, "width": width,
                "samples": 1, "steps": 30,
            },
            timeout=90,
        )
        if not r.ok:
            return False
        artifacts = r.json().get("artifacts", [])
        if not artifacts:
            return False
        img_bytes = base64.b64decode(artifacts[0]["base64"])
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        return True
    except Exception as e:
        log.warning(f"Stability AI failed: {e}")
        return False


# ── PIL Mock (always-available fallback) ──────────────────────────────────────

# Style → colour palettes
STYLE_PALETTES = {
    "tech-dark":       {"bg": (5, 10, 25),  "bg2": (10, 20, 50),
                        "acc": (0, 212, 255)},
    "vlog-warm":       {"bg": (40, 20, 10), "bg2": (70, 40, 15),
                        "acc": (255, 170, 50)},
    "news-clean":      {"bg": (15, 15, 20), "bg2": (25, 25, 40),
                        "acc": (220, 40, 40)},
    "motivation-epic": {"bg": (10, 5, 20),  "bg2": (20, 10, 45),
                        "acc": (180, 80, 255)},
}


def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
              if bold else
              ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"])
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def gen_pil_mock(prompt: str, style: str, width: int, height: int,
                 scene_id: int, out_path: str) -> bool:
    """Generate a stylised gradient image with text overlay using Pillow."""
    palette = STYLE_PALETTES.get(style, STYLE_PALETTES["tech-dark"])
    bg, bg2, acc = palette["bg"], palette["bg2"], palette["acc"]

    img  = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Vertical gradient
    for y in range(height):
        r = y / height
        c = tuple(int(bg[i] + (bg2[i] - bg[i]) * r) for i in range(3))
        draw.line([(0, y), (width, y)], fill=c)

    # Animated diagonal texture
    rng = np.random.default_rng(scene_id)
    grain = rng.normal(0, 3, (height, width, 3)).astype(np.int16)
    arr = np.clip(np.array(img).astype(np.int16) + grain, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)

    # Diagonal accent lines
    for x in range(-height, width, 80):
        draw.line([(x, 0), (x + height, height)],
                  fill=tuple(max(0, v - 200) for v in acc), width=1)

    # Accent vignette glow at centre
    glow_r = min(width, height) * 0.6
    for step in range(20, 0, -1):
        alpha = int(12 * (step / 20))
        r_s   = int(glow_r * step / 20)
        draw.ellipse(
            [width//2 - r_s, height//2 - r_s,
             width//2 + r_s, height//2 + r_s],
            fill=(*acc, alpha),
        )

    # Scene prompt text — wrap and render large
    clean_prompt = re.sub(r"[^\w\s,\.\-\!]", " ", prompt)
    words = clean_prompt.split()[:12]
    wrapped = textwrap.fill(" ".join(words), width=30)

    font_lg = _get_font(min(width // 18, 64), bold=True)
    font_sm = _get_font(min(width // 40, 28))

    # Shadow
    draw.text((width//2 + 3, height//2 + 3), wrapped,
              font=font_lg, fill=(0, 0, 0), anchor="mm", align="center")
    draw.text((width//2, height//2), wrapped,
              font=font_lg, fill=(220, 220, 255), anchor="mm", align="center")

    # Scene number badge
    badge_text = f"SCENE {scene_id}"
    draw.rectangle([20, 20, 180, 60], fill=acc)
    draw.text((100, 40), badge_text, font=font_sm,
              fill=(0, 0, 0), anchor="mm")

    img.save(out_path, quality=95)
    return True


# ── Smart dispatcher ──────────────────────────────────────────────────────────

def generate_scene_image(scene: Scene, style: str,
                         output_dir: str, cfg: dict) -> VisualAsset:
    """Generate the background image for one scene."""
    vid_cfg   = cfg.get("video", {})
    res_str   = vid_cfg.get("resolution", "1280x720")
    width, height = map(int, res_str.lower().split("x"))

    vis_cfg   = cfg.get("visuals", {})
    provider  = vis_cfg.get("image_provider", "mock")
    model_id  = vis_cfg.get("replicate_model", "")
    negative  = vis_cfg.get("negative_prompt",
                             "blurry, watermark, text, low quality")
    style_kw  = STYLE_PALETTES.get(style, {})

    out_path = os.path.join(output_dir, f"scene_{scene.id:02d}.png")
    os.makedirs(output_dir, exist_ok=True)

    # Enrich prompt with style keywords
    prompt = f"{scene.visual}, {style}, cinematic lighting, 4K, ultra detailed"

    success = False
    if provider == "replicate" and model_id:
        success = gen_replicate(prompt, negative, width, height, out_path, model_id)
    if not success and provider == "stability":
        success = gen_stability(prompt, negative, width, height, out_path)
    if not success:
        gen_pil_mock(prompt, style, width, height, scene.id, out_path)

    return VisualAsset(
        scene_id=scene.id,
        image_file=out_path,
        width=width,
        height=height,
    )
