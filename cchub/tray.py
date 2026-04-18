"""Desktop app shell: pywebview window + system-tray icon + update gate.

Main thread owns pywebview (required on Windows for EdgeWebView2). The tray
icon and Flask server both run on background threads. Closing the window
hides it to tray; only the tray "Exit" item actually exits the process.
"""
import logging
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Optional

import pystray
import webview
from PIL import Image, ImageDraw

from . import config as app_config
from . import network, paths, updater
from .version import APP_NAME, __version__

_log = logging.getLogger("cchub.tray")

_window: Optional[webview.Window] = None
_icon: Optional[pystray.Icon] = None
_is_maximized = False

_MIN_W = 780
_MIN_H = 520


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

    def is_maximized(self) -> bool:
        return bool(_is_maximized)

    def hide_to_tray(self) -> None:
        _hide_window()

    def quit(self) -> None:
        _quit_app()

    def get_rect(self):
        if _window is None:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        try:
            return {
                "x": int(_window.x),
                "y": int(_window.y),
                "w": int(_window.width),
                "h": int(_window.height),
            }
        except Exception:
            return {"x": 0, "y": 0, "w": 0, "h": 0}

    def start_drag(self) -> None:
        """Native title-bar drag (fallback when data-pywebview-drag-region is unavailable)."""
        if _window is None:
            return
        try:
            # pywebview >= 5 exposes this helper on Windows.
            move = getattr(_window, "move_start", None)
            if callable(move):
                move()
        except Exception as exc:
            _log.debug("start_drag failed: %s", exc)

    def resize_window(self, w, h) -> None:
        """Resize to an absolute width/height. Clamped to min size."""
        global _is_maximized
        if _window is None:
            return
        try:
            w = max(_MIN_W, int(w))
            h = max(_MIN_H, int(h))
            _window.resize(w, h)
            _is_maximized = False
        except Exception as exc:
            _log.debug("resize_window failed: %s", exc)

    def move_window(self, x, y) -> None:
        """Move window to an absolute screen position."""
        global _is_maximized
        if _window is None:
            return
        try:
            _window.move(int(x), int(y))
            _is_maximized = False
        except Exception as exc:
            _log.debug("move_window failed: %s", exc)

    def move_and_resize(self, x, y, w, h) -> None:
        """Combined move + resize for left/top edge drags (single call = less flicker)."""
        global _is_maximized
        if _window is None:
            return
        try:
            w = max(_MIN_W, int(w))
            h = max(_MIN_H, int(h))
            _window.resize(w, h)
            _window.move(int(x), int(y))
            _is_maximized = False
        except Exception as exc:
            _log.debug("move_and_resize failed: %s", exc)


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


def _worker_base_url() -> str:
    """Best URL for workers: manual override > auto-detected Tailscale IP > localhost."""
    override = app_config.public_url()
    if override:
        return override
    detected = network.detect_tailscale_ip()
    if detected:
        return f"http://{detected}:5000"
    return "http://127.0.0.1:5000"


def _copy_worker_link() -> None:
    import pyperclip

    link = f"{_worker_base_url()}#{app_config.token()}"
    pyperclip.copy(link)


_REMOTE_DIALOG_OPEN = False


