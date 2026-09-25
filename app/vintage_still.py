"""Last-resort stills for scenes with no usable real footage.

The image is generated as a candid period snapshot and then finished locally like
an old photograph (faded colour, grain, softness, vignette) so it sits with real
archival footage instead of looking like glossy AI art.
"""
from __future__ import annotations

import io
import random
from pathlib import Path
from typing import Any

from .config import StudioSettings
from .domain import GenerationRequest
from .providers.base import ProviderError
from .providers.runware import RunwareImageProvider

NEGATIVE = (
    "illustration, painting, drawing, cartoon, anime, cgi, 3d render, digital art, concept art, "
    "hyperrealistic, oversaturated, glossy, perfect symmetry, studio lighting, heavy bokeh, "
    "text, letters, logo, watermark, modern appliances, stainless steel, modern kitchen, contemporary clothing, "
    "modern eyeglasses, smartphone, plastic skin, extra fingers, deformed hands"
)


def still_prompt(scene_text: str, subject: str, era: str) -> str:
    period = era or "mid-century"
    about = f" Main subject: {subject}." if subject else ""
    return (
        f"{scene_text.strip()[:220]}{about} Candid {period} amateur snapshot photograph, shot on Kodachrome "
        f"slide film with a cheap camera. Period-correct {period} American home: enamel stove, rounded "
        f"refrigerator, linoleum floor, {period} hairstyles, cotton dresses and aprons, glass mixing bowls. "
        "Natural window light, slightly soft focus, ordinary imperfect scene, real people and real food, "
        "faded colours, fine film grain"
    )


def film_finish(content: bytes, seed: int, size: tuple[int, int] = (1920, 1080)) -> Any:
    """Make a generated image read like a scanned period photo."""
    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    image = ImageOps.fit(Image.open(io.BytesIO(content)).convert("RGB"), size)
    image = ImageEnhance.Color(image).enhance(0.72)
    image = ImageEnhance.Contrast(image).enhance(0.88)
    image = Image.blend(image, Image.new("RGB", size, (214, 176, 128)), 0.10)  # warm, aged dyes
    image = image.filter(ImageFilter.GaussianBlur(0.7))
    pixels = np.asarray(image, dtype=np.float32)
    pixels = pixels * 0.92 + 14  # lifted blacks like an old print
    rng = np.random.default_rng(seed)
    grain = rng.normal(0, 8, size=(size[1], size[0], 1))
    pixels = pixels + grain
    ys, xs = np.ogrid[: size[1], : size[0]]
    distance = np.sqrt(((xs - size[0] / 2) / (size[0] / 2)) ** 2 + ((ys - size[1] / 2) / (size[1] / 2)) ** 2)
    pixels = pixels * (1 - 0.28 * np.clip(distance - 0.55, 0, 1))[..., None]
    return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))


def generate_vintage_still(
    settings: StudioSettings, scene_text: str, subject: str, era: str, destination: Path,
) -> dict[str, Any]:
    """Generate, age and save a still; returns asset metadata. Raises ProviderError without a key."""
    if not settings.runware_api_key:
        raise ProviderError("No real footage passed and no Runware key is set for a fallback image")
    seed = random.randint(1, 2**31 - 1)
    prompt = still_prompt(scene_text, subject, era)
    result = RunwareImageProvider(settings.runware_api_key).generate(GenerationRequest(
        prompt=prompt, negative_prompt=NEGATIVE, model=settings.runware_default_model,
        width=settings.width, height=settings.height, seed=seed, steps=4,
    ))
    destination.parent.mkdir(parents=True, exist_ok=True)
    film_finish(result.content, seed).save(destination, quality=92)
    return {"prompt": prompt, "cost": result.cost, "model": result.model, "generated_still": True}
