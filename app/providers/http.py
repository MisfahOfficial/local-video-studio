from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import ProviderError


def post_json(url: str, payload: Any, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Provider returned HTTP {error.code}: {detail[:500]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError(f"Provider request failed: {error}") from error


def download_bytes(url: str, timeout: int = 120, max_bytes: int = 80 * 1024 * 1024) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ProviderError("Generated asset is larger than the allowed download size")
            content = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProviderError(f"Could not download generated asset: {error}") from error
    if len(content) > max_bytes:
        raise ProviderError("Generated asset is larger than the allowed download size")
    return content

