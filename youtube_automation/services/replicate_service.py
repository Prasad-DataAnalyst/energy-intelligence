"""
services/replicate_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: Replicate API wrapper for Stable Diffusion XL image generation.
Input:  scene description prompt (str)
Output: local path to generated PNG image
"""
from __future__ import annotations
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SDXL_VERSION = "39ed52f2319f9ee9a0f3780b2de399fbbfa0d6a1"


def is_available() -> bool:
    return bool(os.getenv("REPLICATE_API_TOKEN", ""))


def generate_image(
    prompt: str,
    out_path: str,
    negative_prompt: str = "blurry, low quality, watermark, text, logo",
    width: int = 1344,
    height: int = 768,
    steps: int = 30,
) -> Optional[str]:
    """
    Generate a cinematic still image via Stable Diffusion XL on Replicate.
    Returns out_path on success, None on failure.
    """
    if not is_available():
        return None

    try:
        import replicate
    except ImportError:
        log.warning("replicate package not installed — pip install replicate")
        return None

    cinematic_prompt = (
        f"{prompt}, cinematic photography, shot on ARRI Alexa, "
        "ultra-detailed, 8K resolution, professional color grade, "
        "dramatic lighting, shallow depth of field, film grain"
    )

    try:
        os.environ["REPLICATE_API_TOKEN"] = os.getenv("REPLICATE_API_TOKEN", "")
        output = replicate.run(
            f"stability-ai/sdxl:{SDXL_VERSION}",
            input={
                "prompt": cinematic_prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "num_inference_steps": steps,
                "guidance_scale": 7.5,
                "num_outputs": 1,
            }
        )
        image_url = output[0] if output else None
        if not image_url:
            return None
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(str(image_url), out_path)
        log.info("Replicate SDXL image: %s", out_path)
        return out_path
    except Exception as e:
        log.warning("Replicate generation failed: %s", e)
        return None
