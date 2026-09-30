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


HOMES = {"GB": "British", "UK": "British", "CA": "Canadian", "US": "American", "IE": "Irish"}


def home_word(country: str) -> str:
    """The country the channel is about: a Britain channel's stills show a British home, not an American one."""
    return HOMES.get(str(country or "").upper(), "American")


def still_prompt(scene_text: str, subject: str, era: str, people: bool = True, country: str = "US") -> str:
    period = era or "mid-century"
    about = f" Main subject: {subject}." if subject else ""
    if not people:
        # Ingredient cards: the item alone, filling the frame; nobody in the kitchen behind it.
        return (
            f"{scene_text.strip()[:220]}{about} Close-up {period} snapshot photograph of the food only, "
            "filling the frame, on a plain wooden counter, empty background, no people. Shot on Kodachrome "
            "slide film, natural window light, slightly soft focus, faded colours, fine film grain"
        )
    return (
        f"{scene_text.strip()[:220]}{about} Candid {period} amateur snapshot photograph, shot on Kodachrome "
        f"slide film with a cheap camera. Period-correct {period} {home_word(country)} home: enamel stove, rounded "
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
    settings: StudioSettings, scene_text: str, subject: str, era: str, destination: Path, people: bool = True,
    country: str = "US",
) -> dict[str, Any]:
    """Generate, age and save a still; returns asset metadata.

    Runware first (paid, fast, reliable); when it has no key, no balance or fails, the free
    Pollinations service (FLUX, no key) makes the image instead."""
    seed = random.randint(1, 2**31 - 1)
    prompt = still_prompt(scene_text, subject, era, people, country)
    negative = NEGATIVE if people else f"{NEGATIVE}, person, people, woman, man, face, hands, crowd"
    destination.parent.mkdir(parents=True, exist_ok=True)
    runware_error = "no Runware key"
    if settings.runware_api_key:
        try:
            result = RunwareImageProvider(settings.runware_api_key).generate(GenerationRequest(
                prompt=prompt, negative_prompt=negative, model=settings.runware_default_model,
                width=settings.width, height=settings.height, seed=seed, steps=4,
            ))
            film_finish(result.content, seed).save(destination, quality=92)
            return {"prompt": prompt, "cost": result.cost, "model": result.model, "generated_still": True}
        except ProviderError as error:
            runware_error = str(error)[:160]
    token = str(getattr(settings, "pollinations_token", "") or "").strip()
    if not token:
        # Without an account token Pollinations stamps its logo on the picture, which must not reach a video.
        raise ProviderError(f"No image could be made (Runware: {runware_error}; add a free Pollinations token for a free fallback)")
    try:
        content = pollinations_image(prompt, settings.width, settings.height, seed, people, token)
    except ProviderError as error:
        raise ProviderError(f"No image could be made (Runware: {runware_error}; Pollinations: {str(error)[:160]})") from error
    film_finish(content, seed).save(destination, quality=92)
    return {"prompt": prompt, "cost": 0.0, "model": "pollinations-flux", "generated_still": True}


def pollinations_image(prompt: str, width: int, height: int, seed: int, people: bool = True, token: str = "") -> Any:
    """Free image from Pollinations (free account token, no logo). Best effort: can be slow or down."""
    import io
    import urllib.parse

    from PIL import Image

    from .providers.http import download_bytes

    text = prompt if people else f"{prompt}, no people"
    query = urllib.parse.urlencode({"width": min(width, 1920), "height": min(height, 1080), "seed": seed,
                                    "nologo": "true", "model": "flux", **({"token": token} if token else {})})
    data = download_bytes(f"https://image.pollinations.ai/prompt/{urllib.parse.quote(text[:900])}?{query}",
                          timeout=120, max_bytes=20 * 1024 * 1024)
    try:
        Image.open(io.BytesIO(data)).verify()
    except OSError as error:
        raise ProviderError("Pollinations returned something that is not an image") from error
    return data  # raw bytes, like the Runware result
