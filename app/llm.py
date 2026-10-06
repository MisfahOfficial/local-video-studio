"""One way to ask Gemini (free tier), moving to the next model when one is busy (503),
out of quota (429) or retired (404)."""
from __future__ import annotations

import json
from typing import Any

from .providers.base import ProviderError
from .providers.http import post_json

FALLBACK_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash-lite")
CLAUDE_VISION_MODEL = "claude-haiku-4-5"  # picks clips, checks clips, reads transcripts (Ishaq, 6 Oct: Gemini closed)


def _claude_key(settings: Any) -> str:
    key = str(getattr(settings, "anthropic_api_key", "") or "").strip()
    return key if key.startswith("sk-ant-") else ""  # a Google key pasted in the Claude field is ignored


def _json_schema(schema: Any) -> Any:
    """Gemini's response schema (OBJECT/STRING...) as the JSON Schema Claude's structured outputs take."""
    if isinstance(schema, dict):
        out = {key: _json_schema(value) for key, value in schema.items()}
        if isinstance(out.get("type"), str):
            out["type"] = out["type"].lower()
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_json_schema(item) for item in schema]
    return schema


def _image_block(data: bytes) -> dict[str, Any]:
    import base64

    media = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    if len(data) > 4_500_000:  # the API takes images up to 5 MB: large contact sheets go as JPEG
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGB").save(buffer, format="JPEG", quality=85)
        data, media = buffer.getvalue(), "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def claude_ask(settings: Any, prompt: str, images: list[bytes] | None = None, schema: dict[str, Any] | None = None,
               max_tokens: int = 4096) -> str:
    """Claude answers the same requests Gemini did (text, or pictures + text, JSON when a schema is given)."""
    import anthropic

    client = anthropic.Anthropic(api_key=_claude_key(settings), max_retries=4, timeout=120.0)
    content = [_image_block(image) for image in images or []] + [{"type": "text", "text": prompt}]
    request: dict[str, Any] = {"model": str(getattr(settings, "vision_model", "") or CLAUDE_VISION_MODEL),
                               "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    if schema is not None:
        request["output_config"] = {"format": {"type": "json_schema", "schema": _json_schema(schema)}}
    try:
        response = client.messages.create(**request)
    except anthropic.APIStatusError as error:
        raise ProviderError(f"Claude answered HTTP {error.status_code}: {str(error.message)[:200]}") from error
    except anthropic.APIConnectionError as error:
        raise ProviderError(f"Claude could not be reached: {error}") from error
    if response.stop_reason == "refusal":
        raise ProviderError("Claude declined this request")
    return "".join(block.text for block in response.content if block.type == "text")


def gemini_text(settings: Any, prompt: str, *, schema: dict[str, Any] | None = None, temperature: float = 0.7,
                timeout: int = 120) -> str:
    if _claude_key(settings):
        # Claude first when its key is in Settings; Gemini (if any) only when Claude cannot answer.
        try:
            return claude_ask(settings, prompt, schema=schema)
        except ProviderError:
            if not str(getattr(settings, "gemini_api_key", "") or "").strip():
                raise
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

    if _claude_key(settings):
        try:
            return json.loads(claude_ask(settings, prompt, images, schema))
        except (ProviderError, ValueError):
            if not str(getattr(settings, "gemini_api_key", "") or "").strip():
                raise

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


def claude_text(settings: Any, prompt: str, model: str = "claude-sonnet-5", max_tokens: int = 12000) -> str:
    """Claude writes better motion designs than the free Gemini models (needs an Anthropic key)."""
    key = str(getattr(settings, "anthropic_api_key", "") or "").strip()
    if not key:
        raise ProviderError("No Anthropic key in Settings")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    message = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def pollinations_text(prompt: str, timeout: int = 180) -> str:
    """Free text model on Pollinations (no key; best effort, weaker than Claude or Gemini)."""
    response = post_json("https://text.pollinations.ai/openai",
                         {"model": "openai-fast", "messages": [{"role": "user", "content": prompt}]}, {}, timeout=timeout)
    return str(response["choices"][0]["message"]["content"])
