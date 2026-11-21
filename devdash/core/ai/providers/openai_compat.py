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

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90,
                 require_key: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # local servers (LM Studio, llama.cpp, ...) accept requests without a key
        self.require_key = require_key

    def is_available(self) -> bool:
        return bool(self.model) and (bool(self.api_key) or not self.require_key)

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        if not self.is_available():
            missing = "ai_model" if not self.model else "ai_api_key"
            raise AIError(f"{self.name}: {missing} is required in config.json")
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"Content-Type": "application/json"}
        if self.api_key:  # local servers often ignore it; never send an empty bearer
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            # never let the API key leak into an error shown to the user
            if self.api_key:
                detail = detail.replace(self.api_key, "***")
            raise AIError(f"{self.name}: HTTP {e.code} — {detail}") from e
        except Exception as e:
            msg = str(e)
            if self.api_key:
                msg = msg.replace(self.api_key, "***")
            raise AIError(f"{self.name}: {type(e).__name__}: {msg}") from e
