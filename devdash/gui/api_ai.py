# ai endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import config
from devdash.core.ai import assistant

class AiMixin:
    def ai_status(self) -> dict:
        from devdash.core.ai import ai_status as _ai_status
        return _ai_status(config.load_config(self.home))


    def ai_context(self) -> dict:
        """Preview of the compact context that would be sent to the model.
        Purely local read — works with the AI disabled."""
        with self._conn() as c:
            return assistant.context_preview(c, home=self.home)

    # --- AI configuration from the GUI (settings tab) ----------------------


    def ai_providers(self) -> dict:
        from devdash.core.ai import provider_catalog
        return {"providers": provider_catalog()}


    def ai_config_get(self) -> dict:
        """Current AI settings. The API key is write-only: the response says
        whether one is set, never its value."""
        from devdash.core.ai import public_ai_config
        return public_ai_config(config.load_config(self.home), home=self.home)


    def ai_config_save(self, body: dict) -> dict:
        from devdash.core.ai import apply_ai_settings, public_ai_config
        try:
            # atomic load->modify->save under the config lock (threaded server)
            cfg = config.update_config(self.home, lambda c: apply_ai_settings(c, body))
        except ValueError as e:
            raise ApiError(str(e))  # validation messages never contain the key
        return {"saved": True, "config": public_ai_config(cfg, home=self.home),
                "status": self.ai_status()}

    # --- general settings (GUI settings tab, beyond AI) --------------------


    def ai_config_test(self, body: dict) -> dict:
        """Dry-run a connection with the given (not yet saved) settings.
        Sends only a tiny fixed prompt — never project context — so testing a
        cloud provider leaks nothing. The API key is scrubbed from errors."""
        from devdash.core.ai import apply_ai_settings, get_provider, is_cloud
        cfg = config.load_config(self.home)
        try:
            apply_ai_settings(cfg, body or {})
        except ValueError as e:
            raise ApiError(str(e))
        if not cfg.ai_enabled:
            raise ApiError("enable the AI first (ai_enabled) to test the connection")
        provider = get_provider(cfg)
        if provider is None:
            raise ApiError("AI is disabled")
        try:
            reply = provider.chat([{"role": "user", "content": "Reply with exactly: OK"}],
                                  temperature=0.0)
        except Exception as e:  # AIError or network error: scrub the key, then 400
            msg = str(e)
            if cfg.ai_api_key:
                msg = msg.replace(cfg.ai_api_key, "***")
            raise ApiError(f"connection failed: {msg[:300]}")
        return {"ok": True, "provider": cfg.ai_provider, "model": cfg.ai_model,
                "cloud": is_cloud(cfg), "reply": (reply or "")[:200]}


    def ai_ask(self, body: dict) -> dict:
        q = self._str(body, "question", required=True).strip()
        if not q:
            raise ApiError("question is required")
        from devdash.core.ai import chat as ai_chat
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            r = self._ai_call(assistant.ask, q)
            # persist the exchange so the chat survives a reload/restart
            meta = " / ".join(x for x in (r.get("provider"), r.get("model"),
                                          "cloud" if r.get("cloud") else None) if x)
            ai_chat.append(c, p.id, "user", q)
            ai_chat.append(c, p.id, "assistant", r.get("answer", ""), meta=meta or None)
            return r

    def ai_chat(self, query: dict) -> dict:
        """Persisted chat history of a project (default: active), oldest first."""
        from devdash.core.ai import chat as ai_chat
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            limit = self._int(query.get("limit"), "limit", 200, hi=500) or 200
            return {"project": p.slug, "messages": ai_chat.history(c, p.id, limit)}

    def ai_chat_clear(self, body: dict) -> dict:
        from devdash.core.ai import chat as ai_chat
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            n = ai_chat.clear(c, p.id)
            return {"project": p.slug, "deleted": n}


    def ai_summarize(self, body: dict) -> dict:
        return self._ai_call(assistant.summarize_week)


    def ai_plan(self, body: dict) -> dict:
        title = self._str(body, "title", required=True).strip()
        if not title:
            raise ApiError("title is required")
        # apply=True is the explicit human consent (GUI confirm button)
        return self._ai_call(assistant.plan, title, apply=bool(body.get("apply")))


    def ai_suggest(self, body: dict) -> dict:
        """Analyze the project and propose tickets. READ-ONLY: never creates anything."""
        return self._ai_call(assistant.suggest)


    def ai_suggest_apply(self, body: dict) -> dict:
        """Create tickets from AI proposals — EXPLICIT human consent only: the GUI
        calls this solely from the "create checked tickets" button. Every item is
        re-validated server-side before touching the DB (whitelisted fields)."""
        items = body.get("tasks")
        if not isinstance(items, list) or not items:
            raise ApiError("tasks is required (non-empty list)")
        if len(items) > assistant.MAX_SUGGEST_APPLY:
            raise ApiError(f"too many tasks at once (max {assistant.MAX_SUGGEST_APPLY})")
        clean = []
        for it in items:
            if not isinstance(it, dict):
                raise ApiError("each task must be an object")
            clean.append({k: it.get(k) for k in ("title", "description", "priority", "estimate_min", "tags")})
        return self._ai_call(assistant.apply_suggestions, clean)


    def ai_verdict(self, body: dict) -> dict:
        ref = body.get("ref")
        if not ref:
            raise ApiError("ref is required")
        return self._ai_call(assistant.feature_verdict, ref)


    def ai_task_verdict(self, body: dict) -> dict:
        """AI verdict on a task. The GUI NEVER applies it: apply is refused here
        on purpose — the approve button in the modal performs the status change
        itself, so a human always makes the final call."""
        from devdash.core.ai.provider import AIDisabledError, AIError
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                return assistant.task_verdict(c, tid, apply=False, home=self.home)
            except (AIDisabledError, AIError) as e:
                raise ApiError(str(e), status=400)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)


