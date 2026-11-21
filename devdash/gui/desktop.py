"""GUI launcher: native desktop window (PyWebView) by default, or the default
browser with --browser. Both modes run the same local HTTP server.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from devdash.core import config
from .server import make_server


def run(port: int | None = None, browser: bool = False, home: Path | None = None) -> None:
    """Start the GUI and block until the window/browser mode is stopped."""
    cfg = config.load_config(home)
    # `port is not None` (not `port or ...`): --port 0 must stay a valid "pick a free port"
    effective_port = port if port is not None else cfg.gui_port
    try:
        server = make_server(port=effective_port, home=home)
    except OSError as e:
        print(f"DevDash GUI: cannot bind 127.0.0.1:{effective_port} ({e}).\n"
              f"Another instance may be running — or use --port 0 for a random free port.")
        raise SystemExit(1) from e
    actual_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True).start()
    # live git detection: new commits appear in the GUI without restart
    from .server import start_git_watcher
    start_git_watcher(server.RequestHandlerClass.api, cfg.git_watch_interval_sec)
    url = f"http://127.0.0.1:{actual_port}/"

    if browser:
        import webbrowser

        webbrowser.open(url)
        print(f"DevDash GUI (browser mode): {url}  — Ctrl+C to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        import webview  # optional dependency: pip install -e ".[gui]"

        print(f"DevDash GUI (native window): {url}")
        webview.create_window(
            "DevDash", url=url, width=1360, height=860, min_size=(1024, 640)
        )
        webview.start()  # blocks until the window is closed

    server.shutdown()
    server.server_close()
