# Architecture

The project is intentionally a modular monolith: it is simple to install on one
computer, while its internal boundaries prevent one new provider or timeline
effect from disturbing unrelated code.

```text
Browser UI
   │ HTTP/JSON
Local application server
   ├── Scene planner ── optional Gemini enhancer
   ├── Generation queue ── media provider registry
   │                       ├── Runware images
   │                       ├── Together images
   │                       └── Offline test images
   ├── Timeline renderer ── motion/effect registry ── FFmpeg
   └── SQLite repository ── projects / scenes / assets / jobs
```

## Stable boundaries

| Boundary | Contract | Implementations |
|---|---|---|
| Media generation | `MediaProvider.generate(GenerationRequest)` | Runware, Together, mock |
| Media type | `MediaKind` | `IMAGE` now; `VIDEO` is already represented |
| Scene direction | `SceneDraft` | Free rule planner, optional Gemini enhancer |
| Timeline behavior | `TimelineAction(type, params)` | Motion and transition actions |
| Motion rendering | `MotionRegistry` | Static, push, pull, left/right pan |
| Persistence | `Database` methods | SQLite tables and migrations |
| User interface | HTTP JSON routes | Framework-free browser application |

## Source map

| Path | Responsibility |
|---|---|
| `app/domain.py` | Provider-neutral data types |
| `app/scene_planner.py` | Scene splitting, emotion, prompts, timing |
| `app/gemini_analyzer.py` | Optional structured AI enhancement |
| `app/providers/` | Isolated provider adapters and HTTP handling |
| `app/generation.py` | Queue, concurrency, fallback, files, cost recording |
| `app/timeline/actions.py` | Pluggable FFmpeg motion presets |
| `app/timeline/renderer.py` | Clips, captions, assembly, audio, export |
| `app/database.py` | SQLite schema and all persistence operations |
| `app/server.py` | Local HTTP API and safe file serving |
| `app/static/` | Browser interface |

## Project data

```text
application-data/
├── backups/
│   └── studio-pre-v2-<timestamp>.sqlite3
├── settings.json
├── studio.sqlite3
└── projects/<project-id>/
    ├── voiceover.<ext>
    ├── assets/
    ├── captions.srt
    ├── cache/clips/
    └── renders/
```

The UI never needs to know how a provider authenticates, and the renderer never
needs to know which provider produced an asset. A future video asset follows the
same scene/asset selection flow and is looped or trimmed by the renderer based on
its `media_kind`.

## Design decisions

- Standard-library Python keeps the core dependency-free.
- SQLite provides resumability without operating a separate server.
- Ordered schema migrations and a pre-upgrade SQLite backup protect existing projects.
- Provider keys stay in local settings or environment variables.
- Provider cost returned by the API is recorded per asset.
- One candidate and the cheapest configured route are the defaults.
- Cached scene clips make the rendering pipeline inspectable and pave the way
  for selective re-rendering in a later version.
