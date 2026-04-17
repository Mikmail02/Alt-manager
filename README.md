# Alt Manager - Setup Guide

This guide explains exactly how to run this Alt Manager on a new PC from scratch.

## What This Project Includes

- `app.py` - local Flask server + web panel (`http://localhost:5000`)
- `altacc.js` - Tampermonkey worker script for alt accounts
- `mainacc.js` - Tampermonkey worker script for main account
- `cases.json` - case/capsule list used by Convert/Booster
- `accounts.json` / `settings.json` - local runtime data (auto-created/updated)

## 1) Install Required Software

Install these first:

1. **Python 3.11+**
   - Download: `https://www.python.org/downloads/`
   - On Windows, enable **"Add Python to PATH"** during install

2. **Google Chrome**
   - Download: `https://www.google.com/chrome/`

3. **Tampermonkey (Chrome extension)**
   - Chrome Web Store: `https://www.tampermonkey.net/`

4. **ngrok**
   - Download: `https://ngrok.com/download`
   - Sign up: `https://dashboard.ngrok.com/signup`

## 2) Prepare the Project Folder

Put all project files in one folder, for example:

`C:\Users\<you>\Documents\Alt manager`

On macOS, for example:

`/Users/<you>/Documents/Alt manager`

You should have at least:

- `app.py`
- `altacc.js`
- `mainacc.js`
- `cases.json`

## 3) Install Python Dependencies

Open a terminal in the project folder and run:

```bash
pip install flask flask-cors
```

If `pip` is not recognized:

```bash
python -m pip install flask flask-cors
```

## 4) Configure ngrok

In terminal:

1. Add your auth token (from ngrok dashboard):

```bash
ngrok config add-authtoken <YOUR_NGROK_TOKEN>
```

2. (Later) start tunnel to local panel:

```bash
ngrok http 5000
```

Keep this terminal open while using the manager.

## 5) Start Alt Manager Backend

In a second terminal window, run:

```bash
python app.py
```

Expected: Flask starts on `0.0.0.0:5000`.

Keep this terminal open while using the manager.

## 6) Open the Local Panel

Open in browser:

`http://localhost:5000`

Panel should load with sidebar and tabs.

## 7) Install Tampermonkey Scripts

In Tampermonkey:

1. Create script for **alts**, paste `altacc.js`, save.
2. Create script for **main**, paste `mainacc.js`, save.

Both scripts are set to:

- `@match *://*.case-clicker.com/*`

So they run automatically on case-clicker pages.

## 8) Connect Workers to Your ngrok URL

1. Start `ngrok http 5000`.
2. Copy the public `https://...ngrok...` URL.
3. Open each case-clicker tab where a worker runs.
4. In the worker box (bottom-right), paste URL and click **CONNECT**.
5. Optionally click **TEST LINK** first.

Expected state: `CONNECTED`.

## 9) Main Account Setup (Important)

In the local panel:

1. Let accounts appear via heartbeat.
2. Copy your **main account ID** from selected account.
3. Paste in Settings/Main ID field and save.

This is required for transfer automation (`join_and_confirm` flow).

## 10) Recommended Run Order

1. Start backend: `python app.py`
2. Start tunnel: `ngrok http 5000`
3. Open panel: `http://localhost:5000`
4. Open case-clicker tabs (main + alts)
5. Ensure workers show `CONNECTED`
6. Verify accounts appear in panel
7. Run features (Inventory/Transfer/Convert/Booster)

## 11) Quick Troubleshooting

### `Heartbeat failed (502)`

- Flask backend is not running or crashed
- Restart `python app.py`
- Confirm ngrok points to port `5000`

### Worker says disconnected

- Re-paste ngrok URL and press **CONNECT**
- Check that ngrok terminal is still running
- Use **TEST LINK**

### Accounts flicker offline/online

- Make sure backend and ngrok are stable
- Keep one tab per worker account active/loaded

### Convert buy fails with status 400

- Usually API-side validation/rate issues
- Retry after a few seconds
- Ensure selected case/capsule exists in `cases.json`

## macOS Differences Only

These are the only practical differences from the steps above:

- Use `python3`/`pip3` if `python`/`pip` are not available:
  - `python3 -m pip install flask flask-cors`
  - `python3 app.py`
- If ngrok is not in PATH after install, run it via full path or install with Homebrew:
  - `brew install ngrok/ngrok/ngrok`
- Project path format uses `/Users/...` instead of `C:\Users\...`

### No profile image

- Some external image hosts can block/hotlink intermittently
- UI uses fallback avatar automatically

## 12) Notes

- This project is designed around Chrome + Tampermonkey and works on Windows/macOS with the same workflow.
- `accounts.json` and `settings.json` are runtime files and will change often.
- Do not close terminals running Flask/ngrok while the system is active.
```
