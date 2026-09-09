"""Create a tiny zero-cost project through the application services.

This script is intentionally provider-free. Use the browser UI for normal work;
the script exists for release checks and debugging local FFmpeg installations.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Database
from app.domain import GenerationRequest
from app.providers.mock import MockImageProvider
from app.scene_planner import RuleBasedScenePlanner
from app.timeline.renderer import FFmpegRenderer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", help="Keep demo data in this directory")
    args = parser.parse_args()
    temporary = None
    if args.output_dir:
        root = Path(args.output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)

    db = Database(root / "studio.sqlite3")
    project = db.create_project("Smoke demo", "us_nostalgia")
    script = (Path(__file__).parents[1] / "sample" / "sample_script.txt").read_text(encoding="utf-8")
    scenes = db.replace_scenes(
        project["id"],
        RuleBasedScenePlanner().plan(script, theme_id="us_nostalgia", duration_seconds=9, target_scene_count=3),
    )
    provider = MockImageProvider()
    asset_dir = root / "project" / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        asset = provider.generate(
            GenerationRequest(scene["prompt"], scene["negative_prompt"], "offline-placeholder", 640, 360)
        )
        path = asset_dir / f"scene-{scene['position']:04d}.png"
        path.write_bytes(asset.content)
        db.add_asset(
            project_id=project["id"], scene_id=scene["id"], candidate_index=0,
            media_kind="image", provider="mock", model=asset.model, local_path=str(path),
            remote_url=None, provider_asset_id=None, cost=0, metadata={},
        )
    output = FFmpegRenderer().render(
        project=db.get_project(project["id"]), scenes=db.list_scenes(project["id"]),
        assets=db.list_assets(project["id"]), project_dir=root / "project",
        width=640, height=360, fps=15, burn_captions=True,
    )
    print(output)
    if temporary:
        print("The temporary demo is removed when this process exits. Use --output-dir to keep it.")


if __name__ == "__main__":
    main()
