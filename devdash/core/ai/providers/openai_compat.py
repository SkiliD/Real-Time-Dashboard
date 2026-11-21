"""OpenAI-compatible chat provider (Mistral AI, OpenAI, or any /v1 endpoint).

Stdlib only (urllib). Endpoint/key/model come from config.json:
  ai_provider = "mistral" | "openai" | "openai_compat"
  ai_base_url = "https://api.mistral.ai/v1"   (override)
  ai_api_key  = "..."                         (never hardcoded)
  ai_model    = "mistral-small-latest"
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..provider import AIError, AIProvider


class OpenAICompatProvider(AIProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        if not self.is_available():
            raise AIError(f"{self.name}: ai_api_key and ai_model are required in config.json")
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise AIError(f"{self.name}: HTTP {e.code} — {detail}") from e
        except Exception as e:
            raise AIError(f"{self.name}: {type(e).__name__}: {e}") from e
