"""GUI launcher: native desktop window (PyWebView) by default, or the default
browser with --browser. Both modes run the same local HTTP server.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from devdash.core import config
from .server import make_server


def _port_in_use(port: int) -> bool:
    """True if something is actively listening on 127.0.0.1:port. Plain socket
    WITHOUT SO_REUSEADDR: on Windows a second bind with SO_REUSEADDR (what
    http.server sets) would silently succeed on the same port, so probing
    with a raw socket is the only reliable check."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _bind_server(port: int, home: Path | None = None):
    """Bind the GUI server. If the requested port is already taken (stale
    instance, other service), fall back to a free port (0) instead of dying:
    a busy port must never make the GUI unusable — the window would otherwise
    load a foreign server's error page. Returns (server, warning|None)."""
    if port != 0 and _port_in_use(port):
        srv = make_server(port=0, home=home)
        return srv, (f"127.0.0.1:{port} is already in use — using port "
                     f"{srv.server_address[1]} instead. Close the stale instance "
                     f"or change `gui_port` in config.json.")
    try:
        return make_server(port=port, home=home), None
    except OSError as e:
        if port == 0:
            raise
        srv = make_server(port=0, home=home)
        return srv, (f"127.0.0.1:{port} is busy ({e}) — using port "
                     f"{srv.server_address[1]} instead.")


def run(port: int | None = None, browser: bool = False, home: Path | None = None) -> None:
    """Start the GUI and block until the window/browser mode is stopped."""
    cfg = config.load_config(home)
    # `port is not None` (not `port or ...`): --port 0 must stay a valid "pick a free port"
    effective_port = port if port is not None else cfg.gui_port
    try:
        server, warn = _bind_server(effective_port, home=home)
    except OSError as e:
        print(f"DevDash GUI: cannot bind 127.0.0.1 ({e}).")
        raise SystemExit(1) from e
    if warn:
        print(f"DevDash GUI: {warn}")
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
        try:
            import webview  # optional dependency: pip install -e ".[gui]"
        except ImportError:
            # also covers a frozen PyInstaller build made with --no-gui
            from devdash.core.i18n import tr
            print(tr("err.no_pywebview"))
            raise SystemExit(1) from None

        print(f"DevDash GUI (native window): {url}")
        webview.create_window(
            "DevDash", url=url, width=1360, height=860, min_size=(1024, 640)
        )
        webview.start()  # blocks until the window is closed

    server.shutdown()
    server.server_close()
