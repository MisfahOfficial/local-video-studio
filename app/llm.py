"""One way to ask Gemini (free tier), moving to the next model when one is busy (503),
out of quota (429) or retired (404)."""
from __future__ import annotations

import json
from typing import Any

from .providers.base import ProviderError
from .providers.http import post_json

FALLBACK_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash-lite")


def gemini_text(settings: Any, prompt: str, *, schema: dict[str, Any] | None = None, temperature: float = 0.7,
                timeout: int = 120) -> str:
    key = str(getattr(settings, "gemini_api_key", "") or "").strip()
    if not key:
        raise ProviderError("No Gemini key in Settings")
    config: dict[str, Any] = {"temperature": temperature}
    if schema is not None:
        config.update(response_mime_type="application/json", response_schema=schema)
    models = list(dict.fromkeys([str(getattr(settings, "gemini_model", "") or ""), *FALLBACK_MODELS]))
    last: Exception | None = None
    for model in [item for item in models if item]:
        try:
            response = post_json(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}, {}, timeout=timeout,
            )
            return str(response["candidates"][0]["content"]["parts"][0]["text"])
        except (ProviderError, KeyError, IndexError, TypeError) as error:
            last = error
            continue
    raise ProviderError(f"Every Gemini model was unavailable: {str(last)[:200]}")


def gemini_look(settings: Any, prompt: str, images: list[bytes], schema: dict[str, Any]) -> Any:
    """Gemini looks at PNG frames and answers in JSON (used to judge rendered designs)."""
    import base64

    key = str(getattr(settings, "gemini_api_key", "") or "").strip()
    if not key:
        raise ProviderError("No Gemini key in Settings")
    parts: list[dict[str, Any]] = [{"text": prompt}]
    parts += [{"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode()}} for image in images]
    body = {"contents": [{"parts": parts}], "generationConfig": {
        "temperature": 0.2, "response_mime_type": "application/json", "response_schema": schema}}
    last: Exception | None = None
    for model in list(dict.fromkeys([str(getattr(settings, "gemini_model", "") or ""), *FALLBACK_MODELS])):
        if not model:
            continue
        try:
            response = post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                                 body, {}, timeout=120)
            return json.loads(response["candidates"][0]["content"]["parts"][0]["text"])
        except (ProviderError, KeyError, IndexError, TypeError, ValueError) as error:
            last = error
    raise ProviderError(f"Every Gemini model was unavailable: {str(last)[:200]}")


def gemini_json(settings: Any, prompt: str, schema: dict[str, Any], temperature: float = 0.7) -> Any:
    return json.loads(gemini_text(settings, prompt, schema=schema, temperature=temperature))
