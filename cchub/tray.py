"""Desktop app shell: pywebview window + system-tray icon + update gate.

Main thread owns pywebview (required on Windows for EdgeWebView2). The tray
icon and Flask server both run on background threads. Closing the window
hides it to tray; only "Avslutt" from the tray menu actually exits.
"""
import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import Optional

import pystray
import webview
from PIL import Image, ImageDraw

from . import config as app_config
from . import paths, updater
from .version import APP_NAME, __version__

_log = logging.getLogger("cchub.tray")

_window: Optional[webview.Window] = None
_icon: Optional[pystray.Icon] = None
_is_maximized = False


class _WindowApi:
    """Methods exposed to the frontend as window.pywebview.api.*"""

    def minimize(self) -> None:
        if _window is not None:
            try:
                _window.minimize()
            except Exception as exc:
                _log.debug("minimize failed: %s", exc)

    def toggle_maximize(self) -> None:
        global _is_maximized
        if _window is None:
            return
        try:
            if _is_maximized:
                _window.restore()
                _is_maximized = False
            else:
                _window.maximize()
                _is_maximized = True
        except Exception as exc:
            _log.debug("toggle_maximize failed: %s", exc)

    def hide_to_tray(self) -> None:
        _hide_window()

    def quit(self) -> None:
        _quit_app()


def _load_icon_image() -> Image.Image:
    if paths.ICON_FILE.exists():
        try:
            return Image.open(paths.ICON_FILE)
        except Exception:
            pass
    img = Image.new("RGB", (64, 64), color=(24, 24, 27))
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, 56, 56], outline=(16, 185, 129), width=3)
    draw.text((20, 20), "CC", fill=(228, 228, 231))
    return img


def _show_window() -> None:
    if _window is None:
        return
    try:
        _window.show()
        _window.restore()
    except Exception as exc:
        _log.debug("show_window failed: %s", exc)


def _hide_window() -> None:
    if _window is None:
        return
    try:
        _window.hide()
    except Exception as exc:
        _log.debug("hide_window failed: %s", exc)


def _copy_worker_link() -> None:
    import pyperclip

    link = f"https://127.0.0.1:5000#{app_config.token()}"
    pyperclip.copy(link)


def _open_logs() -> None:
    try:
        import subprocess

        subprocess.Popen(["explorer", str(paths.LOG_DIR)])
    except Exception:
        pass


def _quit_app() -> None:
    global _icon, _window
    if _icon is not None:
        try:
            _icon.stop()
        except Exception:
            pass
    if _window is not None:
        try:
            _window.destroy()
        except Exception:
            pass


# --- Mandatory update gate -----------------------------------------------

def _show_update_modal(release: updater.ReleaseInfo) -> None:
    root = tk.Tk()
    root.title(f"{APP_NAME} \u2014 ny versjon p\u00e5krevd")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    try:
        root.iconbitmap(str(paths.ICON_FILE))
    except Exception:
        pass
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    frame = tk.Frame(root, padx=24, pady=20)
    frame.pack()

    tk.Label(
        frame,
        text=f"Ny versjon {release.tag} er tilgjengelig",
        font=("Segoe UI", 14, "bold"),
    ).pack(anchor="w")
    tk.Label(
        frame,
        text=f"Du kj\u00f8rer {__version__}. Denne oppdateringen er p\u00e5krevd.",
        font=("Segoe UI", 10),
        fg="#555",
    ).pack(anchor="w", pady=(2, 10))

    notes = scrolledtext.ScrolledText(frame, width=60, height=10, wrap="word", font=("Segoe UI", 9))
    notes.insert("1.0", release.body or "(ingen changelog)")
    notes.configure(state="disabled")
    notes.pack()

    status_var = tk.StringVar(value="")
    tk.Label(frame, textvariable=status_var, fg="#c00", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

    btn = tk.Button(frame, text="Oppdater n\u00e5", width=18, font=("Segoe UI", 10, "bold"))
    btn.pack(pady=(10, 0))

    def do_update() -> None:
        btn.configure(state="disabled", text="Laster ned...")
        status_var.set("")
        root.update_idletasks()
        installer = updater.download_installer(release)
        if installer is None:
            status_var.set("Nedlasting feilet. Sjekk nettforbindelsen og pr\u00f8v igjen.")
            btn.configure(state="normal", text="Pr\u00f8v igjen")
            return
        status_var.set("Starter installer...")
        root.update_idletasks()
        time.sleep(0.5)
        updater.launch_installer_and_exit(installer)

    btn.configure(command=do_update)

    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    root.mainloop()


def _check_for_update_blocking() -> None:
    release = updater.fetch_latest()
    if updater.is_update_required(release):
        _log.info("Update required: %s -> %s", __version__, release.tag)
        _show_update_modal(release)


# --- Background services -------------------------------------------------

def _start_server_thread() -> threading.Thread:
    from app import run_server

    def _runner():
        try:
            run_server()
        except Exception as exc:
            _log.exception("Server crashed: %s", exc)
            try:
                messagebox.showerror(APP_NAME, f"Serveren krasjet:\n\n{exc}")
            except Exception:
                pass

    t = threading.Thread(target=_runner, name="cchub-flask", daemon=True)
    t.start()
    return t


def _start_tray_thread() -> threading.Thread:
    """Run pystray on a background thread. Main thread is owned by pywebview."""

    def _runner():
        global _icon

        menu = pystray.Menu(
            pystray.MenuItem("\u00c5pne panel", lambda icon, _: _show_window(), default=True),
            pystray.MenuItem("Kopier worker-link", lambda icon, _: _copy_worker_link()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("\u00c5pne logger", lambda icon, _: _open_logs()),
            pystray.MenuItem(f"Versjon {__version__}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Avslutt", lambda icon, _: _quit_app()),
        )
        _icon = pystray.Icon("cchub", _load_icon_image(), f"{APP_NAME} {__version__}", menu)
        try:
            _icon.run()
        except Exception as exc:
            _log.exception("Tray crashed: %s", exc)

    t = threading.Thread(target=_runner, name="cchub-tray", daemon=True)
    t.start()
    return t


def _wait_for_server(timeout: float = 5.0) -> None:
    """Poll /api/ping until the server is ready or timeout."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    deadline = time.time() + timeout
    url = "https://127.0.0.1:5000/api/ping"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5, context=ctx)
            return
        except Exception:
            time.sleep(0.15)


# --- Entry ---------------------------------------------------------------

def main() -> int:
    global _window

    paths.ensure_dirs()
    logging.basicConfig(
        filename=str(paths.LOG_DIR / "tray.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app_config.load()

    _check_for_update_blocking()

    _start_server_thread()
    _wait_for_server()
    _start_tray_thread()

    _window = webview.create_window(
        title=f"{APP_NAME} {__version__}",
        url="https://127.0.0.1:5000/",
        width=1400,
        height=900,
        min_size=(1000, 640),
        resizable=True,
        confirm_close=False,
        frameless=True,
        easy_drag=False,
        background_color="#09090b",
        js_api=_WindowApi(),
    )

    def _on_closing() -> bool:
        _hide_window()
        return False  # cancel close; we only hide to tray

    _window.events.closing += _on_closing

    icon_path = str(paths.ICON_FILE) if paths.ICON_FILE.exists() else None
    try:
        webview.start(icon=icon_path, private_mode=False)
    except TypeError:
        # Older pywebview without `icon` kwarg.
        webview.start(private_mode=False)

    _quit_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
