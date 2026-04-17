"""Case Clicker Hub — application entry point.

Run directly for development (`python main.py`). When frozen by PyInstaller
this is the module bundled as `CCHub.exe`.
"""
from cchub.tray import main

if __name__ == "__main__":
    raise SystemExit(main())
