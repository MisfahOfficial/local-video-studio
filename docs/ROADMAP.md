# Roadmap

## Version 0.1 — included now

- Local project library and SQLite recovery
- Exact-count rule-based scene planning
- Theme/emotion-aware prompts
- Runware, Together fallback, and free offline providers
- Resumable generation queue and project spend ceiling
- Candidate selection and editable timeline actions
- Scene motion, fades/cuts, script captions, voice-over, MP4 export

## Version 0.2 — production safety and bulk workflow (included now)

- Project-wide and selected-scene bulk controls
- Provider error details and failed-job retry
- Script, duration, voice-over, and image-target warnings
- Output-folder and rendered-video access
- macOS double-click start/update helpers
- Visible app/schema versions and automatic pre-upgrade database backup

## Version 0.3 — visual timeline editor (included now)

- Synchronized voice-over and selected-media preview
- Horizontal thumbnail filmstrip with zoom and drag-to-reorder
- Per-scene duration, caption text, motion, and transition inspector
- Imported replacement images and video clips
- Reusable caption font, size, position, colors, and opacity
- Styled FFmpeg caption export and long-audio range streaming

## Version 0.4 — professional editor workspace (included now)

- CapCut-inspired media/player/inspector workspace
- Separate synchronized preview and export window
- Video, caption, and voice-over tracks with ruler, seeking, zoom, and playhead
- Advanced caption typography, presets, transforms, blend, stroke, background, glow, and shadow
- Local TTF/OTF upload and protected direct font downloads
- Advanced ASS caption rendering through FFmpeg/libass

## Version 0.5 — real timeline editing and export control (included now)

- Persistent main-track clips independent from script/caption timing
- Drag reorder, left/right trim handles, playhead split, razor, delete, undo/redo, and magnetic gap closing
- Visible preview transport with Space-bar play/pause and keyboard shortcuts
- One-to-four-line caption cards with adjustable words per line
- Named exports, destination browser, quality presets, resolutions, frame rates, and video/audio bitrates
- Apple VideoToolbox H.264 acceleration when available

## Version 0.6 — precision sync

- Background Faster-Whisper transcription job
- Word-level script-to-audio alignment and shorter caption phrases
- Audio waveform in the timeline
- Re-render only changed scene clips

## Version 0.7 — generated mixed media

- One or more image-to-video/text-to-video provider adapters
- Per-scene media choice: still, generated clip, or imported clip
- Video job polling, cancellation, retries, and provider cost estimates
- B-roll library and automatic still/video budget allocation

## Version 0.8 — richer editing

- Transition registry with real overlaps and crossfades
- Animated captions and reusable caption themes
- Music/SFX tracks with ducking
- Overlays, channel branding, color looks, and reusable templates
- Premiere/Final Cut timeline exports

## Version 1.0 — production operations

- Windows signed installer and automatic updates
- Project backup/restore package
- Provider health checks and rate-limit-aware scheduling
- Per-channel presets and reusable visual continuity profiles
- Render diagnostics and automated project validation

The order protects the low-cost local core. Each phase adds a module through an
existing contract instead of replacing the application.
