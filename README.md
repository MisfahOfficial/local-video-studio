# Local Video Studio v0.2

Local Video Studio turns a script and an existing voice-over into a scene plan,
bulk-generated images, an editable timeline, captions, and an exported MP4. The
editor and project database run on your computer. Only the image/planning
providers you choose receive API requests.

The default workflow is optimized for cost: **one paid image per scene**, the
free local planner, free script-timed captions, and local FFmpeg rendering.
Extra candidates and premium models are opt-in on individual scenes.

## What is included

- Script-to-scene planning up to the requested count, without empty scenes
- Theme, emotion, narrative-role, prompt, and negative-prompt generation
- Runware image generation with Together as an optional fallback
- A zero-cost offline provider for testing the entire workflow
- A resumable SQLite generation queue with concurrency and a spend ceiling
- Project-wide and selected-scene controls for providers, models, options, motion, transitions, and prompts
- Clear failed-generation details and one-click retry
- Script, duration, voice-over, and target-image validation warnings
- Candidate review and manual image selection
- Timeline actions for motion and transitions
- Local FFmpeg rendering, voice-over muxing, and optional burned captions
- Extension contracts for new image providers, video providers, and effects
- Browser UI served only on `127.0.0.1` by default
- Finder/Explorer output access and a direct rendered-video link
- Automatic v0.1 database backup and safe in-place schema upgrades

## Quick start

Requirements: Python 3.11+ and FFmpeg/FFprobe on `PATH`.

```bash
python run.py
```

The application opens at `http://127.0.0.1:8765`. Open **Settings** and add a
Runware key only when you are ready for paid generations. Keys and projects are
stored on the local machine.

On macOS, you can instead double-click **Start Local Video Studio.command**. The
first launch creates the private Python environment automatically. Keep the
Terminal window open while the studio is running.

For a completely free test:

1. Create a project.
2. Paste a script and enter the voice-over duration.
3. Create the visual plan.
4. In **Bulk controls**, choose **Offline test** and click **Apply to all scenes**.
5. Generate, review the placeholder assets, and render.

The command accepts `--data-dir`, `--host`, `--port`, and `--no-browser`.

## Normal 715-image workflow

1. Create a project and choose a theme.
2. Upload the 2:29:00 voice-over and paste the matching script.
3. Enter `149` minutes and `715` images, then create the plan.
4. Review high-importance scenes. Keep one candidate by default; request two or
   three only where a stronger hook or reveal is worth the extra cost.
5. Click **Generate missing images**. The queue can be paused, resumed, retried,
   or safely restarted. Provider errors appear with their scene and option.
6. Review/select candidates and adjust motion or transition actions.
7. Export locally. Caption burning can be disabled to produce a clean video;
   `captions.srt` is always written in the project folder. Use **Open output
   folder** or **Open rendered video** when the render completes.

At 715 images, each scene averages about 12.5 seconds. Image cost is exactly
`715 × the chosen model's current per-image price` when every scene uses one
candidate. Update **Estimated cost per image** in Settings when a provider changes
its rate. The provider's returned cost is recorded as the authoritative actual
spend.

## Voice sync and captions

The base installation uses the supplied script as captions and distributes
scene timing across the measured voice-over duration by narration word count.
This is free and deterministic. `app/transcription.py` also contains an optional
local Faster-Whisper adapter for word timestamps:

```bash
python -m pip install -e ".[transcription]"
```

Automatic word-level alignment in the UI is a planned precision-sync upgrade. The
current base remains dependency-free so it installs reliably on modest PCs.

## Windows

For development, install Python and FFmpeg, then double-click `start-windows.bat`.
To build a standalone executable, run `installer/build_windows.ps1` in
PowerShell. The build places the executable in `dist/LocalVideoStudio/`.

## Data and recovery

By default, data is stored under the platform application-data directory. Set
`LOCAL_VIDEO_STUDIO_HOME` or pass `--data-dir` to choose another location. Each
project owns its source voice-over, generated assets, caption file, cached clips,
and final renders. SQLite uses WAL mode so an interrupted session can resume.

## Safe updates

Program code and project data live in different folders, so updating the code
does not replace projects, keys, images, captions, or renders. On the first v0.2
launch, the database is copied to `backups/studio-pre-v2-<timestamp>.sqlite3`
before the new columns are added.

To update a GitHub clone on macOS, stop the running studio and double-click
**Update Local Video Studio.command**. It pulls only the currently checked-out
branch with `--ff-only`, refuses to run over uncommitted code changes, and runs
the full offline test suite. Project media is never stored in the Git repository.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [How script, theme, and emotion control images](docs/IMAGE_DIRECTION.md)
- [Adding a provider, effect, or video generator](docs/EXTENDING.md)
- [Roadmap](docs/ROADMAP.md)
- [Ready-to-use Codex tasks](CODEX_TASKS.md)

## Validation

```bash
python -m unittest discover -s tests -v
```

The suite includes a real offline FFmpeg render test and never calls a paid API.
