"""Voice-over from the script through ai33.pro (OpenSpeaker), Ishaq's ElevenLabs "Flint" voice.

The key lives only in Settings (settings.json, never in the code or on GitHub). The API is asynchronous:
POST /v3/text-to-speech returns a task id, GET /v1/task/{id} reports "doing" until the task is "done" with
metadata.audio_url (docs: ai33.pro/docs, "V3 APIs" and "Common Task Flow").
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

BASE = "https://api.ai33.pro"
DEFAULT_VOICE = "elevenlabs_qAZH0aMXY8tw1QufPN0D"  # "Flint"
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 45 * 60  # a 45-minute script is one long task


class VoiceoverError(RuntimeError):
    pass


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        for name, value in fields.items()
    ) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _call(request: urllib.request.Request, timeout: int = 60) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise VoiceoverError(f"ai33 answered HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise VoiceoverError(f"ai33 could not be reached: {error}") from error


def make_voiceover(api_key: str, text: str, destination: Path, voice_id: str = DEFAULT_VOICE, speed: float = 1.0,
                   progress: Callable[[str], None] | None = None, sleep: Callable[[float], None] = time.sleep,
                   opener: Callable[..., Any] | None = None) -> Path:
    """Read `text` aloud with the voice and save the audio to `destination` (its suffix follows the file)."""
    if not api_key.strip():
        raise VoiceoverError("Add the ai33 API key in Settings first.")
    if not text.strip():
        raise VoiceoverError("The script is empty.")
    call = opener or _call
    body, content_type = _multipart({"text": text, "voice_id": voice_id or DEFAULT_VOICE,
                                     "speed": f"{max(0.5, min(1.5, speed)):g}", "with_transcript": "false"})
    created = call(urllib.request.Request(f"{BASE}/v3/text-to-speech", data=body, method="POST",
                                          headers={"xi-api-key": api_key, "Content-Type": content_type}))
    task_id = str(created.get("task_id") or "")
    if not task_id:
        raise VoiceoverError(f"ai33 did not start the voice-over: {str(created)[:200]}")
    waited = 0.0
    while True:
        task = call(urllib.request.Request(f"{BASE}/v1/task/{task_id}",
                                           headers={"xi-api-key": api_key, "Content-Type": "application/json"}))
        status = str(task.get("status") or "")
        if status == "done":
            url = str((task.get("metadata") or {}).get("audio_url") or "")
            if not url:
                raise VoiceoverError("ai33 finished but sent no audio link.")
            break
        if status in ("error", "failed", "cancelled"):
            raise VoiceoverError(f"ai33 could not make the voice-over: {task.get('error_message') or status}")
        if progress:
            progress(f"Making the voice-over: {int(task.get('progress') or 0)}%")
        if waited >= MAX_WAIT_SECONDS:
            raise VoiceoverError("ai33 took longer than 45 minutes; try again later.")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    suffix = Path(url.split("?")[0]).suffix.lower() or ".mp3"
    target = destination.with_suffix(suffix if suffix in (".mp3", ".m4a", ".wav", ".aac", ".ogg") else ".mp3")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=300) as response, partial.open("wb") as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
    except (urllib.error.URLError, TimeoutError) as error:
        partial.unlink(missing_ok=True)
        raise VoiceoverError(f"The voice-over could not be downloaded: {error}") from error
    partial.replace(target)
    return target
