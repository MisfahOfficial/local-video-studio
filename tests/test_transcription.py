from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app.transcription import probe_duration


class DurationProbeTests(unittest.TestCase):
    def test_macos_afinfo_recovers_when_ffprobe_is_broken(self) -> None:
        broken = subprocess.CalledProcessError(134, ["ffprobe"])
        afinfo = subprocess.CompletedProcess(
            ["afinfo"], 0, stdout="estimated duration: 40.515918 sec\n", stderr=""
        )
        with (
            patch("app.transcription.shutil.which", return_value="/usr/bin/afinfo"),
            patch("app.transcription.subprocess.run", side_effect=[broken, afinfo]),
        ):
            duration = probe_duration(Path("voiceover.mp3"))

        self.assertAlmostEqual(duration, 40.515918)


if __name__ == "__main__":
    unittest.main()
