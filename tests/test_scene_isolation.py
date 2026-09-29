import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.youtube_auto import AutoYouTubeManager


class SceneIsolationTests(unittest.TestCase):
    def test_one_scene_error_does_not_stop_the_others(self):
        manager = object.__new__(AutoYouTubeManager)
        done = []

        def source(run, scene):
            if scene["position"] == 3:
                raise TimeoutError("The read operation timed out")
            done.append(scene["position"])

        manager._source_scene = source
        manager._progress = lambda run: None
        run = SimpleNamespace(lock=threading.Lock(), failed=0, errors=[])
        scenes = [{"position": index} for index in range(1, 9)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda scene: manager._source_scene_safely(run, scene), scenes))
        self.assertEqual(sorted(done), [1, 2, 4, 5, 6, 7, 8])
        self.assertEqual(run.failed, 1)
        self.assertEqual(run.errors[0]["scene"], 3)


if __name__ == "__main__":
    unittest.main()
