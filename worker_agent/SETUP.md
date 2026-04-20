# Worker Agent — Phase 1 POC Setup

This is the step-by-step guide for getting **one** alt running via the headless Playwright agent on a VPS, reporting back to your local CC Hub. Once this works end-to-end, we move to Phase 2 (porting worker-JS for full feature parity).

**Assumed OS:** Ubuntu 22.04 or Debian 12 on the VPS. Windows VPS — ask for adapted steps.

**Time budget:** ~30–45 minutes the first time (mostly waiting for apt + Playwright Chromium download).

---

## Part A — On your local PC (the hub)

You only do Part A **once**, before touching any VPS.

### A1. Make sure the hub is on the `feat/worker-agent` branch

Open a terminal in `c:\Users\mikae\Documents\Alt manager`:

```bash
git checkout feat/worker-agent
git pull
```

### A2. Start the hub and grab the agent token

Run the hub as you normally do (`python main.py` or launch CCHub from the tray).

You need **two values** for the VPS config:

1. **Hub Tailscale URL** — in the hub sidebar, click **Copy Worker Link**. It gives you something like `http://100.64.1.23:5000/#SOME_TOKEN`. Keep the part **before** the `#`.

   → `http://100.64.1.23:5000`

2. **API token** — open this file:
   - **Windows:** `%APPDATA%\CCHub\config.json`
   - **macOS:** `~/Library/Application Support/CCHub/config.json`
   - **Linux:** `~/.cchub/config.json`

   Copy the `"api_token"` value (long random string).

### A3. Verify the agent heartbeat endpoint is reachable

From your local PC, with the hub running:

```bash
curl -H "X-Alt-Token: <your-token>" http://127.0.0.1:5000/api/agent/state
# Expected: {"ok":true,"agents":{}}
```

If you see `{"ok":true,"agents":{}}` you're good. An empty `agents` object is correct — no agent has checked in yet.

---

## Part B — On the VPS (do this per VPS)

### B1. SSH into the VPS

```bash
ssh your-user@your-vps-ip
```

Verify Tailscale is up and can reach your hub:

```bash
tailscale ip -4
# you should see a 100.x.y.z address

# substitute your hub's Tailscale IP:
curl -I http://100.64.1.23:5000/api/ping
# expected: HTTP/1.1 200 OK
```

If the curl fails, fix Tailscale before continuing. The agent will not work without reachability.

### B2. Run the installer

The installer does all the heavy lifting — apt packages, Python venv, Playwright Chromium download, service user, systemd unit.

```bash
curl -fsSL https://raw.githubusercontent.com/Mikmail02/Alt-manager/feat/worker-agent/worker_agent/scripts/install.sh | sudo bash
```

This takes 5–15 minutes, mostly downloading Chromium. When it finishes you'll see:

```
Install complete. Next steps:
  1. sudo nano /opt/cchub-agent/config.toml ...
```

### B3. Configure the agent

```bash
sudo nano /opt/cchub-agent/config.toml
```

Fill in:

```toml
[agent]
name = "vps-1"                              # any friendly name
hub_url = "http://100.64.1.23:5000"         # from Part A2
token = "paste-the-long-api-token-here"     # from Part A2
heartbeat_interval_sec = 10

[browser]
headless = true
user_data_base = "./user_data"

[[alts]]
id = "alt-001"                              # stable id you pick
username = "MyTestAlt"                      # your CC username (display only)
cookies_file = "./cookies/alt-001.json"
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.

### B4. Export cookies from your existing browser

This is the trickiest part. Pick **ONE** of these methods:

#### Method 1 — `Cookie-Editor` extension (easiest)

1. In your existing Chrome Guest Profile where the alt is logged into case-clicker.com
2. Install **Cookie-Editor** from the Chrome Web Store
3. Navigate to `https://case-clicker.com/`
4. Click the Cookie-Editor icon → menu (three dots) → **Export** → **JSON**
5. This copies JSON like:
   ```json
   [
     {"domain": ".case-clicker.com", "name": "session", "value": "...", ...},
     ...
   ]
   ```
