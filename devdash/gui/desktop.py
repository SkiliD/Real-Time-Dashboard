"""pywebview window, falls back to --browser"""



from __future__ import annotations



import os

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





GWL_STYLE = -16
WS_THICKFRAME = 0x00040000  # resize border / aero-snap style bit
WS_CAPTION = 0x00C00000     # native title bar (the blue bar when Windows accent is on)
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000


def _frameless_style(style: int) -> int:
    """Pure style math: frameless window = resize handles IN, caption/border OUT.
    pywebview's frameless=True normally does this via FormBorderStyle.None, but
    we re-assert it at the Win32 level so the native (accent-colored) title bar
    can never come back regardless of backend quirks. Idempotent."""
    return (style | WS_THICKFRAME) & ~WS_CAPTION & ~WS_BORDER & ~WS_DLGFRAME


def _enable_edge_resize(win) -> None:
    """A frameless window ships without WS_THICKFRAME, so the OS offers no edge
    or corner resize handles: the window could only be maximized, never
    resized by dragging. Re-apply the frameless style once the native handle
    exists: WS_THICKFRAME in (edges/corners resize + Aero snap) and
    WS_CAPTION/WS_BORDER out (the native accent-colored title bar must stay
    gone — our own dark #titlebar is the only chrome). DWMWA_NCRENDERING_POLICY
    is forced to DISABLED so Win11 DWM does not re-draw a system frame on the
    thickframed window. Best-effort on purpose: a failure here must never
    block startup."""
    def _apply():
        try:
            if not win.events.shown.wait(15):
                return
            native = getattr(win, "native", None)  # winforms Form (pywebview sets window.native)
            if native is None or os.name != "nt":
                return
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = native.Handle.ToInt32()
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(hwnd, GWL_STYLE, _frameless_style(style))
            # SWP_NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED: apply the new style in place
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
            # WS_THICKFRAME + pywebview's DWMWA_NCRENDERING_POLICY=ENABLED makes Win11 DWM
            # draw the SYSTEM caption/frame again (the blue bar). Disable DWM NC rendering
            # (DWMNCRP_DISABLED=1): WS_THICKFRAME then only provides resize hit-testing with
            # a thin classic border, and no caption is ever drawn (we also stripped WS_CAPTION).
            # Belt & suspenders on top: caption/border color = none if anything reappears.
            try:
                import ctypes as _ct
                dwm = _ct.windll.dwmapi
                disabled = _ct.c_int(1)          # DWMNCRP_DISABLED
                dwm.DwmSetWindowAttribute(hwnd, 2, _ct.byref(disabled), 4)      # DWMWA_NCRENDERING_POLICY
                round_corners = _ct.c_int(2)     # DWMWCP_ROUND
                dwm.DwmSetWindowAttribute(hwnd, 33, _ct.byref(round_corners), 4)  # DWMWA_WINDOW_CORNER_PREFERENCE: rounded window corners on Win11
                none_color = _ct.c_uint(0xFFFFFFFF)  # DWMCOLOR_NONE
                dwm.DwmSetWindowAttribute(hwnd, 35, _ct.byref(none_color), 4)   # DWMWA_CAPTION_COLOR
                dwm.DwmSetWindowAttribute(hwnd, 34, _ct.byref(none_color), 4)   # DWMWA_BORDER_COLOR
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=_apply, daemon=True).start()


