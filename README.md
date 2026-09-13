# Local Video Studio v0.6.9

Local Video Studio turns a script and an existing voice-over into a scene plan,
bulk-generated images, an editable timeline, captions, and an exported MP4. The
editor and project database run on your computer. Only the image/planning
providers you choose receive API requests.

The default workflow is optimized for cost: **one paid image per scene**, the
free local planner, free script-timed captions, and local FFmpeg rendering.
Extra candidates and premium models are opt-in on individual scenes.

## What is included

- Script-to-scene planning up to the requested count, without empty scenes
- Gemini Precision Sync that listens to the real VO, matches it to the script, and places semantic scene boundaries
- Automatic 5s / 8s / 10s pacing with optional two-second pop-in detail beats
- Automatic retries and stable Gemini model fallback when a planning model is temporarily overloaded
- Theme, emotion, narrative-role, prompt, and negative-prompt generation
- Runware image generation with Together as an optional fallback
- A zero-cost offline provider for testing the entire workflow
- A resumable SQLite generation queue with concurrency and a spend ceiling
- Project-wide and selected-scene controls for providers, models, options, motion, transitions, and prompts
- Clear failed-generation details and one-click retry
- Script, duration, voice-over, and target-image validation warnings
- Candidate review and manual image selection
- CapCut-inspired three-panel editor with a media bin, separate real-time preview, and inspector
- Premiere-style caption, video, and voice-over track order with a ruler, zoom, seeking, and persistent clip reordering
- Direct caption dragging in the preview plus non-destructive visual/caption visibility and voice-over mute controls
- Real trim handles, playhead splitting, deletion, magnetic gap closing, and timeline undo/redo
- Visible player controls plus Space-bar play/pause and one-second keyboard seeking
- Smooth frame timecode, a full-width draggable seek bar, buffering state, and loaded-audio progress
- Per-clip duration/source trim plus per-scene caption, motion, fade, and cut controls
- Local replacement image/video uploads with automatic scene selection
- Advanced caption font, pattern, case, alignment, spacing, transform, blend, stroke, background, glow, shadow, and presets
- TTF/OTF font installation from a file or a protected direct Google Fonts/GitHub download
- Local FFmpeg rendering, voice-over muxing, and optional styled burned captions
- Separate export window with named files, quality presets, resolutions, frame rates, video/audio bitrates, browsable destination, progress, and video access
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

Version 0.6.9 makes captions directly draggable in the player and saves the new
position to the project. Caption and visual tracks now have show/hide controls,
voice-over can be muted from either the player or timeline, and tracks are ordered
Captions, Video, then Voice-over to match professional editing layouts.

Version 0.6.8 fixes fast, unsynchronized ending scenes caused by a stale or
incorrect voice-over duration. macOS can now fall back to `afinfo` when FFprobe is
broken, Precision Sync never truncates the timed transcript to a shorter stored
duration, and fitting visuals to the VO repairs both clip and caption boundaries
without deleting generated images. Poppins, Impact, Montserrat, and Amsi Pro are
available in the caption font menu at every supported size; locally installed font
files replace the matching menu entry for preview and export. Planned two-second
pop-in motion is now preserved when scenes are saved.

Version 0.6.7 repairs isolated invalid Gemini scene ranges instead of discarding an
otherwise valid Precision Sync plan. Complete-sentence matching still runs first;
the final timeline then reconstructs safe, positive, gapless scene boundaries from
neighboring timestamps and always covers the full voice-over.

Version 0.6.6 fixes editable installation by explicitly packaging only the Python
application and its browser assets, so development folders are no longer mistaken
for additional top-level packages by setuptools.

Version 0.6.5 adds automatic pacing when **Target images** is left blank. Main
scenes are kept to 5 seconds through minute 20, 8 seconds through minute 40, and
10 seconds after minute 40 whenever complete narration boundaries allow. Short,
self-contained emphasis beats can become two-second object/detail inserts with a
new pop-in motion. A manual image target remains available as an override.

Version 0.6.4 keeps every Precision Sync visual attached to a complete spoken
sentence or clearly paused utterance. Long projects are divided only at those safe
audio boundaries, small Gemini timing drift is snapped back to the real pause, and
a target that would force mid-sentence visual changes is rejected before it can
replace the current plan. Script punctuation also restores sentence boundaries when
the speech transcription omits punctuation. Local Free and Gemini Text Smart also
stop at the number of complete script sentences instead of cutting a sentence just
to reach a larger image target.