6. Paste this into a new file on the VPS:

   ```bash
   sudo nano /opt/cchub-agent/cookies/alt-001.json
   ```

   Paste, save, close.

#### Method 2 — DevTools manual copy (no extension)

1. In Chrome Guest Profile, open DevTools (F12) on case-clicker.com
2. **Application** tab → **Cookies** → `https://case-clicker.com`
3. You'll need to copy every cookie and hand-build the JSON. Use Method 1 unless you enjoy pain.

### B5. Fix cookie file ownership

```bash
sudo chown -R cchub:cchub /opt/cchub-agent/cookies
sudo chmod 600 /opt/cchub-agent/cookies/*.json
```

### B6. Start the agent

```bash
sudo systemctl enable --now cchub-agent
sudo journalctl -u cchub-agent -f
```

You should see logs like:

```
agent 0.1.0 starting — name=vps-1 hub=http://100.64.1.23:5000 alts=1
Chromium launched (headless=True)
[alt-001] loaded 12 cookies
[alt-001] navigating to https://case-clicker.com/
hub reachable: version=1.1.0
[alt-001] check: online=True path=/ title='Case Clicker ...'
```

`Ctrl+C` exits the log tail (the service keeps running).

### B7. Verify from the hub side

Back on your **local PC**:

```bash
curl -H "X-Alt-Token: <your-token>" http://127.0.0.1:5000/api/agent/state
```

You should see:

```json
{
  "ok": true,
  "agents": {
    "vps-1": {
      "last_seen": 1734567890.12,
      "version": "0.1.0",
      "alts": [
        {
          "id": "alt-001",
          "username": "MyTestAlt",
          "online": true,
          "last_check": 1734567888.0,
          "last_error": "",
          "last_url": "https://case-clicker.com/"
        }
      ]
    }
  }
}
```

**That's Phase 1 complete.** One alt, headless Playwright, heartbeats flowing end-to-end.

---

## Troubleshooting

**Agent starts but hub never sees heartbeats:**
- Check the token is exactly right in `config.toml` (copy-paste issue?)
- `curl http://<hub-ip>:5000/api/ping` from the VPS — Tailscale must be routing
- `sudo journalctl -u cchub-agent -n 100` — look for `heartbeat failed` lines

**`[alt-001] check: online=False last_error='not logged in'`:**
- Cookies expired or wrong — re-export from browser
- Check the Guest Profile in your existing Chrome still has the alt logged in

**`Chromium launched` never appears, agent crashes:**
- Re-run the Chromium install: `sudo -u cchub PLAYWRIGHT_BROWSERS_PATH=/opt/cchub-agent/.playwright /opt/cchub-agent/venv/bin/python -m playwright install chromium`
- Check disk space: `df -h`

**Want to see what Chromium actually sees?**

Add this snippet temporarily to `agent/alt_runner.py` in `_check_login` to save a screenshot:

```python
await self._page.screenshot(path=f"/tmp/alt-{self.alt_id}.png", full_page=True)
```

Then `scp cchub@vps-ip:/tmp/alt-alt-001.png .` to pull it locally.

---

## Updating the agent after code changes on main

```bash
sudo -u cchub git -C /opt/cchub-agent pull
sudo systemctl restart cchub-agent
```

If requirements changed: `sudo /opt/cchub-agent/venv/bin/pip install -r /opt/cchub-agent/worker_agent/requirements.txt` before restart.

---

## What Phase 1 does NOT include (coming in Phase 2+)

- Porting `mainacc.js` / `altacc.js` worker logic (vault scan, convert, booster, transfer)
- Hub UI for agent registration and alt binding
- Dual-mode coexistence with Tampermonkey workers
- Cookie auto-refresh / credential-based login
- Crash recovery and per-context restart

Phase 1 just proves: **a headless Chromium on a VPS can stay logged into case-clicker.com and heartbeat the hub reliably.** Once that's solid we layer the worker features on top.
