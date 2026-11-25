# settings endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from .api_common import _general_block
from devdash.core import config

class SettingsMixin:
    def settings_get(self) -> dict:
        """Full public settings: AI block (key write-only) + general block."""
        from devdash.core.ai import public_ai_config
        cfg = config.load_config(self.home)
        home = self.home or config.devdash_home()
        return {
            "home": str(home),
            "ai": public_ai_config(cfg, home=home),
            "general": _general_block(cfg, home),
        }


    def tour_get(self) -> dict:
        """Has the first-launch guided tour been seen? Persisted in the DB meta
        table on purpose: localStorage is per-origin (127.0.0.1:PORT) and the
        GUI port can change between launches (fallback when 8765 is busy)."""
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'tour_done'").fetchone()
        return {"done": bool(row and row["value"] == "1")}

    def tour_post(self, body: dict) -> dict:
        done = (body or {}).get("done")
        if not isinstance(done, bool):
            raise ApiError("'done' must be a boolean")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('tour_done', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if done else "0",),
            )
            conn.commit()
        return {"done": done}

    def settings_save(self, body: dict) -> dict:
        """Save general + AI settings in one shot (the settings tab form)."""
        from devdash.core import i18n
        from devdash.core.ai import apply_ai_settings, public_ai_config
        try:
            # atomic load->modify->save under the config lock (threaded server)
            def _apply(c):
                config.apply_general_settings(c, body or {}, allowed_langs=i18n.SUPPORTED_LANGS)
                apply_ai_settings(c, body or {})
            cfg = config.update_config(self.home, _apply)
        except ValueError as e:
            raise ApiError(str(e))  # validation messages never contain the key
        home = self.home or config.devdash_home()
        return {"saved": True, "settings": {
            "home": str(home),
            "ai": public_ai_config(cfg, home=home),
            "general": _general_block(cfg, home),
        }, "status": self.ai_status()}

