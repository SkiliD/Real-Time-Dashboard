from __future__ import annotations

from abc import ABC, abstractmethod

class AIError(Exception):
    """Any provider/transport failure (network, auth, bad response...)."""

class AIDisabledError(Exception):
    """Raised when an AI feature is used but AI is disabled or the
    specific feature flag is off. The rest of the app never sees this."""


class AIProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}].
        Returns the assistant text. Raises AIError on failure."""

    def is_available(self) -> bool:
        """Cheap availability check (config present, server reachable...)."""
        return True
