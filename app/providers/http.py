from __future__ import annotations

import functools
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import ProviderError


def _certificate_file() -> str | None:
    """Return a usable CA bundle without ever weakening TLS verification."""
    configured = os.getenv("SSL_CERT_FILE", "").strip()
    if configured and Path(configured).is_file():
        return configured

    try:
        import certifi

        bundled = certifi.where()
        if bundled and Path(bundled).is_file():
            return bundled
    except (ImportError, OSError):
        pass

    # python.org's macOS builds do not always inherit the certificates trusted
    # by macOS. These system bundles keep verification enabled while providing
    # a safe fallback before the launcher installs certifi.
    for candidate in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if Path(candidate).is_file():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def verified_ssl_context() -> ssl.SSLContext:
    cafile = _certificate_file()
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def _request_headers(headers: dict[str, str]) -> dict[str, str]:
    prepared = {"Content-Type": "application/json", "User-Agent": "LocalVideoStudio/0.6.4", **headers}
    for name, value in prepared.items():
        try:
            name.encode("ascii")
            value.encode("latin-1")
        except UnicodeEncodeError as error:
            label = "API key" if name.lower() in {"authorization", "x-goog-api-key"} else f"HTTP header {name}"
            raise ProviderError(
                f"{label} contains an unsupported copied character. Copy only the raw key, without quotes, labels, or spaces."
            ) from error
    return prepared


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
    include_headers: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, str]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers=_request_headers(headers or {}),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=verified_ssl_context()) as response:
            content = response.read()
            decoded = json.loads(content.decode("utf-8")) if content else {}
            if include_headers:
                return decoded, {name.lower(): value for name, value in response.headers.items()}
            return decoded
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Provider returned HTTP {error.code}: {detail[:500]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError(f"Provider request failed: {error}") from error


def post_json(url: str, payload: Any, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    response = request_json(url, method="POST", payload=payload, headers=headers, timeout=timeout)
    assert isinstance(response, dict)
    return response


def download_bytes(url: str, timeout: int = 120, max_bytes: int = 80 * 1024 * 1024) -> bytes:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LocalVideoStudio/0.6.4"})
        with urllib.request.urlopen(request, timeout=timeout, context=verified_ssl_context()) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ProviderError("Generated asset is larger than the allowed download size")
            content = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProviderError(f"Could not download generated asset: {error}") from error
    if len(content) > max_bytes:
        raise ProviderError("Generated asset is larger than the allowed download size")
    return content
