from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..domain import GeneratedAsset, GenerationRequest, MediaKind


class ProviderError(RuntimeError):
    pass


class MediaProvider(ABC):
    name: str
    supported_kinds: tuple[MediaKind, ...] = (MediaKind.IMAGE,)

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GeneratedAsset:
        raise NotImplementedError

    def supports(self, kind: MediaKind) -> bool:
        return kind in self.supported_kinds


class ProviderRegistry:
    def __init__(self, providers: Iterable[MediaProvider] = ()):
        self._providers: dict[str, MediaProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: MediaProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str, kind: MediaKind) -> MediaProvider:
        provider = self._providers.get(name)
        if not provider:
            raise ProviderError(f"Provider '{name}' is not configured")
        if not provider.supports(kind):
            raise ProviderError(f"Provider '{name}' does not support {kind.value} generation")
        return provider

    def available(self, kind: MediaKind | None = None) -> list[str]:
        if kind is None:
            return sorted(self._providers)
        return sorted(name for name, provider in self._providers.items() if provider.supports(kind))

