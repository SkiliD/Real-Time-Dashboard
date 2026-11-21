"""Anthropic (Claude) cloud provider — Messages API, stdlib only (urllib).

The Anthropic API is NOT OpenAI-compatible: different endpoint
(/v1/messages), auth header (x-api-key), a mandatory max_tokens, and the
system prompt is a top-level field, not a message. This provider handles
that translation.

config.json:
  ai_provider = "anthropic"
  ai_api_key  = "sk-ant-..."            (never hardcoded, never logged)
  ai_model    = "claude-sonnet-4-5"     (or any Claude model id)
  ai_base_url = "https://api.anthropic.com"   (optional override)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..provider import AIError, AIProvider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 2048


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        if not self.is_available():
            missing = "ai_api_key" if not self.api_key else "ai_model"
            raise AIError(f"anthropic: {missing} is required in config.json")
        # Anthropic: system prompt is a separate top-level field
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        convo = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m.get("role") in ("user", "assistant")]
        body = {
            "model": self.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "messages": convo,
        }
        if system:
            body["system"] = system
        req = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # answer = concatenation of the text content blocks
            return "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if self.api_key:
                detail = detail.replace(self.api_key, "***")  # never leak the key
            raise AIError(f"anthropic: HTTP {e.code} — {detail}") from e
        except Exception as e:
            msg = str(e)
            if self.api_key:
                msg = msg.replace(self.api_key, "***")
            raise AIError(f"anthropic: {type(e).__name__}: {msg}") from e
