# Roadmap

## Version 0.1 — included now

- Local project library and SQLite recovery
- Exact-count rule-based scene planning
- Theme/emotion-aware prompts
- Runware, Together fallback, and free offline providers
- Resumable generation queue and project spend ceiling
- Candidate selection and editable timeline actions
- Scene motion, fades/cuts, script captions, voice-over, MP4 export

## Version 0.2 — precision sync

- Background Faster-Whisper transcription job
- Word-level script-to-audio alignment and shorter caption phrases
- Audio waveform in the timeline
- Drag-to-adjust scene boundaries with duration validation
- Re-render only changed scene clips

## Version 0.3 — mixed media

- One or more image-to-video/text-to-video provider adapters
- Per-scene media choice: still, generated clip, or imported clip
- Video job polling, cancellation, retries, and provider cost estimates
- B-roll library and automatic still/video budget allocation

## Version 0.4 — richer editing

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

