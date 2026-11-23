from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..provider import AIError, AIProvider


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        if not self.model:
            return False
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            names = {m.get("name", "") for m in data.get("models", [])}
            return any(n == self.model or n.startswith(self.model + ":") for n in names)
        except Exception:
            return False

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        if not self.model:
            raise AIError("ollama: ai_model is required in config.json (e.g. \"qwen3:8b\")")
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise AIError(f"ollama: HTTP {e.code} — {detail}") from e  # local: no key to leak
        except Exception as e:
            raise AIError(
                f"ollama: {type(e).__name__}: {e} (is `ollama serve` running and the model pulled?)"
            ) from e
