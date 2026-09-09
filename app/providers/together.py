from __future__ import annotations

import base64

from ..domain import GeneratedAsset, GenerationRequest, MediaKind
from .base import MediaProvider, ProviderError
from .http import download_bytes, post_json


class TogetherImageProvider(MediaProvider):
    name = "together"
    supported_kinds = (MediaKind.IMAGE,)
    endpoint = "https://api.together.xyz/v1/images/generations"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def generate(self, request: GenerationRequest) -> GeneratedAsset:
        if not self.api_key:
            raise ProviderError("Together API key is missing. Add it in Settings.")
        response = post_json(
            self.endpoint,
            {
                "model": request.model,
                "prompt": request.prompt,
                "negative_prompt": request.negative_prompt,
                "width": request.width,
                "height": request.height,
                "steps": request.steps,
                "n": 1,
                "response_format": "url",
                **({"seed": request.seed} if request.seed is not None else {}),
            },
            {"Authorization": f"Bearer {self.api_key}"},
        )
        rows = response.get("data") or []
        if not rows:
            raise ProviderError(f"Together did not return image data: {response}")
        result = rows[0]
        image_url = result.get("url")
        encoded = result.get("b64_json")
        if image_url:
            content = download_bytes(str(image_url))
        elif encoded:
            content = base64.b64decode(encoded)
        else:
            raise ProviderError("Together returned neither a URL nor base64 image data")
        return GeneratedAsset(
            content=content,
            extension="png",
            provider=self.name,
            model=request.model,
            remote_url=str(image_url) if image_url else None,
            metadata={"raw_id": result.get("id")},
        )