def _show_remote_access_dialog() -> None:
    """Small tkinter dialog to override the worker URL.

    Normally CC Hub auto-detects the Tailscale IP and this dialog is unused.
    Pasting a URL here configures:
      - config.public_url (used when copying the worker link)
      - config.extra_cert_hosts (the host is added as a SAN on next cert regen)
    """
    global _REMOTE_DIALOG_OPEN
    if _REMOTE_DIALOG_OPEN:
        return
    _REMOTE_DIALOG_OPEN = True

    def _runner():
        global _REMOTE_DIALOG_OPEN
        try:
            root = tk.Tk()
            root.title("Remote access")
            root.configure(bg="#09090b")
            root.geometry("520x280")
            root.resizable(False, False)
            try:
                _enable_dark_titlebar(root)
            except Exception:
                pass
            if paths.ICON_FILE.exists():
                try:
                    root.iconbitmap(str(paths.ICON_FILE))
                except Exception:
                    pass

            tk.Label(
                root,
                text="Remote access",
                bg="#09090b",
                fg="#fafafa",
                font=("Segoe UI", 14, "bold"),
            ).pack(pady=(18, 4), anchor="w", padx=22)

            detected = network.detect_tailscale_ip()
            if detected:
                info_text = (
                    f"Auto-detected Tailscale IP: {detected}\n"
                    "Leave blank to use it automatically, or override below."
                )
                info_fg = "#10b981"
            else:
                info_text = (
                    "No Tailscale detected. Paste the URL that workers\n"
                    "should connect to (e.g. http://100.x.y.z:5000)."
                )
                info_fg = "#a1a1aa"

            tk.Label(
                root,
                text=info_text,
                bg="#09090b",
                fg=info_fg,
                font=("Segoe UI", 9),
                justify="left",
            ).pack(pady=(0, 10), anchor="w", padx=22)

            entry_var = tk.StringVar(value=app_config.public_url())
            entry = tk.Entry(
                root,
                textvariable=entry_var,
                bg="#18181b",
                fg="#fafafa",
                insertbackground="#fafafa",
                relief="flat",
                font=("Segoe UI", 10),
            )
            entry.pack(fill="x", padx=22, ipady=6)
            entry.focus_set()

            status_var = tk.StringVar(value="")
            tk.Label(
                root,
                textvariable=status_var,
                bg="#09090b",
                fg="#10b981",
                font=("Segoe UI", 9),
            ).pack(pady=(8, 0), anchor="w", padx=22)

            def _save() -> None:
                raw = entry_var.get().strip().rstrip("/")
                if raw and not re.match(r"^https?://", raw, re.IGNORECASE):
                    raw = "http://" + raw
                app_config.set_public_url(raw)
                status_var.set("Saved.")

            def _clear() -> None:
                entry_var.set("")
                app_config.set_public_url("")
                status_var.set("Cleared — worker link now uses 127.0.0.1:5000.")

            btn_row = tk.Frame(root, bg="#09090b")
            btn_row.pack(fill="x", padx=22, pady=(14, 0))

            tk.Button(
                btn_row,
                text="Save",
                command=_save,
                bg="#10b981",
                fg="#052e1a",
                activebackground="#059669",
                activeforeground="#ffffff",
                relief="flat",
                font=("Segoe UI", 10, "bold"),
                padx=16,
                pady=6,
            ).pack(side="right")

            tk.Button(
                btn_row,
                text="Clear",
                command=_clear,
                bg="#27272a",
                fg="#fafafa",
                activebackground="#3f3f46",
                activeforeground="#fafafa",
                relief="flat",
                font=("Segoe UI", 10),
                padx=16,
                pady=6,
            ).pack(side="right", padx=(0, 8))

            tk.Button(
                btn_row,
                text="Close",
                command=root.destroy,
                bg="#18181b",
                fg="#a1a1aa",
                activebackground="#27272a",
                activeforeground="#fafafa",
                relief="flat",
                font=("Segoe UI", 10),
                padx=16,
                pady=6,
            ).pack(side="left")

            root.mainloop()
        except Exception as exc:
            _log.exception("remote access dialog crashed: %s", exc)
        finally:
            _REMOTE_DIALOG_OPEN = False

    threading.Thread(target=_runner, name="cchub-remote-dialog", daemon=True).start()


def _extract_host(url: str) -> str:
    m = re.match(r"^https?://([^/:]+)(?::\d+)?", (url or "").strip(), re.IGNORECASE)
    return m.group(1) if m else ""


def _open_logs() -> None:
    import subprocess
    import sys

    path = str(paths.LOG_DIR)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
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

# Conventional-commit prefix → human-readable label.
_COMMIT_LABELS = {
    "feat": "New",
    "fix": "Fix",
    "perf": "Perf",
    "refactor": "Refactor",
    "docs": "Docs",
    "style": "Style",
    "test": "Tests",
    "build": "Build",
    "ci": "CI",
    "chore": "Chore",
    "revert": "Revert",
}


def _format_commit_line(subject: str) -> Optional[str]:
    """Convert a commit subject into a human-friendly changelog bullet.

    Drops noisy entries (chore/ci/build/style/docs) so the modal stays focused
    on what the user actually cares about.
    """
    s = (subject or "").strip()
    if not s:
        return None
    # Match "type(scope)!: message" or "type: message".
    m = re.match(r"^([a-zA-Z]+)(?:\([^)]*\))?!?:\s*(.+)$", s)
    if m:
        kind = m.group(1).lower()
        msg = m.group(2).strip()
        if kind in {"chore", "ci", "build", "style", "docs", "test"}:
            return None
        label = _COMMIT_LABELS.get(kind)
        msg = msg[:1].upper() + msg[1:] if msg else msg
        return f"{label}: {msg}" if label else msg
    # Plain subject line.
    return s[:1].upper() + s[1:]


