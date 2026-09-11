from __future__ import annotations

import http.client
import json
import math
import mimetypes
import random
import time
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

from .domain import Emotion, NarrativeRole, SceneDraft
from .providers.base import ProviderError
from .providers.http import request_json, verified_ssl_context
from .scene_planner import _compose_prompt, _importance, _timeline_actions
from .themes import ThemePreset


AUDIO_SCENE_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "position": {"type": "integer"},
            "start_seconds": {"type": "number"},
            "end_seconds": {"type": "number"},
            "narration": {"type": "string"},
            "visual_subject": {"type": "string"},
            "emotion": {"type": "string", "enum": [item.value for item in Emotion]},
            "narrative_role": {"type": "string", "enum": [item.value for item in NarrativeRole]},
        },
        "required": [
            "position", "start_seconds", "end_seconds", "narration",
            "visual_subject", "emotion", "narrative_role",
        ],
        "additionalProperties": False,
    },
}


class GeminiAudioClient(Protocol):
    def upload(self, path: Path) -> dict[str, Any]: ...

    def transcribe(self, *, file_uri: str, mime_type: str) -> dict[str, Any]: ...

    def create_scene_plan(self, *, model: str, prompt: str) -> dict[str, Any]: ...

    def delete_file(self, name: str) -> None: ...


