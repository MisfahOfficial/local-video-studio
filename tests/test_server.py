from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.database import Database
from app.paths import AppPaths
from app.server import ApiError, create_server, normalize_render_options


class ServerTests(unittest.TestCase):
    def test_export_options_are_normalized_and_h264_safe(self) -> None:
        options = normalize_render_options({
            "width": 2560, "height": 1440, "fps": 60,
            "video_bitrate_kbps": 24_000, "audio_bitrate_kbps": 256,
            "output_name": "Finished Episode", "output_directory": "/tmp/exports",
            "caption_style": {"max_lines": 1, "words_per_line": 5},
        })
        self.assertEqual(options["output_name"], "Finished Episode")
        self.assertEqual(options["video_bitrate_kbps"], 24_000)
        self.assertEqual(options["caption_style"]["max_lines"], 1)
        with self.assertRaises(ApiError):
            normalize_render_options({"width": 1921, "height": 1080})

    def test_local_http_health_project_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = AppPaths(
                root=root,
                database=root / "studio.sqlite3",
                projects=root / "projects",
                settings=root / "settings.json",
                static=Path(__file__).parents[1] / "app" / "static",
            )
            paths.projects.mkdir(parents=True)
            server = create_server(paths, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                health = self._request(f"{base}/api/health")
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["version"], "0.7.0")
                self.assertEqual(health["schema_version"], 4)
                font_request = urllib.request.Request(
                    f"{base}/api/fonts/upload", data=b"\x00\x01\x00\x00font-data", method="POST",
                    headers={"X-Filename": "Channel-Font.ttf", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(font_request, timeout=3) as response:
                    font = json.loads(response.read().decode("utf-8"))["font"]
                self.assertTrue(font["custom"])
                self.assertIn(font["family"], [item["family"] for item in self._request(f"{base}/api/fonts")["fonts"]])
                project = self._request(
                    f"{base}/api/projects",
                    method="POST",
                    payload={"name": "API smoke", "theme_id": "us_nostalgia"},
                )
                plan = self._request(
                    f"{base}/api/projects/{project['id']}/plan",
                    method="POST",
                    payload={
                        "script": "First the warm kitchen opened. Then an old recipe revealed the truth.",
                        "duration_seconds": 10,
                        "image_count": 2,
                        "theme_id": "us_nostalgia",
                    },
                )
                self.assertEqual(len(plan["scenes"]), 2)
                self.assertEqual(plan["generation_count"], 2)
                self.assertIn("warnings", plan)
                first_scene, second_scene = plan["scenes"]
                updated_scene = self._request(
                    f"{base}/api/scenes/{first_scene['id']}", method="PATCH",
                    payload={"duration_seconds": 7.5, "caption_text": "Edited caption"},
                )
                self.assertEqual(updated_scene["caption_text"], "Edited caption")
                project_payload = self._request(f"{base}/api/projects/{project['id']}")
                self.assertEqual(project_payload["scenes"][1]["start_seconds"], 7.5)
                self.assertEqual(len(project_payload["timeline_clips"]), 2)
                caption_style = self._request(
                    f"{base}/api/projects/{project['id']}/caption-style", method="POST",
                    payload={"font": "Georgia", "size": 48, "position": "top", "text_color": "#FFFFFF", "background_color": "#000000", "background_opacity": 0.6, "stroke_enabled": True, "stroke_width": 4, "alignment": "right", "scale": 110},
                )
                self.assertEqual(caption_style["caption_style"]["position"], "top")
                self.assertEqual(caption_style["caption_style"]["alignment"], "right")
                self.assertEqual(caption_style["caption_style"]["scale"], 110)
                reordered = self._request(
                    f"{base}/api/projects/{project['id']}/scenes/reorder", method="POST",
                    payload={"scene_ids": [second_scene["id"], first_scene["id"]]},
                )
                self.assertEqual(reordered["scenes"][0]["id"], second_scene["id"])

                timeline = self._request(f"{base}/api/projects/{project['id']}/timeline")["timeline_clips"]
                reordered_clips = self._request(
                    f"{base}/api/projects/{project['id']}/timeline/reorder", method="POST",
                    payload={"clip_ids": [timeline[1]["id"], timeline[0]["id"]]},
                )["timeline_clips"]
                self.assertEqual(reordered_clips[0]["id"], timeline[1]["id"])
                trimmed_clips = self._request(
                    f"{base}/api/timeline-clips/{reordered_clips[0]['id']}", method="PATCH",
                    payload={"duration_seconds": 4.0, "source_in_seconds": 0.5},
                )["timeline_clips"]
                self.assertEqual(trimmed_clips[0]["end_seconds"], 4.0)
                split = self._request(
                    f"{base}/api/timeline-clips/{trimmed_clips[0]['id']}/split", method="POST",
                    payload={"offset_seconds": 2.0},
                )
                self.assertEqual(len(split["timeline_clips"]), 3)
                deleted = self._request(
                    f"{base}/api/timeline-clips/{split['new_clip_id']}/delete", method="POST", payload={},
                )
                self.assertEqual(len(deleted["timeline_clips"]), 2)
                Database(paths.database).update_project(
                    project["id"], voiceover_path="/tmp/test-voiceover.mp3", duration_seconds=10.0
                )
                unsynced = self._request(f"{base}/api/projects/{project['id']}")["timeline_sync"]
                self.assertEqual(unsynced["status"], "short")
                fitted = self._request(
                    f"{base}/api/projects/{project['id']}/timeline/fit-voiceover",
                    method="POST", payload={},
                )
                self.assertEqual(fitted["timeline_sync"]["status"], "synced")
                self.assertAlmostEqual(fitted["timeline_clips"][-1]["end_seconds"], 10.0)

                upload_request = urllib.request.Request(
                    f"{base}/api/scenes/{first_scene['id']}/asset",
                    data=b"replacement-image-data", method="POST",
                    headers={"X-Filename": "replacement.png", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(upload_request, timeout=3) as response:
                    uploaded = json.loads(response.read().decode("utf-8"))
                self.assertEqual(uploaded["asset"]["provider"], "local")
                self.assertEqual(uploaded["scene"]["selected_asset_id"], uploaded["asset"]["id"])
                with patch("app.server.YouTubeSourceService.search", return_value=[{
                    "video_id": "abcdefghijk", "title": "Licensed fleet footage",
                    "channel": "Archive", "license": "creativeCommon",
                }]):
                    youtube_results = self._request(
                        f"{base}/api/youtube/search?scene_id={first_scene['id']}&q=historic+fleet"
                    )
                self.assertEqual(youtube_results["results"][0]["video_id"], "abcdefghijk")

                def fake_source_clip(**kwargs):
                    kwargs["destination"].parent.mkdir(parents=True, exist_ok=True)
                    kwargs["destination"].write_bytes(b"mock-youtube-video")
                    return {
                        "video_id": kwargs["video_id"], "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                        "title": "Licensed fleet footage", "channel": "Archive", "license": "Creative Commons",
                        "source_start_seconds": 12.0, "source_end_seconds": 19.5, "matched_from_captions": True,
                    }

                with patch("app.server.YouTubeSourceService.source_clip", side_effect=fake_source_clip):
                    youtube_asset = self._request(
                        f"{base}/api/scenes/{first_scene['id']}/youtube-source", method="POST",
                        payload={"video_id": "abcdefghijk"},
                    )
                self.assertEqual(youtube_asset["asset"]["provider"], "youtube")
                self.assertEqual(youtube_asset["asset"]["media_kind"], "video")
                self.assertEqual(youtube_asset["asset"]["metadata"]["source_start_seconds"], 12.0)
                range_request = urllib.request.Request(
                    f"{base}{uploaded['asset']['media_url']}", headers={"Range": "bytes=0-3"},
                )
                with urllib.request.urlopen(range_request, timeout=3) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), b"repl")
                bulk = self._request(
                    f"{base}/api/projects/{project['id']}/scenes/bulk",
                    method="POST",
                    payload={"scene_ids": None, "changes": {"provider": "mock", "motion": "pan_right"}, "save_as_default": True},
                )
                self.assertEqual(bulk["updated"], 2)
                self.assertTrue(all(scene["provider"] == "mock" for scene in bulk["scenes"]))
                with patch("app.server.subprocess.Popen") as popen:
                    opened = self._request(
                        f"{base}/api/projects/{project['id']}/open-folder",
                        method="POST", payload={"kind": "renders"},
                    )
                    self.assertTrue(opened["opened"])
                    popen.assert_called_once()
                page = urllib.request.urlopen(f"{base}/", timeout=3).read().decode("utf-8")
                self.assertIn("Local Video Studio", page)
                self.assertIn("Voice-over", page)
                self.assertIn("captionPresets", page)
                self.assertIn("exportDialog", page)
                self.assertIn("timelineRazorTool", page)
                self.assertIn("exportVideoBitrate", page)
                self.assertIn("captionMaxLines", page)
                self.assertIn("Gemini Precision Sync", page)
                self.assertIn("planningProgress", page)
                self.assertIn("previewLoadState", page)
                self.assertIn("fitTimelineButton", page)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    @staticmethod
    def _request(url: str, method: str = "GET", payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
