from __future__ import annotations

import mimetypes
import uuid

from ..domain import GeneratedAsset, GenerationRequest, MediaKind
from .base import MediaProvider, ProviderError
from .http import download_bytes, post_json


class RunwareImageProvider(MediaProvider):
    name = "runware"
    supported_kinds = (MediaKind.IMAGE,)
    endpoint = "https://api.runware.ai/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def generate(self, request: GenerationRequest) -> GeneratedAsset:
        if not self.api_key:
            raise ProviderError("Runware API key is missing. Add it in Settings.")
        task_uuid = str(uuid.uuid4())
        task: dict[str, object] = {
            "taskType": "imageInference",
            "taskUUID": task_uuid,
            "model": request.model,
            "positivePrompt": request.prompt,
            "negativePrompt": request.negative_prompt,
            "width": request.width,
            "height": request.height,
            "numberResults": 1,
            "deliveryMethod": "sync",
            "includeCost": True,
        }
        if request.seed is not None:
            task["seed"] = request.seed
        if request.steps:
            task["steps"] = request.steps

        response = post_json(
            self.endpoint,
            [task],
            {"Authorization": f"Bearer {self.api_key}"},
        )
        if response.get("errors") or response.get("error"):
            raise ProviderError(str(response.get("errors") or response.get("error")))
        results = response.get("data") or []
        result = next((item for item in results if item.get("taskUUID") == task_uuid), None)
        if not result or not result.get("imageURL"):
            raise ProviderError("Runware did not return an image URL")
        image_url = str(result["imageURL"])
        extension = mimetypes.guess_extension(str(result.get("mimeType") or "")) or ".jpg"
        return GeneratedAsset(
            content=download_bytes(image_url),
            extension=extension.lstrip("."),
            provider=self.name,
            model=request.model,
            cost=float(result.get("cost") or 0),
            remote_url=image_url,
            provider_asset_id=result.get("imageUUID"),
            metadata={"task_uuid": task_uuid},
        )
