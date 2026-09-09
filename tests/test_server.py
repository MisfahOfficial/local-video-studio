from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.paths import AppPaths
from app.server import create_server


class ServerTests(unittest.TestCase):
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
                self.assertEqual(health["version"], "0.3.0")
                self.assertEqual(health["schema_version"], 3)
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
                caption_style = self._request(
                    f"{base}/api/projects/{project['id']}/caption-style", method="POST",
                    payload={"font": "Georgia", "size": 48, "position": "top", "text_color": "#FFFFFF", "background_color": "#000000", "background_opacity": 0.6},
                )
                self.assertEqual(caption_style["caption_style"]["position"], "top")
                reordered = self._request(
                    f"{base}/api/projects/{project['id']}/scenes/reorder", method="POST",
                    payload={"scene_ids": [second_scene["id"], first_scene["id"]]},
                )
                self.assertEqual(reordered["scenes"][0]["id"], second_scene["id"])

                upload_request = urllib.request.Request(
                    f"{base}/api/scenes/{first_scene['id']}/asset",
                    data=b"replacement-image-data", method="POST",
                    headers={"X-Filename": "replacement.png", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(upload_request, timeout=3) as response:
                    uploaded = json.loads(response.read().decode("utf-8"))
                self.assertEqual(uploaded["asset"]["provider"], "local")
                self.assertEqual(uploaded["scene"]["selected_asset_id"], uploaded["asset"]["id"])
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
                self.assertIn("Preview and visual timeline", page)
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
