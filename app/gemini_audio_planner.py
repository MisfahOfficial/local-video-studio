from __future__ import annotations

import http.client
import json
import math
import mimetypes
import random
import re
import time
import urllib.parse
from bisect import bisect_left
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from .domain import Emotion, NarrativeRole, SceneDraft
from .providers.base import ProviderError
from .providers.http import request_json, verified_ssl_context
from .scene_planner import (
    _compose_prompt,
    _importance,
    _is_pop_insert,
    _timeline_actions,
    scene_duration_limit,
)
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
        target_scene_count: int | None,
        theme: ThemePreset,
    ) -> list[SceneDraft]:
        if target_scene_count is not None and target_scene_count < 1:
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

        timed_transcript = self._extract_timed_transcript(transcription, script=script)
        scene_guides = None
        if target_scene_count is None:
            scene_guides = self._adaptive_scene_guides(timed_transcript, duration_seconds)
            target_scene_count = len(scene_guides)
        items: list[dict[str, Any]] = []
        for batch in self._planning_batches(
            timed_transcript,
            duration_seconds,
            target_scene_count,
            scene_guides=scene_guides,
        ):
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
                    scene_guides=batch.get("scene_guides"),
                ),
            )
            planned = self._extract_items(response)
            if len(planned) != batch["count"]:
                raise ProviderError(
                    f"Gemini returned {len(planned)} scenes for planning batch {batch['number']} instead of "
                    f"{batch['count']}. Nothing was replaced; please retry."
                )
            planned.sort(key=lambda item: int(item.get("position") or 0))
            if batch.get("scene_guides"):
                self._validate_guided_alignment(
                    planned,
                    batch["scene_guides"],
                    int(batch["number"]),
                )
            else:
                self._validate_batch_alignment(
                    planned,
                    batch["transcript"],
                    float(batch["window_start"]),
                    float(batch["window_end"]),
                    int(batch["number"]),
                )
            for offset, item in enumerate(planned):
                item["position"] = batch["start_position"] + offset
            items.extend(planned)
        return self._build_drafts(items, duration_seconds, target_scene_count, theme)

    @staticmethod
    def _planning_batches(
        transcript: list[dict[str, Any]], duration: float, target: int, max_scenes: int = 80,
        scene_guides: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not transcript:
            raise ProviderError("Precision Sync received an empty timed transcript.")
        if scene_guides:
            batches: list[dict[str, Any]] = []
            for batch_number, guide_start in enumerate(range(0, len(scene_guides), max_scenes), start=1):
                guides = scene_guides[guide_start:guide_start + max_scenes]
                segment_start = int(guides[0]["segment_start"])
                segment_end = int(guides[-1]["segment_end"])
                batches.append({
                    "number": batch_number,
                    "start_position": int(guides[0]["position"]),
                    "count": len(guides),
                    "window_start": float(guides[0]["start_seconds"]),
                    "window_end": float(guides[-1]["end_seconds"]),
                    "transcript": transcript[segment_start:segment_end],
                    "scene_guides": guides,
                })
            return batches
        if target > len(transcript):
            raise ProviderError(
                f"Precision Sync found {len(transcript)} complete spoken sentences or pauses, but {target} images were "
                "requested. Lower the image target so the tool does not change visuals before a sentence is complete."
            )

        batch_count = max(1, math.ceil(target / max_scenes))
        base, extra = divmod(target, batch_count)
        batches: list[dict[str, Any]] = []
        position = 1
        segment_cursor = 0
        completed_scenes = 0
        window_start = 0.0

        def separator_after(segment_index: int) -> float:
            """Return a safe clock boundary after a complete transcript unit."""
            if segment_index >= len(transcript):
                return duration
            left_end = float(transcript[segment_index - 1]["end_seconds"])
            right_start = float(transcript[segment_index]["start_seconds"])
            return min(duration, max(0.0, (left_end + right_start) / 2))

        for number in range(1, batch_count + 1):
            count = base + (1 if number <= extra else 0)
            completed_scenes += count
            remaining_scenes = target - completed_scenes

            if number == batch_count:
                segment_end = len(transcript)
            else:
                # Each requested scene needs at least one complete transcript unit.
                # Choose the legal unit boundary closest to the proportional audio
                # time instead of cutting the VO at an arbitrary clock position.
                minimum_end = segment_cursor + count
                maximum_end = len(transcript) - remaining_scenes
                ideal_end = duration * completed_scenes / target
                segment_end = min(
                    range(minimum_end, maximum_end + 1),
                    key=lambda candidate: abs(separator_after(candidate) - ideal_end),
                )

            selected = transcript[segment_cursor:segment_end]
            window_end = separator_after(segment_end)
            batches.append({
                "number": number,
                "start_position": position,
                "count": count,
                "window_start": round(window_start, 3),
                "window_end": round(window_end, 3),
                "transcript": selected,
            })
            position += count
            segment_cursor = segment_end
            window_start = window_end
        return batches

    @staticmethod
    def _adaptive_scene_guides(
        transcript: list[dict[str, Any]], duration: float
    ) -> list[dict[str, Any]]:
        """Create sentence-safe scene slots using the automatic pacing curve."""
        if not transcript:
            raise ProviderError("Precision Sync received an empty timed transcript.")

        groups: list[tuple[int, int, bool]] = []
        group_start: int | None = None
        for index, segment in enumerate(transcript):
            segment_start = float(segment["start_seconds"])
            segment_end = float(segment["end_seconds"])
            pop_insert = _is_pop_insert(str(segment.get("text") or ""), segment_end - segment_start)

            if pop_insert:
                if group_start is not None:
                    groups.append((group_start, index, False))
                    group_start = None
                groups.append((index, index + 1, True))
                continue

            if group_start is None:
                group_start = index
                continue

            start_time = 0.0 if not groups and group_start == 0 else float(transcript[group_start]["start_seconds"])
            if segment_end - start_time > scene_duration_limit(start_time):
                groups.append((group_start, index, False))
                group_start = index

        if group_start is not None:
            groups.append((group_start, len(transcript), False))

        boundaries = [0.0]
        for index in range(1, len(groups)):
            left_start, left_end, left_pop = groups[index - 1]
            right_start, _, right_pop = groups[index]
            left_time = float(transcript[left_end - 1]["end_seconds"])
            right_time = float(transcript[right_start]["start_seconds"])
            if left_pop and not right_pop:
                boundary = left_time
            else:
                # Start ordinary and pop-in scenes on their first spoken word.
                # This assigns the preceding pause to the previous visual and
                # keeps the new scene inside its strict pacing limit.
                boundary = right_time
            boundaries.append(max(boundaries[-1], min(duration, boundary)))
        boundaries.append(duration)

        guides: list[dict[str, Any]] = []
        for position, (group, start, end) in enumerate(
            zip(groups, boundaries, boundaries[1:]), start=1
        ):
            segment_start, segment_end, pop_insert = group
            narration = " ".join(
                str(segment.get("text") or "").strip()
                for segment in transcript[segment_start:segment_end]
            ).strip()
            guides.append({
                "position": position,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "narration": narration,
                "pop_insert": pop_insert and end - start <= 2.1,
                "segment_start": segment_start,
                "segment_end": segment_end,
            })
        return guides

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
    def _extract_timed_transcript(
        cls, response: dict[str, Any], *, script: str = ""
    ) -> list[dict[str, Any]]:
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

        cls._mark_script_sentence_endings(words, script)

        segments: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for word in words:
            pause = word["start"] - current[-1]["end"] if current else 0
            if current and pause >= 0.8:
                segments.append(cls._timed_segment(current))
                current = []
            current.append(word)
            if word.get("script_sentence_end") or re.search(
                r"[.!?]+[\"'”’)]*$", str(word["text"]).strip()
            ):
                segments.append(cls._timed_segment(current))
                current = []
        if current:
            segments.append(cls._timed_segment(current))
        return segments

    @staticmethod
    def _mark_script_sentence_endings(words: list[dict[str, Any]], script: str) -> None:
        """Project authoritative script punctuation onto unpunctuated ASR words."""
        if not script.strip():
            return

        script_parts: list[tuple[str, str]] = []
        for match in re.finditer(r"[\w'-]+(?:[.!?]+[\"'”’)]*)?", script):
            raw = match.group(0)
            token = re.sub(r"[\W_]+", "", raw.casefold())
            if token:
                script_parts.append((token, raw))
        transcript_tokens = [
            re.sub(r"[\W_]+", "", str(word.get("text") or "").casefold())
            for word in words
        ]
        if not script_parts or not any(transcript_tokens):
            return

        matcher = SequenceMatcher(
            None,
            [token for token, _ in script_parts],
            transcript_tokens,
            autojunk=True,
        )
        script_to_transcript: dict[int, int] = {}
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                script_to_transcript[block.a + offset] = block.b + offset
        if not script_to_transcript:
            return

        mapped_script_indices = sorted(script_to_transcript)
        previous_transcript_end = -1
        for script_index, (_, raw) in enumerate(script_parts):
            punctuation = re.search(r"([.!?]+)[\"'”’)]*$", raw)
            if not punctuation:
                continue

            transcript_index = script_to_transcript.get(script_index)
            if transcript_index is None:
                insertion = bisect_left(mapped_script_indices, script_index)
                estimates: list[tuple[int, int]] = []
                if insertion:
                    left_script = mapped_script_indices[insertion - 1]
                    distance = script_index - left_script
                    if distance <= 4:
                        estimates.append((distance, script_to_transcript[left_script] + distance))
                if insertion < len(mapped_script_indices):
                    right_script = mapped_script_indices[insertion]
                    distance = right_script - script_index
                    if distance <= 4:
                        estimates.append((distance, script_to_transcript[right_script] - distance))
                if not estimates:
                    continue
                transcript_index = min(estimates)[1]

            transcript_index = min(len(words) - 1, max(previous_transcript_end + 1, transcript_index))
            words[transcript_index]["script_sentence_end"] = True
            spoken_word = str(words[transcript_index].get("text") or "").rstrip()
            if not re.search(r"[.!?]+[\"'”’)]*$", spoken_word):
                words[transcript_index]["text"] = spoken_word + punctuation.group(1)
            previous_transcript_end = transcript_index

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
    def _tokens(value: Any, *, significant: bool = False) -> set[str]:
        tokens = set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
        if not significant:
            return tokens
        ignored = {
            "the", "and", "that", "this", "with", "from", "into", "onto", "were", "was", "are", "is",
            "for", "but", "then", "than", "when", "where", "while", "before", "after", "through", "their",
            "there", "they", "them", "his", "her", "its", "our", "your", "you", "one", "two", "first",
            "last", "old", "new", "scene", "image", "shot", "view", "showing", "visible", "person", "people",
        }
        return {token for token in tokens if len(token) >= 3 and token not in ignored}

    @classmethod
    def _validate_batch_alignment(
        cls,
        planned: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
        window_start: float,
        window_end: float,
        batch_number: int,
    ) -> None:
        """Snap valid plans to complete utterances and reject mid-sentence cuts."""
        if len(planned) > len(transcript):
            raise ProviderError(
                f"Planning batch {batch_number} requested more visual scenes than complete spoken sentences. "
                "Nothing was replaced; lower the image target and retry Precision Sync."
            )

        previous_start = window_start
        for local_position, item in enumerate(planned, start=1):
            try:
                start = float(item["start_seconds"])
                end = float(item["end_seconds"])
            except (KeyError, TypeError, ValueError) as error:
                raise ProviderError(
                    f"Gemini returned invalid timing in planning batch {batch_number}, scene {local_position}."
                ) from error
            if (
                not math.isfinite(start) or not math.isfinite(end) or end <= start
                or start < window_start - 1.0 or end > window_end + 1.0
                or start + 0.25 < previous_start
            ):
                raise ProviderError(
                    f"Gemini placed scene {local_position} of planning batch {batch_number} outside its spoken audio window. "
                    "Nothing was replaced; retry Precision Sync."
                )
            previous_start = start

        # Legal visual changes are only the gaps between complete transcript
        # sentences/utterances. Snap small model timing drift to those boundaries,
        # but never accept a new plan that starts halfway through a sentence.
        separators = [
            (
                float(transcript[index - 1]["end_seconds"])
                + float(transcript[index]["start_seconds"])
            ) / 2
            for index in range(1, len(transcript))
        ]
        cut_indices = [0]
        snapped_boundaries = [window_start]
        for boundary_index in range(1, len(planned)):
            proposed = (
                float(planned[boundary_index - 1]["end_seconds"])
                + float(planned[boundary_index]["start_seconds"])
            ) / 2
            minimum_cut = cut_indices[-1] + 1
            maximum_cut = len(transcript) - (len(planned) - boundary_index)

            def distance_from_pause(candidate: int) -> float:
                left_end = float(transcript[candidate - 1]["end_seconds"])
                right_start = float(transcript[candidate]["start_seconds"])
                pause_start, pause_end = sorted((left_end, right_start))
                if pause_start <= proposed <= pause_end:
                    return 0.0
                return min(abs(proposed - pause_start), abs(proposed - pause_end))

            cut = min(
                range(minimum_cut, maximum_cut + 1),
                key=distance_from_pause,
            )
            snapped = separators[cut - 1]
            if distance_from_pause(cut) > 1.0:
                raise ProviderError(
                    f"Gemini tried to change the visual before a sentence was complete in planning batch "
                    f"{batch_number}, scene {boundary_index}. Nothing was replaced; retry Precision Sync."
                )
            cut_indices.append(cut)
            snapped_boundaries.append(snapped)
        cut_indices.append(len(transcript))
        snapped_boundaries.append(window_end)

        for local_position, item in enumerate(planned, start=1):
            first_segment = cut_indices[local_position - 1]
            last_segment = cut_indices[local_position]
            spoken = " ".join(
                str(segment.get("text") or "").strip()
                for segment in transcript[first_segment:last_segment]
            ).strip()
            narration_tokens = cls._tokens(item.get("narration"))
            spoken_tokens = cls._tokens(spoken)
            comparable = min(len(narration_tokens), len(spoken_tokens))
            shared_tokens = len(narration_tokens & spoken_tokens)
            overlap = shared_tokens / comparable if comparable else 0.0
            if comparable >= 4 and overlap < 0.35:
                raise ProviderError(
                    f"Gemini assigned scene {local_position} of planning batch {batch_number} to the wrong spoken passage. "
                    "Nothing was replaced; retry Precision Sync."
                )
            if len(spoken_tokens) >= 4 and shared_tokens / len(spoken_tokens) < 0.65:
                raise ProviderError(
                    f"Gemini returned an incomplete spoken sentence for scene {local_position} of planning batch "
                    f"{batch_number}. Nothing was replaced; retry Precision Sync."
                )
            item["start_seconds"] = round(snapped_boundaries[local_position - 1], 3)
            item["end_seconds"] = round(snapped_boundaries[local_position], 3)
            item["narration"] = spoken

            narration_subjects = cls._tokens(spoken, significant=True)
            visual_subjects = cls._tokens(item.get("visual_subject"), significant=True)
            if len(narration_subjects) >= 2 and not narration_subjects.intersection(visual_subjects):
                # A lexical mismatch is not reliable proof of a semantic mismatch:
                # a good visual can use synonyms. Do not discard a complete plan.
                # Falling back to the exact timed narration is deterministic and
                # guarantees that image generation stays attached to the VO.
                item["visual_subject"] = spoken

    @classmethod
    def _validate_guided_alignment(
        cls,
        planned: list[dict[str, Any]],
        guides: list[dict[str, Any]],
        batch_number: int,
    ) -> None:
        """Keep Gemini's visual direction but enforce the automatic timing guide."""
        if len(planned) != len(guides):
            raise ProviderError(
                f"Gemini returned the wrong number of auto-paced scenes in planning batch {batch_number}."
            )
        for local_position, (item, guide) in enumerate(zip(planned, guides), start=1):
            spoken = str(guide.get("narration") or "").strip()
            narration_tokens = cls._tokens(item.get("narration"))
            spoken_tokens = cls._tokens(spoken)
            comparable = min(len(narration_tokens), len(spoken_tokens))
            shared_tokens = len(narration_tokens & spoken_tokens)
            overlap = shared_tokens / comparable if comparable else 0.0
            if comparable >= 4 and overlap < 0.35:
                raise ProviderError(
                    f"Gemini assigned auto-paced scene {local_position} of planning batch {batch_number} to the "
                    "wrong spoken passage. Nothing was replaced; retry Precision Sync."
                )
            if len(spoken_tokens) >= 4 and shared_tokens / len(spoken_tokens) < 0.65:
                raise ProviderError(
                    f"Gemini returned an incomplete spoken sentence for auto-paced scene {local_position} of planning "
                    f"batch {batch_number}. Nothing was replaced; retry Precision Sync."
                )

            item["start_seconds"] = float(guide["start_seconds"])
            item["end_seconds"] = float(guide["end_seconds"])
            item["narration"] = spoken
            item["_pop_insert"] = bool(guide.get("pop_insert"))

            narration_subjects = cls._tokens(spoken, significant=True)
            visual_subjects = cls._tokens(item.get("visual_subject"), significant=True)
            if len(narration_subjects) >= 2 and not narration_subjects.intersection(visual_subjects):
                item["visual_subject"] = spoken

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
            pop_insert = bool(item.get("_pop_insert")) or _is_pop_insert(
                narration, boundaries[index] - boundaries[index - 1]
            )
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
                    prompt=_compose_prompt(
                        subject,
                        emotion,
                        theme.id,
                        index,
                        spoken_context=narration,
                        pop_insert=pop_insert,
                    ),
                    negative_prompt=theme.negative_prompt,
                    model_role="photoreal",
                    candidate_count=1,
                    timeline_actions=_timeline_actions(emotion, pop_insert=pop_insert),
                )
            )
        return drafts

    @staticmethod
    def _prompt(
        *, script: str, duration: float, theme: ThemePreset,
        timed_transcript: list[dict[str, Any]], window_start: float,
        window_end: float, start_position: int, count: int,
        scene_guides: list[dict[str, Any]] | None = None,
    ) -> str:
        end_position = start_position + count - 1
        script_excerpt = GeminiAudioScenePlanner._script_excerpt(
            script, window_start, window_end, duration
        )
        guide_text = ""
        if scene_guides:
            public_guides = [
                {
                    "position": guide["position"],
                    "start_seconds": guide["start_seconds"],
                    "end_seconds": guide["end_seconds"],
                    "narration": guide["narration"],
                    "pop_insert": guide["pop_insert"],
                }
                for guide in scene_guides
            ]
            guide_text = (
                "\nAUTO-PACED SCENE GUIDE (copy each position, time range and narration exactly; direct only the visual):\n"
                f"{json.dumps(public_guides, ensure_ascii=False, separators=(',', ':'))}\n"
            )
        return (
            "You are planning an image-led documentary using a transcript measured from the real voice-over with word-level "
            "timestamps. Read the complete supplied time window and authoritative script excerpt before deciding any boundary. Return "
            "exactly the requested number of chronological scenes.\n\n"
            "TIMING RULES\n"
            "- Use the supplied real word timings, pauses, sentence endings and topic changes. Never distribute time evenly.\n"
            f"- Scene {start_position} begins at {window_start:.3f}. Scene {end_position} ends at {window_end:.3f}. "
            "Cover every spoken moment in this window without gaps.\n"
            "- Every scene must contain one or more complete TIMED VO TRANSCRIPT entries. A visual may change only between "
            "entries, after the current sentence or clearly paused utterance is complete.\n"
            "- Put a boundary where the visible subject or action changes, but never cut in the middle of a sentence or "
            "start a new visual for an unfinished thought.\n"
            "- start_seconds and end_seconds are decimal seconds measured from the audio.\n"
            "- narration must concatenate the complete transcript entries assigned to that scene, using the exact contiguous "
            "words in source order.\n\n"
            "AUTO PACING\n"
            "- When an AUTO-PACED SCENE GUIDE is supplied, copy its timing and narration exactly.\n"
            "- Main scenes use a maximum of 5 seconds through minute 20, 8 seconds through minute 40, and 10 seconds "
            "after minute 40 whenever complete-sentence boundaries allow.\n"
            "- A pop_insert is a self-contained object/detail beat of at most 2 seconds. Direct it as a bold centered item "
            "that can pop into the existing visual rhythm; do not turn it into an unrelated new topic.\n\n"
            "VISUAL RULES\n"
            "- visual_subject must describe the literal, specific subject/action/object the viewer should see in 10-35 words.\n"
            "- Reuse at least one concrete noun or named phrase from narration in visual_subject, then add composition details.\n"
            "- Include named foods, people, place, era and action when the narration supplies them. Avoid generic filler.\n"
            "- Do not invent brands, facts, ingredients, locations or historical details absent from the script.\n"
            "- Give adjacent scenes visibly different compositions while keeping the channel style consistent.\n"
            "- emotion and narrative_role must use only the allowed schema values.\n\n"
            f"REQUESTED SCENES IN THIS BATCH: {count}\nPOSITION RANGE: {start_position}-{end_position}\n"
            f"AUDIO WINDOW: {window_start:.3f}-{window_end:.3f} seconds\nFULL AUDIO DURATION: {duration:.3f} seconds\n"
            f"CHANNEL STYLE: {theme.visual_style}\nPALETTE: {theme.palette}\nCAMERA: {theme.camera_language}\n\n"
            f"{guide_text}"
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
