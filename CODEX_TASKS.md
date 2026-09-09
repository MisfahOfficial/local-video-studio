# Ready-to-use Codex tasks

Use these prompts from the project root. Give them one by one for the safest,
easiest-to-review development flow, or use the all-at-once prompt at the end.

## Task 1 — precise local transcription and sync

> Extend Local Video Studio with background Faster-Whisper transcription. Read
> README.md and docs/ARCHITECTURE.md first. Persist transcript jobs and word-level
> timestamps without breaking existing databases. Add a Transcribe & sync button,
> progress/error UI, script-to-transcript alignment, caption phrase settings, and
> tests. Preserve the current dependency-free fallback when Faster-Whisper is not
> installed. Run all existing and new tests plus an offline FFmpeg smoke render.

## Task 2 — selective clip re-rendering and waveform

> Add a local audio waveform and draggable scene boundaries to the Timeline tab.
> Validate that scenes stay ordered and cover the voice-over. Hash each scene's
> selected asset, duration, actions, and render settings so unchanged cached clips
> are reused. Do not change provider adapters. Add migration-safe persistence and
> automated tests.

## Task 3 — first video-generation provider

> Add a video provider through the existing MediaProvider/MediaKind contract.
> Keep provider submit, poll, cancel, authentication, pricing, and error parsing in
> its own adapter. Add per-scene Still/Video selection, a video-model setting, job
> recovery after restart, preview thumbnails, and cost estimates. Do not rewrite
> image generation or FFmpeg rendering. Add mocked API contract tests; never spend
> API credit during tests.

## Task 4 — timeline action plug-ins

> Refactor timeline action interpretation into independent handlers and add
> crossfade, color-look, overlay, and animated-caption actions. Existing motion and
> transition JSON must remain compatible. Add controls that reveal only relevant
> parameters and validate FFmpeg filters with short synthetic test videos.

## Task 5 — Windows installer

> Produce and verify a Windows installer using the existing PyInstaller and Inno
> Setup files. Bundle the static UI and either bundle FFmpeg under its license or
> provide a guided first-run path selector. Preserve project data across upgrades,
> add clean uninstall behavior that does not delete projects by default, and write
> a release checklist.

## All-at-once prompt

> Continue building Local Video Studio using docs/ARCHITECTURE.md and
> docs/ROADMAP.md as authoritative constraints. Implement roadmap versions 0.2–0.4
> in separate, reviewable commits/modules: precise background transcription and
> word sync; waveform and selective scene re-rendering; a provider-neutral video
> generation workflow with one concrete provider; and plug-in crossfades, overlays,
> looks, audio tracks, and animated captions. Preserve existing projects and API
> boundaries. Keep paid API calls disabled in tests, include migrations and failure
> recovery, run unit tests plus real local FFmpeg smoke tests, and update all user
> documentation. Stop and ask before choosing any paid provider or licensing a
> bundled binary.

