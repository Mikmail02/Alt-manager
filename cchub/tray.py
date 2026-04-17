"""System-tray entry point + mandatory update gate.

Flow on startup:
  1. Check GitHub Releases for a newer version.
  2. If newer -> show blocking modal with only "Oppdater nå" button.
     - Click: download installer, launch silently, exit.
     - If download fails, surface the error and allow retry.
  3. Otherwise start Flask in a background thread, create tray icon.
"""
import logging
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext
from typing import Optional

import pystray
from PIL import Image, ImageDraw

from . import config as app_config
from . import paths, updater
from .version import APP_NAME, __version__

_log = logging.getLogger("cchub.tray")


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


def _open_panel() -> None:
    webbrowser.open(f"https://127.0.0.1:5000/?token={app_config.token()}")


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


def _show_update_modal(release: updater.ReleaseInfo) -> None:
    """Blocking Tk modal; the only way out is to run the update or kill the process."""
    root = tk.Tk()
    root.title(f"{APP_NAME} — ny versjon påkrevd")
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
        text=f"Du kjører {__version__}. Denne oppdateringen er påkrevd.",
        font=("Segoe UI", 10),
        fg="#555",
    ).pack(anchor="w", pady=(2, 10))

    notes = scrolledtext.ScrolledText(frame, width=60, height=10, wrap="word", font=("Segoe UI", 9))
    notes.insert("1.0", release.body or "(ingen changelog)")
    notes.configure(state="disabled")
    notes.pack()

    status_var = tk.StringVar(value="")
    status_label = tk.Label(frame, textvariable=status_var, fg="#c00", font=("Segoe UI", 9))
    status_label.pack(anchor="w", pady=(8, 0))

    btn = tk.Button(frame, text="Oppdater nå", width=18, font=("Segoe UI", 10, "bold"))
    btn.pack(pady=(10, 0))

    def do_update() -> None:
        btn.configure(state="disabled", text="Laster ned...")
        status_var.set("")
        root.update_idletasks()
        installer = updater.download_installer(release)
        if installer is None:
            status_var.set("Nedlasting feilet. Sjekk nettforbindelsen og prøv igjen.")
            btn.configure(state="normal", text="Prøv igjen")
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
    """Poll GitHub once on startup. Returns normally if no update required."""
    release = updater.fetch_latest()
    if updater.is_update_required(release):
        _log.info("Update required: %s -> %s", __version__, release.tag)
        _show_update_modal(release)  # blocks until process exits


def _start_server_thread() -> threading.Thread:
    from app import run_server

    def _runner():
        try:
            run_server()
        except Exception as exc:
            _log.exception("Server crashed: %s", exc)
            messagebox.showerror(APP_NAME, f"Serveren krasjet:\n\n{exc}")

    t = threading.Thread(target=_runner, name="cchub-flask", daemon=True)
    t.start()
    return t


def _build_menu(icon_ref: list) -> pystray.Menu:
    def item_quit(icon, _item):
        icon.stop()

    return pystray.Menu(
        pystray.MenuItem("Åpne panel", lambda icon, _: _open_panel(), default=True),
        pystray.MenuItem("Kopier worker-link", lambda icon, _: _copy_worker_link()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Åpne logger", lambda icon, _: _open_logs()),
        pystray.MenuItem(f"Versjon {__version__}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Avslutt", item_quit),
    )


def main() -> int:
    paths.ensure_dirs()
    logging.basicConfig(
        filename=str(paths.LOG_DIR / "tray.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app_config.load()

    _check_for_update_blocking()

    _start_server_thread()
    time.sleep(0.8)  # let Flask bind before we open the browser

    cfg = app_config.load()
    if not cfg.get("first_run_completed"):
        _open_panel()
        cfg["first_run_completed"] = True
        app_config.save(cfg)

    icon_ref: list = []
    icon = pystray.Icon(
        "cchub",
        _load_icon_image(),
        f"{APP_NAME} {__version__}",
        _build_menu(icon_ref),
    )
    icon_ref.append(icon)
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
