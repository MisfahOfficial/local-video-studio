# Extending the studio safely

## Add an image or video provider

Create one file under `app/providers/` and subclass `MediaProvider`:

```python
from app.domain import GeneratedAsset, GenerationRequest, MediaKind
from app.providers.base import MediaProvider

class MyVideoProvider(MediaProvider):
    name = "my_video"
    supported_kinds = (MediaKind.VIDEO,)

    def generate(self, request: GenerationRequest) -> GeneratedAsset:
        # Authenticate, submit, poll, and download inside this adapter only.
        return GeneratedAsset(
            content=downloaded_mp4,
            extension="mp4",
            provider=self.name,
            model=request.model,
            cost=provider_cost,
        )
```

Then register it in `GenerationManager._registry`. Add its settings fields and a
UI option. No database or renderer rewrite is needed: `MediaKind.VIDEO` and video
clip handling already exist.

For long-running video APIs, implement submit/poll as provider-specific methods
and persist the remote job ID in asset/job metadata. Keep the generic queue
unaware of the provider's protocol.

## Add a motion effect

Register a function that returns an FFmpeg video-filter string:

```python
registry.register(
    "gentle_tilt",
    lambda width, height, fps, duration: (
        f"scale={width}:{height},rotate='0.002*sin(2*PI*t/{duration})',fps={fps}"
    ),
)
```

Expose the returned registry name in the UI. Existing scenes keep their saved
action JSON; no migration is required.

## Add a new timeline action

Timeline actions are JSON objects:

```json
{"type": "overlay", "params": {"asset": "logo.png", "position": "top-right"}}
```

Add an action handler in the timeline layer, then add its controls in the timeline
UI. Keep action interpretation out of scene planning and provider adapters.

Good future actions include overlays, color looks, sound effects, background music,
speed ramps, animated word captions, and branded lower thirds.

## Add an export format

Create an exporter beside `app/timeline/renderer.py` and accept the same project,
scene, asset, and action records. This allows Premiere XML, Final Cut XML, or an
image-only package without changing generation.

## Change the database

Add a numbered migration; do not rewrite users' existing tables. New optional
features should prefer nullable columns or separate tables so old projects remain
readable.

## Change the interface

All browser calls go through `api()` in `app/static/app.js`. Add a focused route
in `app/server.py`, keep provider secrets out of responses, and validate local
paths before serving files.