def _clamp_window_geom(g) -> dict | None:
    """Sanity-clamp a persisted window geometry; None if unusable (missing keys,
    absurd sizes). Pure function so it is unit-testable without a window."""
    try:
        w, h = int(g["w"]), int(g["h"])
        x, y = int(g.get("x", 0)), int(g.get("y", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if not (200 <= w <= 4096 and 200 <= h <= 4096):
        return None
    return {"w": w, "h": h, "x": max(-64, min(x, 4096)), "y": max(0, min(y, 4096)),
            "maximized": bool(g.get("maximized"))}


def _clamp_to_workarea(geom: dict, work: tuple[int, int, int, int]) -> dict:
    """Clamp a window geometry so at least 64 px of the window stays visible
    inside the work area (left, top, right, bottom) — a persisted position on a
    removed/rotated monitor must never restore the window fully off-screen.
    Pure function -> unit-testable."""
    left, top, right, bottom = work
    w = geom["w"]
    return {**geom,
            "x": min(max(geom["x"], left - w + 64), right - 64),
            "y": min(max(geom["y"], top), bottom - 64)}


def _primary_workarea() -> tuple[int, int, int, int] | None:
    """Primary monitor work area (excludes the taskbar) via Win32; None elsewhere."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 2)  # MONITOR_DEFAULTTOPRIMARY
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None


def _save_window_geom(win, home: Path | None) -> None:
    """Persist window position + size (+ maximized flag) when the window closes,
    so the next launch reopens the app exactly where it was left. Best-effort:
    a failure here must never block or crash the shutdown."""
    try:
        cfg = config.load_config(home)
        g: dict = {"maximized": False}
        native = getattr(win, "native", None)
        if native is not None:  # winforms Form: exact bounds + window state
            try:
                from System.Windows.Forms import FormWindowState  # pythonnet (winforms backend)
                if native.WindowState == FormWindowState.Maximized:
                    native.WindowState = FormWindowState.Normal  # read the restored bounds
                    g["maximized"] = True
            except Exception:
                pass
            b = native.Bounds
            g.update(x=int(b.X), y=int(b.Y), w=int(b.Width), h=int(b.Height))
        else:
            g.update(w=int(getattr(win, "width", 0) or 0), h=int(getattr(win, "height", 0) or 0))
        gc = _clamp_window_geom(g)
        if gc:
            cfg.gui_window = gc
            config.save_config(cfg, home)
    except Exception:
        pass


def _restore_window_state(win, geom: dict) -> None:
    """Re-maximize on launch if the app was closed while maximized."""
    def _apply():
        try:
            if geom.get("maximized") and win.events.shown.wait(15):
                win.maximize()
        except Exception:
            pass

    threading.Thread(target=_apply, daemon=True).start()


def _apply_window_icon(win, icon_path: Path) -> None:

    """Force the window's taskbar icon (ICON_SMALL + ICON_BIG) once the native

    handle exists. The winforms backend only sets Form.Icon (big); the taskbar

    reads ICON_SMALL, which would otherwise stay the hosting exe's icon."""

    def _apply():

        try:

            if not win.events.shown.wait(15):

                return

            native = getattr(win, "native", None)  # winforms Form (pywebview sets window.native)

            if native is None or not icon_path.is_file():

                return

            import ctypes



            user32 = ctypes.windll.user32

            hwnd = native.Handle.ToInt32()

            from System.Drawing import Icon as WinIcon  # pythonnet, loaded by the winforms backend



            small = WinIcon(str(icon_path), 32, 32)

            big = WinIcon(str(icon_path), 64, 64)

            user32.SendMessageW(hwnd, 0x0080, 0, small.Handle.ToInt32())  # WM_SETICON, ICON_SMALL

            user32.SendMessageW(hwnd, 0x0080, 1, big.Handle.ToInt32())  # WM_SETICON, ICON_BIG

        except Exception:

            pass



    threading.Thread(target=_apply, daemon=True).start()





class WindowApi:

    """Custom title-bar controls, exposed to the page as window.pywebview.api

    (the OS frame is removed: frameless=True). webview is imported lazily so

    this module stays importable without the [gui] extra."""

    def __init__(self) -> None:

        self._maximized = False

        self._dragging = False



    def _win(self):

        import webview  # lazy: module stays importable without [gui]

        return webview.windows[0]



    def minimize(self) -> None:

        self._win().minimize()



    def toggle_maximize(self) -> bool:

        if self._maximized:

            self._win().restore()

            self._maximized = False

        else:

            self._win().maximize()

            self._maximized = True

        return self._maximized



    def close(self) -> None:

        self._win().destroy()



    def start_drag(self) -> None:

        """Move the frameless window with the mouse while the left button

        is held. -webkit-app-region is NOT honored by every pywebview

        backend (verified on winforms+WebView2: WM_NCHITTEST over a

        drag region still returns HTCLIENT), and a synthetic

        WM_NCLBUTTONDOWN/HTCAPTION does not move the window either

        because WebView2 keeps mouse capture. So we track the cursor

        ourselves and SetWindowPos the form. No-op elsewhere."""

        import os



        if os.name != "nt" or self._dragging:

            return

        import ctypes

        import threading

        import time



        win = self._win()

        hwnd = 0

        native = getattr(win, "native", None)  # winforms Form: pywebview sets window.native

        if native is not None:

            try:

                hwnd = native.Handle.ToInt32()

            except Exception:

                hwnd = 0

        if not hwnd:

            hwnd = ctypes.windll.user32.FindWindowW(None, win.title)

        if not hwnd:

            return

        self._dragging = True

        user32 = ctypes.windll.user32



        class _POINT(ctypes.Structure):

            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]



        class _RECT(ctypes.Structure):

            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),

                        ("r", ctypes.c_long), ("b", ctypes.c_long)]



        def _loop():

            try:

                pt = _POINT()

                user32.GetCursorPos(ctypes.byref(pt))

                last = (pt.x, pt.y)

                while user32.GetAsyncKeyState(0x01) & 0x8000:  # VK_LBUTTON held

                    pt = _POINT()

                    user32.GetCursorPos(ctypes.byref(pt))

                    dx, dy = pt.x - last[0], pt.y - last[1]

                    if dx or dy:

                        r = _RECT()

                        user32.GetWindowRect(hwnd, ctypes.byref(r))

                        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE

                        user32.SetWindowPos(hwnd, 0, r.l + dx, r.t + dy, 0, 0, 0x0015)

                    last = (pt.x, pt.y)

                    time.sleep(0.01)

            except Exception:

                pass

            finally:

                self._dragging = False



        threading.Thread(target=_loop, daemon=True).start()



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
    geom = _clamp_window_geom(cfg.gui_window or {})  # geometry saved at last close
    if geom:
        wa = _primary_workarea()
        if wa:
            geom = _clamp_to_workarea(geom, wa)  # never restore the window fully off-screen



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



        # frameless window with our own title bar (see #titlebar in index.html):

        # easy_drag must stay False so only the title bar is the drag region

        # (inputs/buttons inside the page must stay clickable). shadow=True keeps

        # the native drop shadow on Windows 11. background_color matches the app

        # theme so there is no white flash while the page loads.

        bg = "#f4f5fa" if cfg.theme == "light" else "#0f1115"

        win = webview.create_window(  # keep the returned Window: webview.windows is emptied at close

            # min_size below the 920px CSS breakpoint: the layout switches to

            # icon-rail sidebar + stacked kanban when the window is narrow

            "DevDash", url=url,
            width=geom["w"] if geom else 1360, height=geom["h"] if geom else 860,
            **({"x": geom["x"], "y": geom["y"]} if geom else {}),  # restore last position
            min_size=(720, 480),

            frameless=True, easy_drag=False, shadow=True,

            background_color=bg, js_api=WindowApi(),
        )
        # remember position + size (+ maximized) when the window closes
        win.events.closed += lambda: _save_window_geom(win, home)
        if geom:
            _restore_window_state(win, geom)

        # taskbar identity: without an explicit AppUserModelID the taskbar groups

        # this window under the hosting exe (python.exe) and shows ITS icon

        if os.name == "nt":

            import ctypes

            from ctypes import wintypes



            shell32 = ctypes.windll.shell32

            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]

            shell32.SetCurrentProcessExplicitAppUserModelID.restype = wintypes.HRESULT

            shell32.SetCurrentProcessExplicitAppUserModelID("DevDash")



        # taskbar / alt-tab icon: winforms sets Form.Icon from webview.start(icon=...)

        icon_path = Path(__file__).resolve().parent / "icon.ico"

        _apply_window_icon(win, icon_path)
        _enable_edge_resize(win)  # frameless => re-add WS_THICKFRAME so edges resize

        webview.start(icon=str(icon_path) if icon_path.is_file() else None)  # blocks until the window is closed



    server.shutdown()

    server.server_close()

