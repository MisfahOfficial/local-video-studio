from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

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
                page = urllib.request.urlopen(f"{base}/", timeout=3).read().decode("utf-8")
                self.assertIn("Local Video Studio", page)
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