def _build_changelog_items(release: updater.ReleaseInfo) -> list:
    """Prefer GitHub compare commits; fall back to parsing the release body."""
    old_tag = f"v{__version__}"
    commits = updater.fetch_commits_between(old_tag, release.tag)
    items: list = []
    for subj in commits:
        line = _format_commit_line(subj)
        if line:
            items.append(line)
    if items:
        return items
    # Fall back: parse body as plain bullets.
    for raw in (release.body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("**full changelog"):
            continue
        if line.startswith("#"):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            continue
        line = re.sub(r"^[-*\u2022]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"\*(.*?)\*", r"\1", line)
        line = re.sub(r"\s+by\s+@\w+\s+in\s+.*$", "", line)
        if line:
            items.append(line)
    return items


def _enable_dark_titlebar(root: tk.Tk) -> None:
    """Best-effort: ask Windows DWM to draw the title bar in dark mode."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(root.wm_frame(), 16))
        value = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 2004+); 19 on older.
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


def _show_update_modal(release: updater.ReleaseInfo) -> None:
    # Palette (matches the in-app dark theme).
    BG = "#09090b"
    CARD = "#18181b"
    BORDER = "#27272a"
    FG = "#e4e4e7"
    MUTED = "#a1a1aa"
    ACCENT = "#10b981"
    ACCENT_HOVER = "#059669"
    DANGER = "#ef4444"

    root = tk.Tk()
    root.title(f"{APP_NAME} \u2014 Update Required")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg=BG)
    try:
        root.iconbitmap(str(paths.ICON_FILE))
    except Exception:
        pass
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    root.update_idletasks()
    _enable_dark_titlebar(root)

    outer = tk.Frame(root, bg=BG, padx=28, pady=24)
    outer.pack()

    tk.Label(
        outer,
        text="UPDATE REQUIRED",
        font=("Segoe UI", 9, "bold"),
        fg=ACCENT,
        bg=BG,
    ).pack(anchor="w")

    tk.Label(
        outer,
        text=f"Version {release.tag} is available",
        font=("Segoe UI", 18, "bold"),
        fg=FG,
        bg=BG,
    ).pack(anchor="w", pady=(4, 4))

    tk.Label(
        outer,
        text=f"You're on {__version__}. This update is mandatory.",
        font=("Segoe UI", 10),
        fg=MUTED,
        bg=BG,
    ).pack(anchor="w", pady=(0, 16))

    tk.Label(
        outer,
        text="What's new",
        font=("Segoe UI", 10, "bold"),
        fg=FG,
        bg=BG,
    ).pack(anchor="w", pady=(0, 6))

    card = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    card.pack(fill="x")

    items = _build_changelog_items(release) or ["See the GitHub release for details."]
    MAX_ITEMS = 10
    visible = items[:MAX_ITEMS]
    for idx, item in enumerate(visible):
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(10 if idx == 0 else 4, 4))
        tk.Label(
            row,
            text="\u2022",
            font=("Segoe UI", 11, "bold"),
            fg=ACCENT,
            bg=CARD,
            width=2,
            anchor="n",
        ).pack(side="left", anchor="n")
        tk.Label(
            row,
            text=item,
            font=("Segoe UI", 10),
            fg=FG,
            bg=CARD,
            wraplength=440,
            justify="left",
            anchor="w",
        ).pack(side="left", fill="x", expand=True, anchor="w")
    if len(items) > MAX_ITEMS:
        tk.Label(
            card,
            text=f"+ {len(items) - MAX_ITEMS} more",
            font=("Segoe UI", 9, "italic"),
            fg=MUTED,
            bg=CARD,
        ).pack(anchor="w", padx=14, pady=(4, 10))
    else:
        tk.Frame(card, bg=CARD, height=10).pack()

    status_var = tk.StringVar(value="")
    status_label = tk.Label(
        outer,
        textvariable=status_var,
        fg=DANGER,
        bg=BG,
        font=("Segoe UI", 9),
    )
    status_label.pack(anchor="w", pady=(14, 8))

    btn = tk.Button(
        outer,
        text="Update now",
        font=("Segoe UI", 11, "bold"),
        bg=ACCENT,
        fg="#0b0b0b",
        activebackground=ACCENT_HOVER,
        activeforeground="#0b0b0b",
        relief="flat",
        borderwidth=0,
        pady=10,
        cursor="hand2",
    )
    btn.pack(fill="x")

    def do_update() -> None:
        btn.configure(state="disabled", text="Downloading\u2026", bg=BORDER, fg=MUTED)
        status_var.set("")
        root.update_idletasks()
        installer = updater.download_installer(release)
        if installer is None:
            status_var.set("Download failed. Check your connection and try again.")
            btn.configure(state="normal", text="Try again", bg=ACCENT, fg="#0b0b0b")
            return
        btn.configure(text="Launching installer\u2026")
        root.update_idletasks()
        time.sleep(0.5)
        updater.launch_installer_and_exit(installer)

    btn.configure(command=do_update)

    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
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
                messagebox.showerror(APP_NAME, f"Server crashed:\n\n{exc}")
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
            pystray.MenuItem("Open panel", lambda icon, _: _show_window(), default=True),
            pystray.MenuItem("Copy worker link", lambda icon, _: _copy_worker_link()),
            pystray.MenuItem("Configure remote access...", lambda icon, _: _show_remote_access_dialog()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open logs", lambda icon, _: _open_logs()),
            pystray.MenuItem(f"Version {__version__}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, _: _quit_app()),
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
    url = "http://127.0.0.1:5000/api/ping"
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
        url="http://127.0.0.1:5000/",
        width=1280,
        height=820,
        min_size=(_MIN_W, _MIN_H),
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