Version 0.6.3 keeps Precision Sync moving when a valid visual uses different words
from the narration. Instead of rejecting the whole plan, questionable visual subjects
are automatically re-anchored to the exact timed narration, and every precision image
prompt includes its spoken context.

Version 0.6.2 treats the measured voice-over as the master clock. The editor shows
separate visual and VO durations, offers one-click timeline fitting, automatically
repairs a mismatch before export, and verifies that the finished MP4 covers the full
voice-over. Precision Sync also rejects timestamped scenes whose narration or image
subject does not match the spoken passage.

Version 0.6.1 installs a verified CA certificate bundle automatically on macOS,
so HTTPS image-provider requests work with python.org Python builds without
disabling certificate verification.

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
3. Select **Gemini Precision Sync**, leave **Target images** blank for automatic
   pacing, and create the plan. Precision Sync uses the measured VO duration and
   chooses the scene count automatically. Enter a number only when you deliberately
   need a fixed image count.
4. Review high-importance scenes. Keep one candidate by default; request two or
   three only where a stronger hook or reveal is worth the extra cost.
5. Click **Generate missing images**. The queue can be paused, resumed, retried,
   or safely restarted. Provider errors appear with their scene and option.
6. Open **Timeline** to use the media bin, live player, caption inspector, and
   synchronized tracks. Drag clips to reorder them, drag either cyan clip edge
   to trim, or place the playhead and use **Split**. The razor tool cuts wherever
   you click. Imported-video trims preserve their source-in point; still-image
   trims change their on-screen duration. Import replacements or save the caption
   design for the project at any time.
7. Press **Space** to play/pause. Use **V** for selection, **B** for the razor,
   Left/Right Arrow to seek one second, and Command/Ctrl-Z to undo a timeline edit.
8. Open the separate **Export** window. Name the file, choose a quality preset or
   custom resolution/frame rate/bitrates, and browse to any destination. Caption
   burning can be disabled to produce a clean video;
   `captions.srt` is always written in the project folder. Use **Open output
   folder** or **Open rendered video** when the render completes.

At 715 images, each scene averages about 12.5 seconds. Image cost is exactly
`715 × the chosen model's current per-image price` when every scene uses one
candidate. Update **Estimated cost per image** in Settings when a provider changes
its rate. The provider's returned cost is recorded as the authoritative actual
spend.

## Voice sync and captions

**Gemini Precision Sync** uploads the finished VO through the Gemini Files API,
listens to the whole recording, matches it against the supplied script, and
returns timestamped semantic scenes. The result is validated for the exact scene
count, chronological order, complete audio coverage, and sentence-safe visual
boundaries before any existing scene is replaced. The remote VO file is deleted
immediately after planning. Gemini's free tier can be used when it is available for
the selected account/model.

**Gemini Text Smart** and **Local Free** remain available as estimated-timing
fallbacks. They distribute time without listening to the VO, so they should not
be used for final synchronization. Caption cards can be limited to one, two,
three, or four lines and have an adjustable words-per-line target. Preview and
FFmpeg output use the same grouping. `app/transcription.py` also contains an
optional local Faster-Whisper adapter:

```bash
python -m pip install -e ".[transcription]"
```

The current visual editor stays dependency-free so it installs reliably on modest PCs.

## Windows

For development, install Python and FFmpeg, then double-click `start-windows.bat`.
To build a standalone executable, run `installer/build_windows.ps1` in
PowerShell. The build places the executable in `dist/LocalVideoStudio/`.

## Data and recovery

By default, data is stored under the platform application-data directory. Custom
fonts are stored in its `fonts` folder for both live preview and FFmpeg export. Set
`LOCAL_VIDEO_STUDIO_HOME` or pass `--data-dir` to choose another location. Each
project owns its source voice-over, generated assets, caption file, cached clips,
and final renders. SQLite uses WAL mode so an interrupted session can resume.

## Safe updates

Program code and project data live in different folders, so updating the code
does not replace projects, keys, images, captions, or renders. On the first v0.5
launch, the database is copied to `backups/studio-pre-v4-<timestamp>.sqlite3`
before the independent editable video timeline is added. Existing scenes become
clips automatically without changing their media or timing.

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