class GeminiFilesClient:
    """Small dependency-free Gemini Files + Interactions API client."""

    base_url = "https://generativelanguage.googleapis.com"
    planning_fallback_models = (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )
    transient_markers = (
        "http 408",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "high demand",
        "temporarily unavailable",
        "try again later",
        "timed out",
        "timeout",
    )

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ProviderError("Gemini API key is missing. Add it in Settings before using Precision Sync.")

    @property
    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    def upload(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ProviderError("The uploaded voice-over file cannot be found on this computer.")
        size = path.stat().st_size
        if size <= 0:
            raise ProviderError("The uploaded voice-over file is empty.")
        if size > 2 * 1024 * 1024 * 1024:
            raise ProviderError("Gemini Precision Sync accepts voice-over files up to 2 GB.")

        mime_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        started = request_json(
            f"{self.base_url}/upload/v1beta/files",
            method="POST",
            payload={"file": {"display_name": path.name}},
            headers={
                **self.headers,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime_type,
            },
            timeout=60,
            include_headers=True,
        )
        assert isinstance(started, tuple)
        _, response_headers = started
        upload_url = response_headers.get("x-goog-upload-url")
        if not upload_url:
            raise ProviderError("Gemini did not provide an upload URL for the voice-over.")

        payload = self._stream_upload(upload_url, path, mime_type)
        file_info = payload.get("file") or {}
        name = str(file_info.get("name") or "")
        if not name:
            raise ProviderError(f"Gemini returned invalid voice-over file details: {payload}")
        return self._wait_until_active(name, initial=file_info)

    def _stream_upload(self, upload_url: str, path: Path, mime_type: str) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(upload_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ProviderError("Gemini returned an unsafe voice-over upload URL.")
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=600,
            context=verified_ssl_context(),
        )
        target = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
        try:
            connection.putrequest("POST", target)
            connection.putheader("Content-Type", mime_type)
            connection.putheader("Content-Length", str(path.stat().st_size))
            connection.putheader("X-Goog-Upload-Offset", "0")
            connection.putheader("X-Goog-Upload-Command", "upload, finalize")
            connection.endheaders()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
            response = connection.getresponse()
            content = response.read().decode("utf-8", errors="replace")
            if not 200 <= response.status < 300:
                raise ProviderError(f"Gemini voice-over upload failed (HTTP {response.status}): {content[:500]}")
            return json.loads(content)
        except (OSError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError(f"Gemini voice-over upload failed: {error}") from error
        finally:
            connection.close()

    def _wait_until_active(self, name: str, *, initial: dict[str, Any]) -> dict[str, Any]:
        file_info = initial
        deadline = time.monotonic() + 600
        while str(file_info.get("state") or "ACTIVE").upper() not in {"ACTIVE", "FAILED"}:
            if time.monotonic() >= deadline:
                raise ProviderError("Gemini took too long to process the voice-over. Please retry.")
            time.sleep(1)
            response = request_json(
                f"{self.base_url}/v1beta/{name}", headers=self.headers, timeout=60
            )
            assert isinstance(response, dict)
            file_info = response
        if str(file_info.get("state") or "").upper() == "FAILED":
            raise ProviderError(f"Gemini could not process the voice-over: {file_info.get('error') or 'unknown error'}")
        return file_info

    def transcribe(self, *, file_uri: str, mime_type: str) -> dict[str, Any]:
        payload = {
            "model": "gemini-3.5-transcribe",
            "input": [{"type": "audio", "uri": file_uri, "mime_type": mime_type}],
            "generation_config": {
                "transcription_config": {
                    "language_codes": [],
                    "mode": {"type": "verbatim", "timestamp_granularities": ["word"]},
                }
            },
            "store": False,
        }
        return self._interaction_with_retry(
            payload=payload,
            models=("gemini-3.5-transcribe",),
            preferred_attempts=4,
            timeout=1800,
            task="voice-over transcription",
        )

    def create_scene_plan(self, *, model: str, prompt: str) -> dict[str, Any]:
        payload = {
            "model": model,
            "input": [{"type": "text", "text": prompt}],
            "response_format": AUDIO_SCENE_SCHEMA,
            "generation_config": {
                "temperature": 0.15,
                "thinking_level": "low",
                "max_output_tokens": 65536,
            },
            "store": False,
        }
        models = tuple(dict.fromkeys((model, *self.planning_fallback_models)))
        return self._interaction_with_retry(
            payload=payload,
            models=models,
            preferred_attempts=3,
            timeout=900,
            task="scene planning",
        )

    @classmethod
    def _is_transient_error(cls, error: ProviderError) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in cls.transient_markers)

    def _interaction_with_retry(
        self,
        *,
        payload: dict[str, Any],
        models: tuple[str, ...],
        preferred_attempts: int,
        timeout: int,
        task: str,
    ) -> dict[str, Any]:
        last_error: ProviderError | None = None
        attempted_models: list[str] = []
        for model_index, candidate in enumerate(models):
            attempted_models.append(candidate)
            attempts = preferred_attempts if model_index == 0 else 1
            for attempt in range(attempts):
                request_payload = {**payload, "model": candidate}
                try:
                    response = request_json(
                        f"{self.base_url}/v1beta/interactions",
                        method="POST",
                        payload=request_payload,
                        headers=self.headers,
                        timeout=timeout,
                    )
                    assert isinstance(response, dict)
                    return response
                except ProviderError as error:
                    if not self._is_transient_error(error):
                        raise
                    last_error = error
                    if attempt + 1 < attempts:
                        delay = min(8.0, 2.0 ** attempt) + random.uniform(0.0, 0.35)
                        time.sleep(delay)

        models_text = ", ".join(attempted_models)
        raise ProviderError(
            f"Gemini {task} is temporarily busy after automatic retries across {models_text}. "
            "Your existing scenes were preserved. Please retry in a few minutes."
        ) from last_error

    def delete_file(self, name: str) -> None:
        if not name:
            return
        try:
            request_json(
                f"{self.base_url}/v1beta/{name}",
                method="DELETE",
                headers=self.headers,
                timeout=60,
            )
        except ProviderError:
            # Files expire automatically; cleanup failure must not destroy a
            # successful local plan.
            pass


class GeminiAudioScenePlanner:
    """Build semantic scenes whose boundaries are measured from the real VO."""

    def __init__(self, api_key: str, model: str, client: GeminiAudioClient | None = None):
        self.model = model
        self.client = client or GeminiFilesClient(api_key)

    def plan(
        self,
        *,
        script: str,
        voiceover_path: Path,
        duration_seconds: float,
        target_scene_count: int,
        theme: ThemePreset,
    ) -> list[SceneDraft]:
        if target_scene_count < 1:
            raise ProviderError("Precision Sync needs a target image count of at least 1.")
        if duration_seconds <= 0:
            raise ProviderError("The voice-over duration could not be measured. Re-upload the VO and try again.")

        remote_file: dict[str, Any] | None = None
        try:
            remote_file = self.client.upload(voiceover_path)
            transcription = self.client.transcribe(
                file_uri=str(remote_file.get("uri") or ""),
                mime_type=str(remote_file.get("mimeType") or remote_file.get("mime_type") or "audio/mpeg"),
            )
        finally:
            if remote_file:
                self.client.delete_file(str(remote_file.get("name") or ""))

        timed_transcript = self._extract_timed_transcript(transcription)
        items: list[dict[str, Any]] = []
        for batch in self._planning_batches(timed_transcript, duration_seconds, target_scene_count):
            response = self.client.create_scene_plan(
                model=self.model,
                prompt=self._prompt(
                    script=script,
                    duration=duration_seconds,
                    theme=theme,
                    timed_transcript=batch["transcript"],
                    window_start=batch["window_start"],
                    window_end=batch["window_end"],
                    start_position=batch["start_position"],
                    count=batch["count"],
                ),
            )
            planned = self._extract_items(response)
            if len(planned) != batch["count"]:
                raise ProviderError(
                    f"Gemini returned {len(planned)} scenes for planning batch {batch['number']} instead of "
                    f"{batch['count']}. Nothing was replaced; please retry."
                )
            planned.sort(key=lambda item: int(item.get("position") or 0))
            for offset, item in enumerate(planned):
                item["position"] = batch["start_position"] + offset
            items.extend(planned)
        return self._build_drafts(items, duration_seconds, target_scene_count, theme)

    @staticmethod
    def _planning_batches(
        transcript: list[dict[str, Any]], duration: float, target: int, max_scenes: int = 80
    ) -> list[dict[str, Any]]:
        batch_count = max(1, math.ceil(target / max_scenes))
        base, extra = divmod(target, batch_count)
        batches: list[dict[str, Any]] = []
        position = 1
        completed = 0
        for number in range(1, batch_count + 1):
            count = base + (1 if number <= extra else 0)
            window_start = duration * completed / target
            completed += count
            window_end = duration * completed / target
            selected = [
                segment for segment in transcript
                if float(segment["end_seconds"]) >= window_start and float(segment["start_seconds"]) <= window_end
            ]
            if not selected:
                selected = transcript
            batches.append({
                "number": number,
                "start_position": position,
                "count": count,
                "window_start": round(window_start, 3),
                "window_end": round(window_end, 3),
                "transcript": selected,
            })
            position += count
        return batches

    @staticmethod
    def _offset_seconds(value: Any) -> float:
        text = str(value or "").strip().lower()
        if text.endswith("s"):
            text = text[:-1]
        try:
            return float(text)
        except ValueError as error:
            raise ProviderError(f"Gemini returned an invalid word timestamp: {value}") from error

    @classmethod
    def _extract_timed_transcript(cls, response: dict[str, Any]) -> list[dict[str, Any]]:
        words: list[dict[str, Any]] = []
        for step in response.get("steps") or []:
            for content in step.get("content") or []:
                for annotation in content.get("annotations") or []:
                    if annotation.get("type") != "word_info":
                        continue
                    text = str(annotation.get("text") or "").strip()
                    if not text:
                        continue
                    start = cls._offset_seconds(annotation.get("start_offset"))
                    end = cls._offset_seconds(annotation.get("end_offset"))
                    if math.isfinite(start) and math.isfinite(end) and end >= start:
                        words.append({"text": text, "start": start, "end": end})
        if not words:
            raise ProviderError(
                "Gemini transcription returned no word timestamps. Nothing was replaced; please retry Precision Sync."
            )

        segments: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for word in words:
            pause = word["start"] - current[-1]["end"] if current else 0
            if current and (pause >= 0.8 or len(current) >= 36):
                segments.append(cls._timed_segment(current))
                current = []
            current.append(word)
            if len(current) >= 5 and str(word["text"]).endswith((".", "?", "!")):
                segments.append(cls._timed_segment(current))
                current = []
        if current:
            segments.append(cls._timed_segment(current))
        return segments

    @staticmethod
    def _timed_segment(words: list[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(str(word["text"]) for word in words)
        for punctuation in (".", ",", "!", "?", ":", ";"):
            text = text.replace(f" {punctuation}", punctuation)
        return {
            "start_seconds": round(float(words[0]["start"]), 3),
            "end_seconds": round(float(words[-1]["end"]), 3),
            "text": text,
        }

    @staticmethod
    def _extract_items(response: dict[str, Any]) -> list[dict[str, Any]]:
        for step in reversed(response.get("steps") or []):
            if step.get("type") != "model_output":
                continue
            for content in step.get("content") or []:
                if content.get("type") != "text":
                    continue
                try:
                    value = json.loads(str(content.get("text") or ""))
                except json.JSONDecodeError as error:
                    raise ProviderError("Gemini returned an unreadable Precision Sync plan.") from error
                if isinstance(value, list):
                    return value
        raise ProviderError(f"Gemini returned no timestamped scene plan: {response}")

    @staticmethod
    def _build_drafts(
        items: list[dict[str, Any]],
        duration: float,
        target: int,
        theme: ThemePreset,
    ) -> list[SceneDraft]:
        if len(items) != target:
            raise ProviderError(
                f"Gemini returned {len(items)} timed scenes instead of {target}. Nothing was replaced; please retry."
            )

        ordered = sorted(items, key=lambda item: int(item.get("position") or 0))
        raw_ranges: list[tuple[float, float]] = []
        for expected, item in enumerate(ordered, start=1):
            try:
                start = float(item["start_seconds"])
                end = float(item["end_seconds"])
            except (KeyError, TypeError, ValueError) as error:
                raise ProviderError(f"Gemini returned invalid timing for scene {expected}.") from error
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                raise ProviderError(f"Gemini returned an invalid time range for scene {expected}.")
            if not str(item.get("narration") or "").strip() or not str(item.get("visual_subject") or "").strip():
                raise ProviderError(f"Gemini returned an incomplete description for scene {expected}.")
            raw_ranges.append((start, end))

        if raw_ranges[0][0] > max(5.0, duration * 0.02) or raw_ranges[-1][1] < duration - max(5.0, duration * 0.02):
            raise ProviderError("Gemini did not cover the complete voice-over. Nothing was replaced; please retry.")
        if any(raw_ranges[index][0] + 1 < raw_ranges[index - 1][0] for index in range(1, target)):
            raise ProviderError("Gemini returned scene timestamps out of order. Nothing was replaced; please retry.")

        boundaries = [0.0]
        minimum = min(0.25, duration / max(1, target * 4))
        for index in range(target - 1):
            proposed = (raw_ranges[index][1] + raw_ranges[index + 1][0]) / 2
            earliest = boundaries[-1] + minimum
            latest = duration - minimum * (target - index - 1)
            boundaries.append(min(latest, max(earliest, proposed)))
        boundaries.append(duration)

        drafts: list[SceneDraft] = []
        for index, item in enumerate(ordered, start=1):
            try:
                emotion = Emotion(str(item["emotion"]))
                role = NarrativeRole(str(item["narrative_role"]))
            except (KeyError, ValueError) as error:
                raise ProviderError(f"Gemini returned invalid direction for scene {index}.") from error
            narration = str(item["narration"]).strip()
            subject = str(item["visual_subject"]).strip()
            drafts.append(
                SceneDraft(
                    position=index,
                    start_seconds=round(boundaries[index - 1], 3),
                    end_seconds=round(boundaries[index], 3),
                    narration=narration,
                    visual_subject=subject,
                    emotion=emotion,
                    narrative_role=role,
                    importance=_importance(role, emotion),
                    prompt=_compose_prompt(subject, emotion, theme.id, index),
                    negative_prompt=theme.negative_prompt,
                    model_role="photoreal",
                    candidate_count=1,
                    timeline_actions=_timeline_actions(emotion),
                )
            )
        return drafts

    @staticmethod
    def _prompt(
        *, script: str, duration: float, theme: ThemePreset,
        timed_transcript: list[dict[str, Any]], window_start: float,
        window_end: float, start_position: int, count: int,
    ) -> str:
        end_position = start_position + count - 1
        script_excerpt = GeminiAudioScenePlanner._script_excerpt(
            script, window_start, window_end, duration
        )
        return (
            "You are planning an image-led documentary using a transcript measured from the real voice-over with word-level "
            "timestamps. Read the complete supplied time window and authoritative script excerpt before deciding any boundary. Return "
            "exactly the requested number of chronological scenes.\n\n"
            "TIMING RULES\n"
            "- Use the supplied real word timings, pauses, sentence endings and topic changes. Never distribute time evenly.\n"
            f"- Scene {start_position} begins at {window_start:.3f}. Scene {end_position} ends at {window_end:.3f}. "
            "Cover every spoken moment in this window without gaps.\n"
            "- Put a boundary where the visible subject or action changes. Do not cut in the middle of a spoken phrase.\n"
            "- start_seconds and end_seconds are decimal seconds measured from the audio.\n"
            "- narration must be the exact contiguous words spoken in that time range and must stay in source order.\n\n"
            "VISUAL RULES\n"
            "- visual_subject must describe the literal, specific subject/action/object the viewer should see in 10-35 words.\n"
            "- Include named foods, people, place, era and action when the narration supplies them. Avoid generic filler.\n"
            "- Do not invent brands, facts, ingredients, locations or historical details absent from the script.\n"
            "- Give adjacent scenes visibly different compositions while keeping the channel style consistent.\n"
            "- emotion and narrative_role must use only the allowed schema values.\n\n"
            f"REQUESTED SCENES IN THIS BATCH: {count}\nPOSITION RANGE: {start_position}-{end_position}\n"
            f"AUDIO WINDOW: {window_start:.3f}-{window_end:.3f} seconds\nFULL AUDIO DURATION: {duration:.3f} seconds\n"
            f"CHANNEL STYLE: {theme.visual_style}\nPALETTE: {theme.palette}\nCAMERA: {theme.camera_language}\n\n"
            "AUTHORITATIVE SCRIPT EXCERPT (use for exact wording and factual details):\n"
            f"{script_excerpt}\n\nTIMED VO TRANSCRIPT FOR THIS WINDOW (use these measured boundaries):\n"
            f"{json.dumps(timed_transcript, ensure_ascii=False, separators=(',', ':'))}"
        )

    @staticmethod
    def _script_excerpt(script: str, start: float, end: float, duration: float) -> str:
        if not script or duration <= 0:
            return script
        padding = min(1200, max(200, len(script) // 100))
        first = max(0, int(len(script) * start / duration) - padding)
        last = min(len(script), int(len(script) * end / duration) + padding)
        if first:
            next_space = script.find(" ", first)
            first = next_space + 1 if next_space >= 0 else first
        if last < len(script):
            previous_space = script.rfind(" ", 0, last)
            last = previous_space if previous_space >= 0 else last
        return script[first:last].strip()
