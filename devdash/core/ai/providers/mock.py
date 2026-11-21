"""Deterministic mock provider — for tests and demos, no network.

Set in config.json: ai_enabled=true, ai_provider="mock", ai_model="mock".
Recognizes the assistant prompts: "subtasks" -> JSON plan, "verdict" -> JSON
feature verdict, otherwise echoes a short text answer.
"""

from __future__ import annotations

import json

from ..provider import AIProvider


class MockProvider(AIProvider):
    name = "mock"

    def is_available(self) -> bool:
        return True

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        # inspect the WHOLE conversation (system prompt may carry the keyword)
        user = " ".join(m.get("content", "") for m in messages).lower()
        if "subtasks" in user:
            title = user.split("titre:", 1)[-1].strip().splitlines()[0][:60] if "titre:" in user else "tâche"
            return json.dumps({"subtasks": [
                {"title": f"[mock] {title} — étape 1: cadrage", "estimate_min": 30, "tags": ["plan"]},
                {"title": f"[mock] {title} — étape 2: implémentation", "estimate_min": 90, "tags": ["plan"]},
                {"title": f"[mock] {title} — étape 3: tests", "estimate_min": 45, "tags": ["plan", "tests"]},
            ]}, ensure_ascii=False)
        if "verdict" in user:
            return json.dumps({
                "seems_complete": True,
                "confidence": 0.8,
                "reasoning": "[mock] toutes les tâches liées sont terminées et des commits feat(...) existent",
            }, ensure_ascii=False)
        return f"[mock provider] Réponse à: {messages[-1]['content'][:200] if messages else ''}"
