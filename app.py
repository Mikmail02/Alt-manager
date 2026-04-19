import json
import os
import re
import time
import tempfile
import threading
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

from cchub import auth, config as app_config, network, paths, updater
from cchub.version import __version__

PORT = 5000
# Bind on all interfaces so Tailscale/LAN workers can reach the hub.
# TLS + X-Alt-Token header guard all /api routes.
HOST = "0.0.0.0"

paths.ensure_dirs()
paths.migrate_legacy_data(paths.RESOURCE_DIR)

DATA_FILE = str(paths.ACCOUNTS_FILE)
SETTINGS_FILE = str(paths.SETTINGS_FILE)
CASES_FILE = str(paths.CASES_FILE)

app = Flask(__name__)
auth.install(app)
DB_LOCK = threading.RLock()


@app.route("/api/ping")
def api_ping():
    return jsonify({"ok": True, "version": __version__})


@app.route("/api/remote_url", methods=["GET", "POST"])
def api_remote_url():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        normalized = network.normalize_remote_url(data.get("url") or "", PORT)
        app_config.set_public_url(normalized)
        return jsonify({"ok": True, "url": app_config.public_url()})
    return jsonify({
        "url": app_config.public_url(),
        "detected": network.detect_tailscale_ip() or "",
        "port": PORT,
    })


@app.route("/config")
def panel_config():
    base = network.worker_base_url(app_config.public_url(), PORT)
    return jsonify({
        "token": app_config.token(),
        "version": __version__,
        "base_url": base,
    })


@app.route("/api/check_update", methods=["POST"])
def api_check_update():
    """Manual update check from the sidebar button."""
    try:
        release = updater.fetch_latest()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"fetch_latest crashed: {exc}"}), 500
    if release is None:
        return jsonify({
            "ok": False,
            "error": "Could not reach GitHub (network or SSL issue). Check logs in tray → Open logs.",
        }), 502
    required = updater.is_update_required(release)
    return jsonify({
        "ok": True,
        "current": __version__,
        "latest": release.tag,
        "update_available": required,
        "has_installer": release.installer_url is not None,
    })


@app.route("/api/apply_update", methods=["POST"])
def api_apply_update():
    """Download and launch the latest installer. On success the response returns
    just before the process exits so the UI can show a progress state briefly."""
    release = updater.fetch_latest()
    if release is None or not updater.is_update_required(release):
        return jsonify({"ok": False, "error": "No update available"}), 400

    installer = updater.download_installer(release)
    if installer is None:
        return jsonify({"ok": False, "error": "Download failed"}), 502

    def _launch_and_exit():
        time.sleep(0.4)  # let the HTTP response flush
        updater.launch_installer_and_exit(installer)

    threading.Thread(target=_launch_and_exit, name="cchub-apply-update", daemon=True).start()
    return jsonify({"ok": True, "tag": release.tag})

def load_db():
    if not os.path.exists(DATA_FILE):
        return []
    # Retry briefly to avoid transient reads while writer is replacing file.
    for _ in range(3):
        try:
            with DB_LOCK:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except json.JSONDecodeError:
            time.sleep(0.03)
        except:
            return []
    return []

def save_db(data):
    # Atomic write prevents partial JSON files during concurrent requests.
    with DB_LOCK:
        parent = os.path.dirname(os.path.abspath(DATA_FILE)) or '.'
        fd, tmp_path = tempfile.mkstemp(prefix='accounts_', suffix='.tmp', dir=parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, DATA_FILE)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass

def load_settings():
    if not os.path.exists(SETTINGS_FILE): return {"main_id": ""}
    try:
        with open(SETTINGS_FILE, 'r') as f: return json.load(f)
    except: return {"main_id": ""}

def load_cases():
    if not os.path.exists(CASES_FILE): return []
    try:
        with open(CASES_FILE, 'r') as f: return json.load(f)
    except: return []

def parse_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except:
            return 0
    return 0

@app.route('/')
def index():
    return render_template_string(
        HTML_UI.replace("__CCHUB_TOKEN__", app_config.token()).replace("__CCHUB_VERSION__", __version__)
    )

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.json
    acc_id = data.get('id')
    
    if not acc_id: return jsonify({"status": "error"}), 400
    
    db = load_db()
    account = next((a for a in db if a['id'] == acc_id), None)
    
    if not account:
        account = {
            "id": acc_id, 
            "commands": [], 
            "active_trade": None, 
            "status_log": "Waiting...",
            "inventory_items": [] 
        }
        db.append(account)
    
    account['username'] = data.get('username')
    incoming_avatar = data.get('avatar')
    if incoming_avatar:
        account['avatar'] = incoming_avatar
    
    account['last_seen'] = time.time()
    # Reset refresh attempt counter on successful heartbeat
    account['last_refresh_attempt'] = 0
    
    if 'stats' in data:
        account['stats'] = data['stats']
        if 'skinCount' in data:
            account['stats']['skinCount'] = data['skinCount']
    if 'profile' in data and isinstance(data['profile'], dict):
        profile = account.get('profile', {})
        profile.update(data['profile'])
        account['profile'] = profile
        # Prefer direct profile image when worker provides it.
        profile_image = profile.get('image')
        if profile_image:
            account['avatar'] = profile_image
    
    cmds = account.get('commands', [])
    account['commands'] = [] 
    
    save_db(db)
    return jsonify({"status": "ok", "commands": cmds})

@app.route('/api/update_inventory', methods=['POST'])
def update_inventory():
    data = request.json
    acc_id = data.get('id')
    items = data.get('items', [])
    
    db = load_db()
    account = next((a for a in db if a['id'] == acc_id), None)
    if account:
        account['inventory_items'] = items
        save_db(db)
    return jsonify({"success": True})

@app.route('/api/queue/skin_action', methods=['POST'])
def skin_action():
    data = request.json
    acc_id = data.get('acc_id')
    action = data.get('action') 
    skin_id = data.get('skin_id')
    
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    
    cmd = {}
    if action == 'favorite':
        cmd = {"type": "toggle_favorite", "skinId": skin_id, "isFavorite": data.get('state')}
        acc['status_log'] = f"Toggling favorite on skin {skin_id}..."
    elif action == 'sell_tokens':
        cmd = {"type": "sell_tokens", "skinIds": [skin_id]}
        acc['status_log'] = "Selling item for tokens..."
    elif action == 'sell_money':
        cmd = {"type": "sell_money", "skinIds": [skin_id]}
        acc['status_log'] = "Selling item for money..."

    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append(cmd)
    
    save_db(db)
    return jsonify({"success": True})

# --- Auto-trigger: when an alt posts a trade link, queue the main to join it. ---
@app.route('/api/log_status', methods=['POST'])
def log_status():
    data = request.json
    acc_id = data.get('id')
    msg = data.get('msg')
    
    db = load_db()
    account = next((a for a in db if a['id'] == acc_id), None)
    
    if account:
        account['status_log'] = msg
        
        # Check for PROGRESS updates from convert job
        progress_match = re.search(r'PROGRESS:(\d+):(\d+):(\d+):(\d+)(?::(\d+)x(\d+))?', msg)
        if progress_match:
            opened, total, remaining, progress = map(int, progress_match.groups()[:4])
            batch_size = int(progress_match.group(5)) if progress_match.group(5) else None
            multiplier = int(progress_match.group(6)) if progress_match.group(6) else None
            
            if 'convert_job' in account:
                account['convert_job']['opened'] = opened
                account['convert_job']['total'] = total
                account['convert_job']['remaining'] = remaining
                account['convert_job']['progress'] = progress
                account['convert_job']['last_progress_update'] = time.time()  # Track last progress update
                if batch_size is not None:
                    account['convert_job']['batchSize'] = batch_size
                if multiplier is not None:
                    account['convert_job']['multiplier'] = multiplier
                if progress >= 100:
                    account['convert_job']['active'] = False
                    account['convert_job']['done'] = True  # Mark as done but keep visible

        convert_failed = re.search(r'CONVERT_FAILED:(.+)', msg)
        if convert_failed and 'convert_job' in account:
            account['convert_job']['active'] = False
            account['convert_job']['done'] = False
            account['convert_job']['error'] = convert_failed.group(1)
            account['convert_job']['last_progress_update'] = time.time()

        convert_start = re.search(r'CONVERT_START:([\d.]+),([\d.]+)', msg)
        if convert_start and 'convert_job' in account:
            try:
                tokens_s = convert_start.group(1)
                money_s = convert_start.group(2)
                if account['convert_job'].get('tokens_start') is None:
                    account['convert_job']['tokens_start'] = float(tokens_s)
                if account['convert_job'].get('money_start') is None:
                    account['convert_job']['money_start'] = float(money_s)
            except (ValueError, TypeError):
                pass

        # Booster updates
        booster_progress = re.search(r'BOOSTER_PROGRESS:(\d+):(\d+):(\d+):(\d+):(\d+)x(\d+):(\d+)', msg)
        if booster_progress and 'booster_job' in account:
            opened, total, remaining, progress, batch_size, multiplier, cycle = map(int, booster_progress.groups())
            account['booster_job']['opened'] = opened
            account['booster_job']['total'] = total
            account['booster_job']['remaining'] = remaining
            account['booster_job']['progress'] = progress
            account['booster_job']['batchSize'] = batch_size
            account['booster_job']['multiplier'] = multiplier
            account['booster_job']['cycle'] = cycle
            account['booster_job']['last_progress_update'] = time.time()

        booster_wait = re.search(r'BOOSTER_WAIT:(\d+)', msg)
        if booster_wait and 'booster_job' in account:
            account['booster_job']['waiting_seconds'] = int(booster_wait.group(1))
            account['booster_job']['status'] = 'waiting_boost'
            account['booster_job']['last_progress_update'] = time.time()

        booster_status = re.search(r'BOOSTER_STATUS:([^:]+):(.+)', msg)
        if booster_status and 'booster_job' in account:
            account['booster_job']['status'] = booster_status.group(1)
            account['booster_job']['status_text'] = booster_status.group(2)
            account['booster_job']['last_progress_update'] = time.time()

        booster_alert = re.search(r'BOOSTER_ALERT:(.+)', msg)
        if booster_alert and 'booster_job' in account:
            account['booster_job']['alert'] = True
            account['booster_job']['alert_text'] = booster_alert.group(1)
            account['booster_job']['active'] = False
            account['booster_job']['done'] = False

        booster_done = re.search(r'BOOSTER_DONE:(.+)', msg)
        if booster_done and 'booster_job' in account:
            account['booster_job']['active'] = False
            account['booster_job']['done'] = True
            account['booster_job']['status'] = 'done'
            account['booster_job']['status_text'] = booster_done.group(1)
        
        # Check if log contains a trade link
        trade_url_match = re.search(r'https://case-clicker\.com/trading/([a-f0-9]+)', msg)
        if trade_url_match:
            trade_id = trade_url_match.group(1)
            trade_url = f"https://case-clicker.com/trading/{trade_id}"
            account['active_trade'] = trade_url

            # AUTOMATION: Extract Trade ID and send command to Main
            try:
                settings = load_settings()
                main_id = settings.get('main_id')

                # If we have a Main ID, and it's NOT main sending the link (avoid loop)
                if main_id and main_id != acc_id:
                    main_acc = next((a for a in db if a['id'] == main_id), None)

                    if main_acc:
                        print(f"[AUTO] Sending Main ({main_id}) to trade {trade_id} from {acc_id}")

                        # Create command that Main Worker v1.0 expects
                        cmd = {
                            "type": "join_and_confirm",
                            "tradeId": trade_id
                        }

                        if 'commands' not in main_acc: main_acc['commands'] = []
                        main_acc['commands'].append(cmd)
                        main_acc['status_log'] = f"Received trade command: {trade_id}"

            except Exception as e:
                print(f"[AUTO ERROR] Could not parse trade link: {e}")

        save_db(db)
    return jsonify({"success": True})

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    db = load_db()
    current_time = time.time()
    changed = False
    settings = load_settings()
    main_id = settings.get('main_id') or ''

    # Check for offline accounts and queue refresh/resume command
    # "Offline for refresh" = no heartbeat in 20+ sec (we do NOT use vault_stale for refresh,
    # otherwise accounts that send heartbeats but haven't collected vault get refresh spammed)
    OFFLINE_HEARTBEAT_SEC = 20
    REFRESH_THROTTLE_SEC = 90

    for account in db:
        last_seen = account.get('last_seen', 0)
        time_since_seen = current_time - last_seen
        heartbeat_stale = time_since_seen > OFFLINE_HEARTBEAT_SEC and last_seen > 0

        stats = account.get('stats', {}) or {}
        vault_ts = parse_timestamp(stats.get('vaultLastCollected'))
        vault_stale = vault_ts > 0 and (current_time - vault_ts) > 180
        is_offline = heartbeat_stale or vault_stale

        if is_offline:
            # Check if convert job is active
            convert_job = account.get('convert_job', {})
            is_convert_active = convert_job.get('active', False)
            booster_job = account.get('booster_job', {})
            is_booster_active = booster_job.get('active', False)
            
            if is_booster_active:
                last_progress_update = booster_job.get('last_progress_update', 0)
                if current_time - last_progress_update > 45:
                    if 'commands' not in account:
                        account['commands'] = []
                    has_resume = any(cmd.get('type') == 'resume_booster' for cmd in account['commands'])
                    if not has_resume:
                        account['commands'].append({
                            "type": "resume_booster",
                            "caseId": booster_job.get('caseId'),
                            "caseName": booster_job.get('caseName'),
                            "caseType": booster_job.get('caseType', 'case'),
                            "casePrice": booster_job.get('casePrice', 0),
                            "clickUntilBoost": booster_job.get('clickUntilBoost', False),
                            "createdAtTs": booster_job.get('createdAtTs', 0),
                            "cycle": booster_job.get('cycle', 0)
                        })
                        changed = True
                        print(f"[AUTO-RESUME] Queued booster resume for: {account.get('username', account.get('id'))}")
            elif is_convert_active:
                # If convert job is active but stuck (no progress updates), resume it
                last_progress_update = convert_job.get('last_progress_update', 0)
                if current_time - last_progress_update > 30:  # No progress for 30 seconds
                    # Resume convert job (only opening, not buying)
                    if 'commands' not in account:
                        account['commands'] = []
                    
                    has_resume = any(cmd.get('type') == 'resume_convert' for cmd in account['commands'])
                    if not has_resume:
                        # Calculate remaining cases to open
                        total_cases = convert_job.get('total', 0)
                        # We'll let the script check current inventory and continue
                        account['commands'].append({
                            "type": "resume_convert",
                            "caseId": convert_job.get('caseId'),
                            "caseType": convert_job.get('caseType', 'case'),
                            "sellMethod": convert_job.get('sellMethod', 'tokens'),
                            "totalCases": convert_job.get('total', 0)  # Pass total for comparison
                        })
                        changed = True
                        print(f"[AUTO-RESUME] Queued resume convert for: {account.get('username', account.get('id'))}")
            else:
                # Normal refresh: only when heartbeat is stale (no heartbeat 20+ sec), NOT when only vault_stale
                if not heartbeat_stale:
                    continue  # Account is sending heartbeats; do not queue refresh
                if account.get('id') == main_id:
                    continue  # Never auto-refresh the main account
                convert_job = account.get('convert_job', {})
                booster_job = account.get('booster_job', {})
                if not convert_job.get('done', False) and not booster_job.get('done', False):  # Don't refresh if job was just completed
                    last_refresh = account.get('last_refresh_attempt', 0)
                    if current_time - last_refresh > REFRESH_THROTTLE_SEC:  # Throttle: at most one refresh per 90 sec per account
                        account['last_refresh_attempt'] = current_time
                        changed = True
                        if 'commands' not in account:
                            account['commands'] = []
                        
                        # Check if refresh command already queued
                        has_refresh = any(cmd.get('type') == 'refresh_page' for cmd in account['commands'])
                        if not has_refresh:
                            account['commands'].append({"type": "refresh_page"})
                            changed = True
                            print(f"[AUTO-REFRESH] Queued refresh for offline account: {account.get('username', account.get('id'))}")
    
    if changed:
        save_db(db)
    return jsonify(db)

@app.route('/api/reorder', methods=['POST'])
def reorder_accounts():
    new_order_ids = request.json.get('ids', [])
    db = load_db()
    acc_map = {acc['id']: acc for acc in db}
    new_db = []
    for acc_id in new_order_ids:
        if acc_id in acc_map:
            new_db.append(acc_map[acc_id])
            del acc_map[acc_id]
    for remaining_acc in acc_map.values():
        new_db.append(remaining_acc)
    save_db(new_db)
    return jsonify({"success": True})

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        with open(SETTINGS_FILE, 'w') as f: json.dump(request.json, f)
        return jsonify({"success": True})
    return jsonify(load_settings())

@app.route('/api/queue/transfer', methods=['POST'])
def queue_transfer():
    data = request.json
    sender_id = data.get('sender_id')
    send_tokens = data.get('send_tokens', False)
    send_skins = data.get('send_skins', False)
    token_amount = data.get('token_amount')
    try:
        token_amount = int(token_amount) if token_amount is not None else None
        if token_amount is not None and token_amount < 0:
            token_amount = 0
    except:
        token_amount = None
    
    db = load_db()
    acc = next((a for a in db if a['id'] == sender_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    
    acc['status_log'] = "Initiating transfer..."
    acc['active_trade'] = None
    
    cmd = { 
        "type": "start_trade_socket",
        "sendTokens": send_tokens,
        "sendSkins": send_skins,
        "tokenAmount": token_amount
    }
    
    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append(cmd)
    
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/queue/scan_case_summary', methods=['POST'])
def queue_scan_case_summary():
    data = request.json
    acc_id = data.get('acc_id')
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    if 'commands' not in acc: acc['commands'] = []
    has_cmd = any(c.get('type') == 'scan_case_summary' for c in acc['commands'])
    if not has_cmd:
        acc['commands'].append({"type": "scan_case_summary"})
        save_db(db)
    return jsonify({"success": True})

@app.route('/api/queue/sell_cases', methods=['POST'])
def queue_sell_cases():
    data = request.json
    acc_id = data.get('acc_id')
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    acc['status_log'] = "Starting case sell snapshot..."
    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append({"type": "sell_cases_snapshot"})
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/case_summary', methods=['POST'])
def update_case_summary():
    data = request.json
    acc_id = data.get('id')
    summary = data.get('summary') or {}
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"}), 404
    summary['updated_at'] = time.time()
    acc['case_summary'] = summary
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/queue/scan_inventory', methods=['POST'])
def queue_scan_inventory():
    data = request.json
    acc_id = data.get('acc_id')
    
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    
    acc['status_log'] = "Scanning inventory..."
    
    cmd = {"type": "scan_inventory"}
    
    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append(cmd)
    
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/cases', methods=['GET'])
def get_cases():
    return jsonify(load_cases())

@app.route('/api/job/dismiss', methods=['POST'])
def dismiss_job():
    data = request.json
    account_id = data.get('accountId')
    job_type = data.get('jobType', 'convert')
    
    db = load_db()
    account = next((a for a in db if a['id'] == account_id), None)
    if account:
        key = 'booster_job' if job_type == 'booster' else 'convert_job'
        if key in account:
            del account[key]
            save_db(db)
            return jsonify({"success": True})
    return jsonify({"success": False}), 404

@app.route('/api/queue/booster', methods=['POST'])
def queue_booster():
    data = request.json
    acc_id = data.get('acc_id')
    case_id = data.get('case_id')
    click_until_boost = bool(data.get('click_until_boost', False))

    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})

    cases = load_cases()
    case_data = next((c for c in cases if c['_id'] == case_id), None)
    if not case_data: return jsonify({"success": False, "msg": "Case not found"})

    profile = acc.get('profile', {})
    created_at_ts = parse_timestamp(profile.get('createdAt'))

    acc['status_log'] = f"Starting booster: {case_data['name']}..."
    acc['booster_job'] = {
        "active": True,
        "done": False,
        "alert": False,
        "caseId": case_id,
        "caseName": case_data['name'],
        "caseType": case_data.get('type', 'case'),
        "casePrice": case_data['price'],
        "clickUntilBoost": click_until_boost,
        "createdAtTs": created_at_ts,
        "rankName": profile.get('rankName', ''),
        "rankImage": profile.get('rankImage', ''),
        "cycle": 0,
        "opened": 0,
        "total": 0,
        "remaining": 0,
        "progress": 0,
        "batchSize": 0,
        "multiplier": 0,
        "waiting_seconds": 0,
        "status": "running",
        "status_text": "",
        "last_progress_update": time.time()
    }

    cmd = {
        "type": "start_booster",
        "caseId": case_id,
        "caseName": case_data['name'],
        "caseType": case_data.get('type', 'case'),
        "casePrice": case_data['price'],
        "clickUntilBoost": click_until_boost,
        "createdAtTs": created_at_ts
    }
    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append(cmd)
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/queue/convert', methods=['POST'])
def queue_convert():
    data = request.json
    acc_id = data.get('acc_id')
    case_id = data.get('case_id')
    budget = data.get('budget')
    sell_method = data.get('sell_method')  # 'money' or 'tokens'
    
    db = load_db()
    acc = next((a for a in db if a['id'] == acc_id), None)
    if not acc: return jsonify({"success": False, "msg": "Account not found"})
    
    cases = load_cases()
    case_data = next((c for c in cases if c['_id'] == case_id), None)
    if not case_data: return jsonify({"success": False, "msg": "Case not found"})
    
    acc['status_log'] = f"Starting convert: {case_data['name']}..."
    
    # Initialize convert job tracking (casePrice for ROI spent; tokens_start/money_start set when worker reports CONVERT_START)
    acc['convert_job'] = {
        "active": True,
        "caseId": case_id,
        "caseName": case_data['name'],
        "caseType": case_data.get('type', 'case'),
        "sellMethod": sell_method,
        "casePrice": float(case_data.get('price', 0)),
        "opened": 0,
        "total": 0,
        "remaining": 0,
        "progress": 0,
        "batchSize": 0,
        "multiplier": 0,
        "last_progress_update": time.time()
    }
    
    # Update last used timestamp for this case
    all_cases = load_cases()
    for c in all_cases:
        if c['_id'] == case_id:
            c['last_used'] = time.time()
            break
    with open(CASES_FILE, 'w') as f:
        json.dump(all_cases, f, indent=4)
    
    cmd = {
        "type": "convert_cases",
        "caseId": case_id,
        "caseName": case_data['name'],
        "casePrice": case_data['price'],
        "caseType": case_data.get('type', 'case'),
        "budget": budget,
        "sellMethod": sell_method
    }
    
    if 'commands' not in acc: acc['commands'] = []
    acc['commands'].append(cmd)
    
    save_db(db)
    return jsonify({"success": True})

@app.route('/api/delete', methods=['POST'])
def delete_acc():
    target_id = request.json.get('id')
    db = load_db()
    db = [a for a in db if a['id'] != target_id]
    save_db(db)
    return jsonify({"success": True})

HTML_UI = """
<!DOCTYPE html>
<html>
<head>
    <title>Case Clicker Hub __CCHUB_VERSION__</title>
    <script>
      window.__CC_TOKEN__ = "__CCHUB_TOKEN__";
      window.__CC_VERSION__ = "__CCHUB_VERSION__";
      (function() {
        const origFetch = window.fetch.bind(window);
        window.fetch = function(input, init) {
          init = init || {};
          const headers = new Headers(init.headers || {});
          let url = typeof input === "string" ? input : (input && input.url) || "";
          if (url.startsWith("/api/")) {
            headers.set("X-Alt-Token", window.__CC_TOKEN__);
          }
          init.headers = headers;
          return origFetch(input, init);
        };
      })();
    </script>
    <style>
      html, body { height: 100% !important; margin: 0 !important; }
      body { flex-direction: column !important; }
      /* Outer shell — root contains resize grips + app chrome */
      .cc-shell {
        position: relative; flex: 1 1 auto; display: flex; flex-direction: column;
        min-height: 0;
      }
      /* Native drag via pywebview attribute (data-pywebview-drag-region) */
      .cc-titlebar {
        display: flex; align-items: stretch; height: 32px;
        background: #000; border-bottom: 1px solid #27272a;
        color: #a1a1aa; font-family: 'Inter', sans-serif;
        font-size: 12px; font-weight: 600; user-select: none;
        flex: 0 0 auto; z-index: 50;
      }
      .cc-titlebar-title {
        display: flex; align-items: center; padding: 0 14px; letter-spacing: 0.2px;
        flex: 0 0 auto;
      }
      .cc-titlebar-title b { color: #10b981; margin-right: 6px; }
      .cc-titlebar-spacer { flex: 1 1 auto; min-width: 0; }
      .cc-titlebar-buttons {
        display: flex; flex: 0 0 auto;
      }
      .cc-titlebar-btn {
        width: 46px; height: 32px; border: 0; background: transparent;
        color: #a1a1aa; font-family: 'Segoe UI Symbol', 'Segoe MDL2 Assets', sans-serif;
        font-size: 13px; cursor: pointer; display: flex;
        align-items: center; justify-content: center; transition: background .12s, color .12s;
      }
      .cc-titlebar-btn:hover { background: #1f1f22; color: #fff; }
      .cc-titlebar-btn.cc-close:hover { background: #e81123; color: #fff; }
      .cc-app-body {
        flex: 1 1 auto; display: flex; min-height: 0; overflow: hidden;
      }

      /* Edge resize grips — thin invisible zones around the shell.
         Use pointer-events + setPointerCapture from JS. */
      .cc-grip { position: absolute; z-index: 60; background: transparent; }
      .cc-grip-n { top: 0; left: 6px; right: 6px; height: 4px; cursor: n-resize; }
      .cc-grip-s { bottom: 0; left: 6px; right: 6px; height: 4px; cursor: s-resize; }
      .cc-grip-w { top: 6px; bottom: 6px; left: 0; width: 4px; cursor: w-resize; }
      .cc-grip-e { top: 6px; bottom: 6px; right: 0; width: 4px; cursor: e-resize; }
      .cc-grip-nw { top: 0; left: 0; width: 8px; height: 8px; cursor: nw-resize; }
      .cc-grip-ne { top: 0; right: 0; width: 8px; height: 8px; cursor: ne-resize; }
      .cc-grip-sw { bottom: 0; left: 0; width: 8px; height: 8px; cursor: sw-resize; }
      .cc-grip-se { bottom: 0; right: 0; width: 8px; height: 8px; cursor: se-resize; }
    </style>
    <script>
      function cchubMinimize() { window.pywebview && window.pywebview.api.minimize(); }
      function cchubToggleMax() { window.pywebview && window.pywebview.api.toggle_maximize(); }
      function cchubClose() { window.pywebview && window.pywebview.api.hide_to_tray(); }

      // Titlebar drag fallback — the `data-pywebview-drag-region` attribute is
      // honored by modern pywebview on Windows; if that fires the OS-native drag,
      // DOM mousemove stops firing and this fallback stays idle. If the attribute
      // is ignored, our own listener moves the window manually.
      (function installDragFallback() {
        window.addEventListener('DOMContentLoaded', () => {
          const bar = document.querySelector('.cc-titlebar');
          if (!bar) return;
          const DRAG_THRESHOLD = 3;
          let tracking = false;
          let armed = false;
          let startScreenX = 0, startScreenY = 0;
          let startWinX = 0, startWinY = 0;
          let lastSent = 0;

          bar.addEventListener('mousedown', async (e) => {
            if (e.button !== 0) return;
            if (e.target.closest('[data-no-drag]')) return;
            if (!window.pywebview || !window.pywebview.api) return;
            try {
              const rect = await window.pywebview.api.get_rect();
              if (!rect) return;
              startWinX = rect.x; startWinY = rect.y;
              startScreenX = e.screenX; startScreenY = e.screenY;
              armed = true; tracking = false;
            } catch (_) {}
          });
          bar.addEventListener('dblclick', (e) => {
            if (e.target.closest('[data-no-drag]')) return;
            cchubToggleMax();
          });
          bar.addEventListener('mousemove', (e) => {
            if (!armed) return;
            const dx = e.screenX - startScreenX;
            const dy = e.screenY - startScreenY;
            if (!tracking) {
              if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
              tracking = true;
            }
            const now = performance.now();
            if (now - lastSent < 12) return;
            lastSent = now;
            window.pywebview.api.move_window(startWinX + dx, startWinY + dy);
          });
          const endDrag = () => { armed = false; tracking = false; };
          bar.addEventListener('mouseup', endDrag);
          bar.addEventListener('mouseleave', endDrag);
          window.addEventListener('blur', endDrag);
        });
      })();

      // Edge + corner resize — pointer capture keeps events alive even when the
      // cursor leaves the shrinking window.
      (function installResizeGrips() {
        window.addEventListener('DOMContentLoaded', () => {
          const MIN_W = 780, MIN_H = 520;
          const grips = [
            { sel: '.cc-grip-n',  edges: ['n'] },
            { sel: '.cc-grip-s',  edges: ['s'] },
            { sel: '.cc-grip-w',  edges: ['w'] },
            { sel: '.cc-grip-e',  edges: ['e'] },
            { sel: '.cc-grip-nw', edges: ['n','w'] },
            { sel: '.cc-grip-ne', edges: ['n','e'] },
            { sel: '.cc-grip-sw', edges: ['s','w'] },
            { sel: '.cc-grip-se', edges: ['s','e'] },
          ];
          grips.forEach(({ sel, edges }) => {
            const el = document.querySelector(sel);
            if (!el) return;
            el.addEventListener('pointerdown', async (e) => {
              if (e.button !== 0) return;
              if (!window.pywebview || !window.pywebview.api) return;
              const rect = await window.pywebview.api.get_rect();
              if (!rect) return;
              const start = {
                sx: e.screenX, sy: e.screenY,
                x: rect.x, y: rect.y, w: rect.w, h: rect.h,
              };
              let raf = 0, pending = null;
              const flush = () => {
                raf = 0;
                if (!pending) return;
                const p = pending; pending = null;
                if (edges.includes('n') || edges.includes('w')) {
                  window.pywebview.api.move_and_resize(p.x, p.y, p.w, p.h);
                } else {
                  window.pywebview.api.resize_window(p.w, p.h);
                }
              };
              const onMove = (ev) => {
                const dx = ev.screenX - start.sx;
                const dy = ev.screenY - start.sy;
                let x = start.x, y = start.y, w = start.w, h = start.h;
                if (edges.includes('e')) w = Math.max(MIN_W, start.w + dx);
                if (edges.includes('s')) h = Math.max(MIN_H, start.h + dy);
                if (edges.includes('w')) {
                  const newW = Math.max(MIN_W, start.w - dx);
                  x = start.x + (start.w - newW);
                  w = newW;
                }
                if (edges.includes('n')) {
                  const newH = Math.max(MIN_H, start.h - dy);
                  y = start.y + (start.h - newH);
                  h = newH;
                }
                pending = { x, y, w, h };
                if (!raf) raf = requestAnimationFrame(flush);
              };
              const onUp = () => {
                el.removeEventListener('pointermove', onMove);
                el.removeEventListener('pointerup', onUp);
                el.removeEventListener('pointercancel', onUp);
                try { el.releasePointerCapture(e.pointerId); } catch(_) {}
                if (raf) { cancelAnimationFrame(raf); raf = 0; }
                if (pending) flush();
              };
              try { el.setPointerCapture(e.pointerId); } catch(_) {}
              el.addEventListener('pointermove', onMove);
              el.addEventListener('pointerup', onUp);
              el.addEventListener('pointercancel', onUp);
              e.preventDefault();
            });
          });
        });
      })();
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #09090b;
            --bg-panel: #101013;
            --bg-elev: #15151a;
            --bg-hover: #1c1c22;
            --border: #24242a;
            --border-soft: #1d1d23;
            --primary: #f43f5e;
            --accent: #10b981;
            --accent-soft: rgba(16, 185, 129, 0.12);
            --warn: #eab308;
            --info: #60a5fa;
            --danger: #ef4444;
            --text-main: #e4e4e7;
            --text-muted: #a1a1aa;
            --text-dim: #71717a;
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.4);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.35);
            --shadow-lg: 0 10px 40px rgba(0,0,0,0.5);
        }

        * { box-sizing: border-box; }
        body { background: var(--bg-dark); color: var(--text-main); font-family: 'Inter', sans-serif; margin: 0; display: flex; height: 100vh; overflow: hidden; font-feature-settings: 'cv11','ss01'; }
        button { font-family: inherit; }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2a30; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #3a3a42; }

        #nprogress .bar { background: var(--accent); position: fixed; z-index: 9999; top: 0; left: 0; width: 0%; height: 2px; transition: width 0.2s ease; box-shadow: 0 0 10px var(--accent); }

        /* ---------- SIDEBAR ---------- */
        .sidebar { width: 320px; flex: 0 0 320px; background: #07070a; border-right: 1px solid var(--border-soft); display: flex; flex-direction: column; padding: 18px 0 0; z-index: 10; user-select: none; transition: flex-basis .15s ease, width .15s ease; }
        @media (max-width: 1120px) {
            .sidebar { width: 280px; flex-basis: 280px; }
        }
        @media (max-width: 900px) {
            .sidebar { width: 240px; flex-basis: 240px; }
            .brand { padding: 0 14px 14px 14px; font-size: 14px; }
            .settings-area { padding: 14px; }
        }

        .brand { padding: 0 20px 16px 20px; font-weight: 800; font-size: 15px; letter-spacing: -0.3px; color: #fff; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border-soft); margin-bottom: 10px; }
        .brand-title { cursor: pointer; display: flex; align-items: center; gap: 8px; }
        .brand-title .logo { display: inline-flex; width: 22px; height: 22px; border-radius: 6px; background: linear-gradient(135deg, var(--accent) 0%, #059669 100%); align-items: center; justify-content: center; color: #04110a; font-weight: 900; font-size: 12px; box-shadow: 0 4px 10px rgba(16,185,129,0.25); }
        .edit-btn { background: transparent; border: 1px solid var(--border); color: var(--text-muted); font-size: 10px; padding: 5px 10px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 700; transition: 0.15s; letter-spacing: 0.3px; }
        .edit-btn:hover { color: #fff; border-color: #3a3a42; background: var(--bg-elev); }
        .edit-btn.active { background: var(--accent); color: #04110a; border-color: var(--accent); }

        /* Search + filter row */
        .sidebar-controls { padding: 0 16px 10px; display: flex; gap: 6px; }
        .sidebar-search { flex: 1; position: relative; }
        .sidebar-search input { width: 100%; background: var(--bg-elev); border: 1px solid var(--border-soft); color: #fff; padding: 8px 10px 8px 30px; border-radius: var(--radius-sm); font-size: 12px; outline: none; transition: border-color 0.15s; }
        .sidebar-search input:focus { border-color: #3a3a42; }
        .sidebar-search::before { content: ''; position: absolute; top: 50%; left: 10px; transform: translateY(-50%); width: 12px; height: 12px; background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2371717a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E") no-repeat center/contain; pointer-events: none; }
        .sidebar-filter { background: var(--bg-elev); border: 1px solid var(--border-soft); color: var(--text-muted); padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 11px; font-weight: 700; transition: 0.15s; }
        .sidebar-filter:hover { color: #fff; border-color: #3a3a42; }
        .sidebar-filter.active { background: var(--accent-soft); color: var(--accent); border-color: rgba(16,185,129,0.35); }

        /* Bulk-action toolbar (visible only in edit mode with selection) */
        .bulk-bar { margin: 0 12px 10px; padding: 10px 12px; background: linear-gradient(135deg, rgba(16,185,129,0.06), rgba(16,185,129,0.02)); border: 1px solid rgba(16,185,129,0.25); border-radius: var(--radius-md); display: none; align-items: center; justify-content: space-between; gap: 8px; }
        .bulk-bar.show { display: flex; animation: fadein 0.2s; }
        .bulk-bar-count { font-size: 11px; font-weight: 700; color: var(--accent); letter-spacing: 0.3px; }
        .bulk-bar-actions { display: flex; gap: 6px; }
        .bulk-btn { background: transparent; border: 1px solid var(--border); color: var(--text-muted); padding: 5px 9px; border-radius: var(--radius-sm); cursor: pointer; font-size: 10px; font-weight: 700; letter-spacing: 0.3px; transition: 0.15s; }
        .bulk-btn:hover { color: #fff; border-color: #3a3a42; background: var(--bg-elev); }
        .bulk-btn.danger:hover { color: var(--danger); border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.08); }
        .bulk-btn.primary { background: var(--accent); color: #04110a; border-color: var(--accent); }
        .bulk-btn.primary:hover { background: #22d39a; color: #04110a; border-color: #22d39a; }

        .edit-hint { margin: 0 12px 8px; padding: 8px 10px; font-size: 10px; color: var(--text-dim); background: var(--bg-elev); border: 1px dashed var(--border); border-radius: var(--radius-sm); line-height: 1.4; display: none; }
        .edit-hint.show { display: block; }

        .acc-list { flex: 1; overflow-y: auto; padding: 0 10px; }

        /* ---------- LIST ITEM ---------- */
        .acc-item { padding: 10px 12px; margin-bottom: 4px; border-radius: var(--radius-md); cursor: pointer; display: flex; gap: 11px; transition: background 0.12s, border-color 0.12s; border: 1px solid transparent; background: transparent; position: relative; align-items: center; }
        .acc-item:hover { background: var(--bg-elev); }
        .acc-item.active { background: var(--bg-elev); border-color: rgba(16,185,129,0.45); box-shadow: inset 2px 0 0 var(--accent); }
        .acc-item.draggable { cursor: grab; }
        .acc-item.draggable:active { cursor: grabbing; }
        .acc-item.dragging { opacity: 0.5; border: 1px dashed #555; }
        .acc-item.selected { background: var(--accent-soft); border-color: rgba(16,185,129,0.35); }
        .acc-item.hidden-filtered { display: none; }

        .main-separator { height: 1px; background: var(--border-soft); margin: 8px 12px; }
        .acc-item.pinned { box-shadow: inset 2px 0 0 var(--warn); }
        .acc-item.pinned.active { box-shadow: inset 2px 0 0 var(--warn), inset 0 0 0 1px rgba(16,185,129,0.45); }

        .acc-check { width: 18px; height: 18px; flex-shrink: 0; display: none; align-items: center; justify-content: center; border: 1.5px solid var(--border); border-radius: 4px; background: var(--bg-elev); transition: 0.15s; }
        .acc-check.show { display: flex; }
        .acc-check.checked { background: var(--accent); border-color: var(--accent); }
        .acc-check.checked::after { content: '✓'; color: #04110a; font-size: 11px; font-weight: 900; line-height: 1; }

        .acc-drag-handle { display: none; width: 14px; flex-shrink: 0; color: var(--text-dim); font-size: 14px; cursor: grab; line-height: 1; user-select: none; }
        .acc-drag-handle.show { display: flex; align-items: center; }

        .acc-avatar-wrap { position: relative; flex-shrink: 0; }
        .acc-avatar { width: 38px; height: 38px; border-radius: var(--radius-md); background: #222; object-fit: cover; display: block; border: 1px solid var(--border-soft); }
        .avatar-status { position: absolute; bottom: -2px; right: -2px; width: 11px; height: 11px; border-radius: 50%; border: 2px solid #07070a; background: var(--danger); }
        .avatar-status.online { background: var(--accent); box-shadow: 0 0 6px var(--accent); }

        .acc-info { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
        .acc-name-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
        .acc-name { font-weight: 600; font-size: 13px; color: #fafafa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.2px; }
        .main-tag { background: var(--warn); color: #111; font-size: 8px; padding: 2px 5px; border-radius: 3px; font-weight: 900; line-height: 1; letter-spacing: 0.4px; flex-shrink: 0; }

        .badge-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
        .pro-tag { background: var(--warn); color: #111; font-size: 8px; padding: 2px 4px; border-radius: 3px; font-weight: 900; line-height: 1; display: inline-block; letter-spacing: 0.3px; }
        .prem-tag { transform: skew(-10deg); padding: 2px 5px; display: inline-flex; align-items: center; gap: 4px; border-radius: 2px; }
        .prem-stripes { display: flex; gap: 2px; }
        .prem-stripe { width: 2px; height: 9px; background: currentColor; }
        .prem-val { font-family: 'Inter', sans-serif; font-weight: 800; font-size: 9px; line-height: 1; transform: skew(0deg); color: inherit; }

        .acc-meta { font-size: 10px; color: var(--text-dim); font-family: 'JetBrains Mono'; font-weight: 500; display: flex; align-items: center; gap: 8px; margin-top: 1px; }
        .acc-meta span { white-space: nowrap; }
        .acc-meta .mm { color: var(--accent); }
        .acc-meta .mt { color: var(--warn); }

        .acc-quick-delete { display: none; background: transparent; border: none; color: var(--text-dim); cursor: pointer; padding: 4px; border-radius: 4px; font-size: 14px; line-height: 1; transition: 0.15s; flex-shrink: 0; }
        .acc-quick-delete.show { display: inline-flex; align-items: center; justify-content: center; }
        .acc-quick-delete:hover { color: var(--danger); background: rgba(239,68,68,0.08); }

        .acc-empty { padding: 40px 20px; text-align: center; color: var(--text-dim); font-size: 12px; }

        /* ---------- SETTINGS AREA ---------- */
        .settings-area { padding: 16px 18px; border-top: 1px solid var(--border-soft); background: #05050799; }
        .settings-label { font-size: 9px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; display: flex; align-items: center; gap: 5px; }
        .settings-label + input { margin-bottom: 10px; }
        .settings-input { background: var(--bg-elev); border: 1px solid var(--border-soft); color: #fff; padding: 9px 11px; border-radius: var(--radius-sm); width: 100%; font-size: 11px; font-family: 'JetBrains Mono'; outline: none; transition: border-color 0.15s; }
        .settings-input:focus { border-color: #3a3a42; }
        .hint-text { font-size: 9px; color: var(--text-dim); margin-top: -6px; margin-bottom: 10px; min-height: 11px; line-height: 1.3; }
        .save-btn { width: 100%; background: #fafafa; color: #000; border: none; padding: 10px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 800; font-size: 10px; transition: background 0.15s; letter-spacing: 0.6px; }
        .save-btn:hover { background: #fff; }
        .copy-link-btn { width: 100%; background: transparent; color: var(--text-muted); border: 1px solid var(--border); padding: 9px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 700; font-size: 10px; letter-spacing: 0.6px; margin-top: 6px; transition: 0.15s; }
        .copy-link-btn:hover { background: var(--bg-elev); color: #fafafa; border-color: #3a3a42; }
        .copy-link-btn.copied { background: var(--accent); color: #04110a; border-color: var(--accent); }

        /* ---------- MAIN CONTENT AREA ---------- */
        .main { flex: 1; display: flex; flex-direction: column; background: var(--bg-dark); position: relative; overflow: hidden; }
        
        /* ---------- GLOBAL HEADER ---------- */
        .global-header {
            flex: 0 0 auto;
            min-height: 56px;
            background: rgba(7,7,10,0.85);
            border-bottom: 1px solid var(--border-soft);
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 8px 20px;
            backdrop-filter: blur(14px);
            position: relative;
            z-index: 20;
        }
        .gh-stats {
            display: flex;
            flex: 1 1 auto;
            min-width: 0;
            gap: 10px;
            align-items: center;
            overflow: hidden;
        }
        .gh-item {
            flex: 1; min-width: 0;
            display: flex; flex-direction: column;
            padding: 7px 14px;
            background: var(--bg-panel);
            border: 1px solid var(--border-soft);
            border-radius: var(--radius-md);
            gap: 2px;
            transition: border-color 0.15s;
        }
        .gh-item:hover { border-color: var(--border); }
        .gh-label {
            font-size: 9px; color: var(--text-dim); font-weight: 700;
            text-transform: uppercase; letter-spacing: 0.6px;
            white-space: nowrap;
        }
        .gh-val {
            font-size: 14px; color: #fff; font-family: 'JetBrains Mono';
            font-weight: 600; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; max-width: 100%;
        }
        .gh-val.tok { color: var(--warn); }
        .gh-val.mon { color: var(--accent); }

        .active-jobs-wrap { position: relative; flex: 0 0 auto; }
        .active-jobs-btn {
            background: var(--bg-panel);
            color: #fff;
            border: 1px solid var(--border);
            padding: 9px 14px;
            border-radius: var(--radius-md);
            font-weight: 600;
            font-size: 11px;
            cursor: pointer;
            transition: 0.15s;
            display: flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
            letter-spacing: 0.2px;
        }
        .active-jobs-btn:hover { background: var(--bg-elev); border-color: #3a3a42; }
        .active-jobs-btn.has-active { border-color: rgba(16,185,129,0.4); color: var(--accent); }
        .active-jobs-btn.has-alert { border-color: rgba(239,68,68,0.4); color: var(--danger); }
        .active-jobs-btn .badge {
            background: var(--bg-elev);
            border-radius: 10px;
            padding: 2px 7px;
            font-size: 10px;
            min-width: 18px;
            text-align: center;
            font-weight: 700;
        }
        .active-jobs-btn.has-active .badge { background: var(--accent); color: #04110a; }
        .active-jobs-btn.has-alert #activeJobsAlertBadge { background: var(--danger); color: #fff; }
        .active-jobs-btn .badge:empty,
        .active-jobs-btn .badge[data-zero="1"] { display: none; }
        .active-jobs-btn-label { display: inline; }

        @media (max-width: 1120px) {
            .gh-stats { gap: 6px; }
            .gh-val { font-size: 12px; }
            .gh-item { padding: 6px 10px; }
        }
        @media (max-width: 960px) {
            .global-header { padding: 6px 14px; gap: 8px; }
            .gh-label { display: none; }
            .gh-item::before {
                content: attr(data-short);
                font-size: 9px; color: var(--text-dim); font-weight: 800;
                text-transform: uppercase; margin-right: 4px;
            }
            .gh-item { flex-direction: row; align-items: baseline; gap: 5px; padding: 6px 9px; }
            .active-jobs-btn-label { display: none; }
            .active-jobs-btn { padding: 8px 10px; }
        }

        /* ---------- ACTIVE JOBS DROPDOWN ---------- */
        .active-jobs-dropdown {
            position: absolute;
            top: calc(100% + 8px);
            right: 0;
            width: min(520px, calc(100vw - 40px));
            max-height: calc(100vh - 140px);
            background: var(--bg-panel);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-lg);
            z-index: 10000;
            display: none;
            flex-direction: column;
            overflow: hidden;
        }
        .active-jobs-dropdown.show { display: flex; animation: fadein 0.15s; }
        .active-jobs-header {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border-soft);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .active-jobs-header h3 {
            margin: 0;
            font-size: 13px;
            font-weight: 700;
            color: #fff;
            letter-spacing: 0.2px;
        }
        .active-jobs-list { overflow-y: auto; max-height: 520px; }
        .active-job-item { padding: 14px 18px; border-bottom: 1px solid var(--border-soft); transition: 0.15s; }
        .active-job-item:last-child { border-bottom: none; }
        .active-job-item:hover { background: var(--bg-elev); }
        .active-job-item.done { opacity: 0.75; }
        .active-job-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .active-job-user { font-weight: 700; color: #fff; font-size: 13px; }
        .active-job-type { font-size: 10px; color: var(--accent); font-weight: 600; letter-spacing: 0.3px; margin-top: 2px; }
        .active-job-close { background: transparent; border: none; color: var(--text-dim); cursor: pointer; font-size: 16px; padding: 0; width: 22px; height: 22px; display: flex; align-items: center; justify-content: center; border-radius: 4px; transition: 0.15s; }
        .active-job-close:hover { color: #fff; background: var(--bg-elev); }
        .active-job-progress { margin-top: 8px; }
        .active-job-progress-bar { background: var(--bg-dark); border-radius: var(--radius-sm); padding: 2px; height: 22px; overflow: hidden; position: relative; }
        .active-job-progress-fill { background: linear-gradient(90deg, var(--accent) 0%, #34d399 100%); height: 100%; border-radius: 4px; transition: width 0.3s ease; display: flex; align-items: center; justify-content: center; color: #04110a; font-weight: 800; font-size: 10px; }
        .active-job-stats { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); font-family: 'JetBrains Mono'; margin-top: 6px; flex-wrap: wrap; gap: 8px; }

        /* ---------- TOP BAR (account detail) ---------- */
        .top-bar { min-height: 64px; border-bottom: 1px solid var(--border-soft); display: flex; align-items: center; justify-content: space-between; padding: 10px 24px; background: rgba(9,9,11,0.55); backdrop-filter: blur(20px); z-index: 10; gap: 16px; flex-wrap: wrap; }
        .user-head { display: flex; align-items: center; gap: 13px; min-width: 0; }
        .uh-avatar { width: 42px; height: 42px; border-radius: var(--radius-md); border: 1px solid var(--border-soft); object-fit: cover; flex-shrink: 0; }
        .uh-meta { min-width: 0; }
        .uh-meta h1 { font-size: 15px; font-weight: 700; color: #fff; margin: 0; display: flex; align-items: center; gap: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.3px; }
        .uh-meta p { font-size: 10px; color: var(--text-dim); margin: 3px 0 0 0; font-family: 'JetBrains Mono'; }

        .tabs { display: flex; gap: 4px; height: 100%; flex-wrap: wrap; align-items: center; }
        .tab { padding: 8px 14px; font-size: 12px; font-weight: 600; color: var(--text-muted); cursor: pointer; border-radius: var(--radius-sm); transition: 0.15s; white-space: nowrap; letter-spacing: 0.1px; }
        .tab:hover { color: #fff; background: var(--bg-elev); }
        .tab.active { color: #fff; background: var(--bg-elev); box-shadow: inset 0 -2px 0 var(--accent); }

        .unlink-btn { background: transparent; border: 1px solid var(--border); color: var(--text-muted); padding: 7px 13px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 600; font-size: 11px; transition: 0.15s; letter-spacing: 0.2px; }
        .unlink-btn:hover { background: rgba(239,68,68,0.08); color: var(--danger); border-color: rgba(239,68,68,0.4); }

        @media (max-width: 1120px) {
            .tabs { gap: 2px; }
            .tab { padding: 7px 10px; font-size: 11px; }
        }
        @media (max-width: 900px) {
            .top-bar { padding: 8px 14px; gap: 10px; }
            .tabs { gap: 2px; width: 100%; order: 3; overflow-x: auto; }
            .tabs::-webkit-scrollbar { height: 4px; }
            .tabs::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
        }

        .content { flex: 1; padding: 28px 32px; overflow-y: auto; display: none; }
        .content.active { display: block; animation: fadein 0.25s; }
        @media (max-width: 1120px) { .content { padding: 22px; } }
        @media (max-width: 900px) { .content { padding: 14px; } }
        .content h2 { font-size: 18px; font-weight: 700; letter-spacing: -0.3px; margin: 0 0 20px; color: #fff; }
        @keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse {
            0% { transform: translateX(-40%); opacity: 0.75; }
            50% { transform: translateX(30%); opacity: 1; }
            100% { transform: translateX(110%); opacity: 0.75; }
        }

        /* ---------- OVERVIEW ---------- */
        .overview-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; gap: 16px; flex-wrap: wrap; }
        .overview-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
        .ov-card {
            background: var(--bg-panel);
            border: 1px solid var(--border-soft);
            border-radius: var(--radius-lg);
            padding: 16px;
            display: flex;
            align-items: center;
            gap: 13px;
            transition: 0.15s;
            cursor: pointer;
            position: relative;
        }
        .ov-card:hover { border-color: var(--border); background: var(--bg-elev); transform: translateY(-1px); box-shadow: var(--shadow-md); }
        .ov-avatar-wrap { position: relative; flex-shrink: 0; }
        .ov-avatar { width: 46px; height: 46px; border-radius: var(--radius-md); object-fit: cover; border: 1px solid var(--border-soft); background: #222; }
        .ov-meta { flex: 1; min-width: 0; }
        .ov-name { font-weight: 600; color: #fff; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 14px; letter-spacing: -0.2px; }
        .ov-stats { display: flex; gap: 10px; font-family: 'JetBrains Mono'; font-size: 11px; margin-top: 4px; }
        .ov-stat { display: flex; align-items: center; gap: 3px; }
        .ov-empty { grid-column: 1/-1; padding: 60px 20px; text-align: center; color: var(--text-dim); font-size: 13px; border: 1px dashed var(--border); border-radius: var(--radius-lg); }

        /* ---------- DASHBOARD STATS ---------- */
        .dash-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 14px; }
        @media (max-width: 900px) { .dash-grid { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; } }
        .stat-box { background: var(--bg-panel); border: 1px solid var(--border-soft); padding: 16px 18px; border-radius: var(--radius-lg); display: flex; flex-direction: column; gap: 6px; transition: border-color 0.15s; }
        .stat-box:hover { border-color: var(--border); }
        .sb-label { font-size: 9px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.6px; }
        .sb-val { font-size: 19px; font-weight: 700; color: #fff; font-family: 'JetBrains Mono'; letter-spacing: -0.3px; }
        .sb-sub { font-size: 10px; color: var(--text-dim); font-family: 'JetBrains Mono'; }

        /* ---------- INVENTORY ---------- */
        .inv-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
        .inv-toolbar-right { display: flex; align-items: center; gap: 12px; }
        .inv-count-pill { background: var(--bg-panel); border: 1px solid var(--border-soft); padding: 7px 12px; border-radius: var(--radius-sm); font-family: 'JetBrains Mono'; font-size: 12px; color: #fff; font-weight: 600; }
        .btn-accent { background: var(--accent); color: #04110a; border: none; padding: 8px 14px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 700; font-size: 11px; transition: 0.15s; letter-spacing: 0.2px; }
        .btn-accent:hover { background: #22d39a; }

        .inv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }
        .skin-card {
            background: var(--bg-panel);
            border: 1px solid var(--border-soft);
            border-radius: var(--radius-md);
            padding: 10px;
            position: relative;
            overflow: hidden;
            transition: 0.15s;
            display: flex;
            flex-direction: column;
        }
        .skin-card:hover { border-color: var(--border); transform: translateY(-1px); box-shadow: var(--shadow-md); }

        .skin-card.is-event::before { content: ''; position: absolute; inset: 0; background: var(--event-grad); opacity: 0.1; z-index: 0; pointer-events: none; }
        .skin-card > * { position: relative; z-index: 1; }

        .star-btn { position: absolute; top: 5px; left: 5px; cursor: pointer; font-size: 15px; color: #3a3a42; transition: 0.15s; z-index: 3; }
        .star-btn:hover { transform: scale(1.15); }
        .star-btn.active { color: var(--warn); text-shadow: 0 0 6px rgba(234, 179, 8, 0.5); }

        .sticker-container { position: absolute; top: 5px; right: 5px; display: flex; flex-direction: column; gap: 2px; z-index: 2; pointer-events: none; }
        .sticker-mini { width: 22px; height: 22px; object-fit: contain; filter: drop-shadow(0 2px 3px rgba(0,0,0,0.7)); }

        .skin-img { width: 100%; height: 86px; object-fit: contain; margin-bottom: 8px; filter: drop-shadow(0 4px 6px rgba(0,0,0,0.5)); margin-top: 8px; }
        .skin-name { font-size: 11px; font-weight: 700; color: #e4e4e7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px; }
        .skin-rarity-bar { height: 2px; width: 100%; position: absolute; bottom: 0; left: 0; }
        .skin-price { font-size: 10px; color: var(--accent); font-family: 'JetBrains Mono'; margin-bottom: 8px; font-weight: 600; }

        .card-actions { display: flex; gap: 4px; margin-top: auto; border-top: 1px solid var(--border-soft); padding-top: 8px; }
        .act-btn { flex: 1; background: var(--bg-elev); border: 1px solid var(--border-soft); color: var(--text-muted); font-size: 10px; padding: 5px; border-radius: 4px; cursor: pointer; font-weight: 700; transition: 0.12s; display: flex; align-items: center; justify-content: center; }
        .act-btn:hover { color: #fff; background: var(--bg-hover); }
        .act-btn.tok { color: var(--warn); border-color: rgba(234, 179, 8, 0.2); }
        .act-btn.tok:hover { background: rgba(234, 179, 8, 0.1); }
        .act-btn.mon { color: var(--accent); border-color: rgba(16, 185, 129, 0.2); }
        .act-btn.mon:hover { background: rgba(16, 185, 129, 0.1); }
        .act-btn.cpy { color: var(--info); border-color: rgba(96, 165, 250, 0.2); }
        .act-btn.cpy:hover { background: rgba(96, 165, 250, 0.1); }

        /* ---------- TRANSFER ---------- */
        .transfer-panel { max-width: 560px; margin: 0 auto; background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 32px; }
        .transfer-panel h2 { margin: 0 0 22px; text-align: center; }
        .chk-row { display: flex; gap: 12px; margin: 0 0 18px; }
        .chk-box { flex: 1; background: var(--bg-elev); border: 1px solid var(--border-soft); padding: 16px; border-radius: var(--radius-md); cursor: pointer; display: flex; justify-content: space-between; align-items: center; transition: 0.15s; }
        .chk-box:hover { border-color: var(--border); }
        .chk-box.selected { border-color: var(--accent); background: var(--accent-soft); }
        .big-btn { width: 100%; padding: 14px; font-size: 13px; font-weight: 800; background: #fafafa; color: #000; border: none; border-radius: var(--radius-md); cursor: pointer; text-transform: uppercase; letter-spacing: 0.5px; transition: background 0.15s; }
        .big-btn:hover { background: #fff; }

        /* ---------- CARDS (convert, booster panels) ---------- */
        .panel { background: var(--bg-panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 20px; }
        .panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
        .panel-title { font-size: 13px; font-weight: 700; color: #fff; letter-spacing: 0.2px; }
        .mini-stat { background: var(--bg-elev); border: 1px solid var(--border-soft); border-radius: var(--radius-sm); padding: 10px 12px; }
        .mini-stat-label { font-size: 9px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700; }
        .mini-stat-value { color: #fff; font-weight: 800; font-family: 'JetBrains Mono'; margin-top: 4px; font-size: 13px; }

        .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; display: none; align-items: center; justify-content: center; backdrop-filter: blur(5px); }
        .term-win { width: 600px; max-width: calc(100vw - 40px); background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-lg); display: flex; flex-direction: column; overflow: hidden; }
        .term-head { background: var(--bg-elev); padding: 12px 18px; border-bottom: 1px solid var(--border-soft); display: flex; justify-content: space-between; align-items: center; }
        .term-body { height: 300px; padding: 18px; overflow-y: auto; font-family: 'JetBrains Mono'; font-size: 12px; color: var(--text-muted); display: flex; flex-direction: column; gap: 6px; }
        .term-foot { padding: 14px; background: var(--bg-elev); border-top: 1px solid var(--border-soft); text-align: right; }
        .trade-btn { background: var(--accent); color: #04110a; font-weight: 800; padding: 8px 16px; border-radius: var(--radius-sm); text-decoration: none; font-size: 12px; display: inline-block; letter-spacing: 0.3px; }
        .hidden { display: none !important; }
    </style>
</head>
<body>

<!-- Resize grips (frameless window) -->
<div class="cc-grip cc-grip-n"></div>
<div class="cc-grip cc-grip-s"></div>
<div class="cc-grip cc-grip-w"></div>
<div class="cc-grip cc-grip-e"></div>
<div class="cc-grip cc-grip-nw"></div>
<div class="cc-grip cc-grip-ne"></div>
<div class="cc-grip cc-grip-sw"></div>
<div class="cc-grip cc-grip-se"></div>

<div class="cc-titlebar" data-pywebview-drag-region>
  <div class="cc-titlebar-title" data-pywebview-drag-region><b>CC</b> Case Clicker Hub</div>
  <div class="cc-titlebar-spacer" data-pywebview-drag-region></div>
  <div class="cc-titlebar-buttons" data-no-drag>
    <button class="cc-titlebar-btn" data-no-drag title="Minimize" onclick="cchubMinimize()">&#xE921;</button>
    <button class="cc-titlebar-btn" data-no-drag title="Maximize" onclick="cchubToggleMax()">&#xE922;</button>
    <button class="cc-titlebar-btn cc-close" data-no-drag title="Close to tray" onclick="cchubClose()">&#xE8BB;</button>
  </div>
</div>
<div class="cc-app-body">

<div id="nprogress"><div class="bar"><div class="peg"></div></div></div>

<div class="sidebar">
    <div class="brand">
        <div class="brand-title" onclick="goHome()">
            <span class="logo">CC</span>
            <span>Case Clicker Hub</span>
        </div>
        <button id="editBtn" class="edit-btn" onclick="toggleEditMode()">EDIT</button>
    </div>

    <div class="sidebar-controls">
        <div class="sidebar-search">
            <input id="accSearch" type="text" placeholder="Search accounts..." oninput="filterAccounts()" autocomplete="off">
        </div>
        <button id="statusFilterBtn" class="sidebar-filter" onclick="cycleStatusFilter()" title="Filter by status">All</button>
    </div>

    <div id="bulkBar" class="bulk-bar">
        <div class="bulk-bar-count"><span id="bulkSelectedCount">0</span> selected</div>
        <div class="bulk-bar-actions">
            <button class="bulk-btn" onclick="bulkSelectAll()">ALL</button>
            <button class="bulk-btn" onclick="bulkClearSelection()">CLEAR</button>
            <button class="bulk-btn" onclick="bulkSetMain()" title="Mark selected as main account">SET MAIN</button>
            <button class="bulk-btn danger" onclick="bulkDelete()">UNLINK</button>
        </div>
    </div>

    <div id="editHint" class="edit-hint">Click a row to select it. Drag the handle to reorder. The MAIN account is always pinned at the top.</div>

    <div class="acc-list" id="accList"></div>

    <div class="settings-area">
        <div class="settings-label">Main Account ID</div>
        <input id="mainId" class="settings-input" placeholder="Account ID...">
        <div style="height:10px;"></div>
        <div class="settings-label">Remote URL <span style="color:var(--text-dim); font-weight:500; text-transform:none; letter-spacing:0;">(optional)</span></div>
        <input id="remoteUrl" class="settings-input" placeholder="100.x.y.z or http://100.x.y.z:5000">
        <div id="remoteUrlHint" class="hint-text"></div>
        <button onclick="saveSettings()" class="save-btn">SAVE CONFIG</button>
        <button id="copyWorkerLinkBtn" onclick="copyWorkerLink()" class="copy-link-btn" title="Copy the link workers paste into Tampermonkey">COPY WORKER LINK</button>
        <button id="checkUpdateBtn" onclick="checkForUpdate()" class="copy-link-btn" title="Check GitHub for a newer release">CHECK FOR UPDATES</button>
    </div>
</div>

<div class="main">
    <!-- GLOBAL HEADER -->
    <div class="global-header">
        <div class="gh-stats">
            <div class="gh-item" data-short="U"><div class="gh-label">Total Users</div><div class="gh-val" id="ghUsers">0</div></div>
            <div class="gh-item" data-short="$"><div class="gh-label">Total Money</div><div class="gh-val mon" id="ghMoney">$0</div></div>
            <div class="gh-item" data-short="T"><div class="gh-label">Total Tokens</div><div class="gh-val tok" id="ghTokens">0</div></div>
            <div class="gh-item" data-short="NW"><div class="gh-label">Networth</div><div class="gh-val" id="ghNet">0</div></div>
        </div>
        <div class="active-jobs-wrap">
            <button class="active-jobs-btn" onclick="toggleActiveJobs()" title="Active Jobs">
                <span class="active-jobs-btn-label">Active Jobs</span>
                <span class="badge" id="activeJobsBadge" data-zero="1">0</span>
                <span class="badge" id="activeJobsAlertBadge" data-zero="1" style="background:#ef4444; color:#fff;">0</span>
            </button>
            <div class="active-jobs-dropdown" id="activeJobsDropdown">
                <div class="active-jobs-header">
                    <h3>Active Jobs</h3>
                    <button onclick="toggleActiveJobs()" style="background:transparent; border:none; color:#666; cursor:pointer; font-size:18px;">×</button>
                </div>
                <div class="active-jobs-list" id="activeJobsList"></div>
            </div>
        </div>
    </div>

    <!-- USER TOP BAR -->
    <div class="top-bar hidden" id="topBar">
        <div class="user-head">
            <img id="uhAvatar" class="uh-avatar" src="" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2764%27 height=%2764%27%3E%3Crect width=%27100%25%27 height=%27100%25%27 fill=%27%23333%27/%3E%3C/svg%3E'">
            <div class="uh-meta">
                <h1 id="uhName">User</h1>
                <p id="uhId">ID: ...</p>
            </div>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="setTab('dash')">Dashboard</div>
            <div class="tab" onclick="setTab('inv')">Inventory</div>
            <div class="tab" onclick="setTab('trans')">Transfer</div>
            <div class="tab" onclick="setTab('conv')">Convert</div>
            <div class="tab" onclick="setTab('boost')">Booster</div>
        </div>
        <button onclick="deleteAcc()" class="unlink-btn">UNLINK</button>
    </div>

    <!-- HOME / OVERVIEW -->
    <div id="view-overview" class="content active">
        <div class="overview-header">
            <h2 style="margin:0;">Account Overview</h2>
            <div id="overviewSummary" style="font-size:12px; color:var(--text-dim); font-family:'JetBrains Mono';"></div>
        </div>
        <div class="overview-grid" id="overviewGrid"></div>
    </div>

    <!-- USER DASHBOARD -->
    <div id="view-dash" class="content">
        <div class="dash-grid">
            <div class="stat-box"><div class="sb-label">Wallet Balance</div><div class="sb-val" style="color:var(--accent)">$<span id="dMoney">0</span></div></div>
            <div class="stat-box"><div class="sb-label">Clicker Tokens</div><div class="sb-val" style="color:#eab308"><span id="dTokens">0</span></div></div>
            <div class="stat-box"><div class="sb-label">Cases / Click</div><div class="sb-val"><span id="dCpc">0</span></div><div class="sb-sub">Max $<span id="dCpcMax">0</span></div></div>
            <div class="stat-box"><div class="sb-label">Total XP</div><div class="sb-val"><span id="dXp">0</span></div></div>
        </div>
        <div class="dash-grid">
            <div class="stat-box"><div class="sb-label">Vault Status</div><div class="sb-val">$<span id="dVpm">0</span>/m</div><div class="sb-sub" id="dVaultTime">Last: Never</div></div>
            <div class="stat-box"><div class="sb-label">Cases Opened</div><div class="sb-val"><span id="dOpen">0</span></div></div>
            <div class="stat-box"><div class="sb-label">Inventory Size</div><div class="sb-val"><span id="dInvSize">0</span> Items</div></div>
            <div class="stat-box"><div class="sb-label">Premier Rating</div><div class="sb-val" id="dPrem">0</div></div>
        </div>
    </div>

    <!-- INVENTORY -->
    <div id="view-inv" class="content">
        <div class="inv-toolbar">
            <h2 style="margin:0;">Inventory</h2>
            <div class="inv-toolbar-right">
                <div class="inv-count-pill"><span id="invCount">0</span> items</div>
                <button onclick="refreshInventory()" class="btn-accent">REFRESH</button>
            </div>
        </div>
        <div class="inv-grid" id="invGrid"></div>
    </div>

    <!-- TRANSFER -->
    <div id="view-trans" class="content">
        <div class="transfer-panel">
            <h2>Asset Transfer</h2>
            <div class="chk-row">
                <div class="chk-box" id="chkTok" onclick="toggleT('tok')">
                    <div><div style="font-weight:700;color:#fff;">Tokens</div><div style="font-size:11px;color:var(--text-dim); margin-top:2px;">Choose amount</div></div>
                    <div style="color:var(--accent); font-weight:bold; font-size:16px;" id="indTok"></div>
                </div>
                <div class="chk-box" id="chkSkin" onclick="toggleT('skin')">
                    <div><div style="font-weight:700;color:#fff;">Skins</div><div style="font-size:11px;color:var(--text-dim); margin-top:2px;">All cached items</div></div>
                    <div style="color:var(--accent); font-weight:bold; font-size:16px;" id="indSkin"></div>
                </div>
            </div>
            <div id="tokenAmountWrap" style="display:none; margin-bottom:16px;">
                <div class="sb-label" style="margin-bottom:6px;">Token Amount</div>
                <div style="display:flex; gap:8px;">
                    <input id="tokenAmountInput" type="number" min="1" step="1" placeholder="Enter amount..." style="flex:1; padding:10px 12px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); font-family:'JetBrains Mono'; outline:none;">
                    <button onclick="setMaxTokens()" class="btn-accent" style="padding:10px 18px;">MAX</button>
                </div>
                <div id="tokenAvailable" style="margin-top:6px; font-size:11px; color:var(--text-dim); font-family:'JetBrains Mono';"></div>
            </div>
            <button class="big-btn" onclick="runTransfer()">INITIATE TRANSFER</button>
        </div>
    </div>

    <!-- CONVERT -->
    <div id="view-conv" class="content">
        <div style="max-width:1200px; margin:0 auto;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:8px;">
                <h2 style="margin:0;">Case Converter</h2>
                <div style="font-family:'JetBrains Mono'; font-size:12px; color:var(--text-muted);">Balance: <span style="color:var(--accent);">$<span id="convBalance">0</span></span></div>
            </div>

            <!-- CONVERT JOB STATUS -->
            <div id="convertJobStatus" class="panel" style="display:none; margin-bottom:18px; border-color: rgba(16,185,129,0.3); box-shadow: 0 0 24px rgba(16, 185, 129, 0.12);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                    <div>
                        <div style="font-weight:700; color:#fff; font-size:13px; margin-bottom:3px;">Converting to <span id="jobSellMethod" style="color:var(--accent);">tokens</span></div>
                        <div style="font-size:11px; color:var(--text-dim); font-family:'JetBrains Mono';" id="jobCaseName">Case Name</div>
                    </div>
                    <div style="font-family:'JetBrains Mono'; font-size:20px; color:var(--accent); font-weight:700;" id="jobProgressText">0%</div>
                </div>
                <div style="background:var(--bg-dark); border-radius:var(--radius-sm); padding:2px; margin-bottom:12px; overflow:hidden; position:relative;">
                    <div id="jobProgressBar" style="background:linear-gradient(90deg, var(--accent) 0%, #34d399 100%); height:26px; border-radius:4px; transition:width 0.3s ease; width:0%; display:flex; align-items:center; justify-content:center; color:#04110a; font-weight:800; font-size:11px;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-muted); font-family:'JetBrains Mono';">
                    <div>Opened: <span id="jobOpened" style="color:#fff; font-weight:700;">0</span></div>
                    <div>Remaining: <span id="jobRemaining" style="color:#fff; font-weight:700;">0</span></div>
                    <div>Total: <span id="jobTotal" style="color:#fff; font-weight:700;">0</span></div>
                </div>
                <div style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border-soft); font-size:11px; color:var(--text-muted); font-family:'JetBrains Mono';">
                    Opening: <span id="jobBatchInfo" style="color:var(--accent); font-weight:700;">-</span>
                </div>
                <div style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border-soft); font-size:11px; color:var(--text-muted); font-family:'JetBrains Mono'; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                    <span>Spent: <span id="jobSpent" style="color:#fff; font-weight:700;">$0</span></span>
                    <span>Earned: <span id="jobEarned" style="color:var(--accent); font-weight:700;">-</span></span>
                    <span>ROI: <span id="jobRoi" style="color:#86efac; font-weight:700;">-</span></span>
                </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
                <!-- LEFT: Case Selection -->
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Select Case / Capsule</div>
                    </div>
                    <input id="caseSearch" type="text" placeholder="Search cases..." style="width:100%; padding:9px 12px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); margin-bottom:10px; font-size:12px; outline:none;" oninput="filterCases()">
                    <div style="max-height:400px; overflow-y:auto; border:1px solid var(--border-soft); border-radius:var(--radius-sm); padding:8px;">
                        <div id="casesList" style="display:grid; gap:6px;"></div>
                    </div>
                </div>

                <!-- RIGHT: Configuration -->
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Configuration</div>
                    </div>
                    <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:0 0 12px;">
                        <div class="mini-stat">
                            <div class="mini-stat-label">Units</div>
                            <div id="transCaseUnits" class="mini-stat-value">-</div>
                        </div>
                        <div class="mini-stat">
                            <div class="mini-stat-label">Value (100%)</div>
                            <div id="transCaseValue" class="mini-stat-value" style="color:var(--info);">$-</div>
                        </div>
                        <div class="mini-stat">
                            <div class="mini-stat-label">Sell Value (70%)</div>
                            <div id="transCaseSellValue" class="mini-stat-value" style="color:var(--accent);">$-</div>
                        </div>
                    </div>
                    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; gap:8px; flex-wrap:wrap;">
                        <div id="transCaseUpdated" style="font-size:10px; color:var(--text-dim); font-family:'JetBrains Mono';">Not scanned yet</div>
                        <div style="display:flex; gap:6px;">
                            <button id="btnRefreshCaseSummary" onclick="requestCaseSummary(true)" style="padding:7px 10px; background:var(--bg-elev); border:1px solid var(--border); color:#fff; border-radius:var(--radius-sm); cursor:pointer; font-size:10px; font-weight:700; letter-spacing:0.3px;">REFRESH</button>
                            <button id="btnSellCases" onclick="sellCasesSnapshot()" style="padding:7px 10px; background:var(--warn); border:none; color:#000; border-radius:var(--radius-sm); cursor:pointer; font-size:10px; font-weight:800; letter-spacing:0.3px;">SELL CASES</button>
                        </div>
                    </div>
                    <div id="sellCasesLoader" style="display:none; margin-bottom:14px; background:var(--bg-elev); border:1px solid var(--border-soft); border-radius:var(--radius-sm); padding:10px;">
                        <div style="height:6px; border-radius:999px; background:var(--bg-dark); overflow:hidden;">
                            <div id="sellCasesLoaderBar" style="height:100%; width:35%; background:linear-gradient(90deg, var(--accent), #22d3ee); animation: pulse 1.1s ease-in-out infinite;"></div>
                        </div>
                        <div id="sellCasesLoaderText" style="font-size:11px; color:var(--text-muted); margin-top:8px;">Selling snapshot of current cases...</div>
                    </div>

                    <div id="selectedCaseInfo" style="display:none; margin-bottom:16px; padding:12px 14px; background:var(--bg-elev); border-radius:var(--radius-sm); border:1px solid var(--border-soft);">
                        <div style="font-weight:700; color:#fff; margin-bottom:4px;" id="selectedCaseName"></div>
                        <div style="font-size:11px; color:var(--text-muted); font-family:'JetBrains Mono';">Price: $<span id="selectedCasePrice">0</span></div>
                    </div>

                    <div style="margin-bottom:16px;">
                        <div class="sb-label" style="margin-bottom:6px;">Sell Method</div>
                        <div style="display:flex; gap:8px;">
                            <button id="btnSellMoney" onclick="setSellMethod('money')" style="flex:1; padding:10px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); cursor:pointer; font-weight:700; transition:0.15s;">Money</button>
                            <button id="btnSellTokens" onclick="setSellMethod('tokens')" style="flex:1; padding:10px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); cursor:pointer; font-weight:700; transition:0.15s;">Tokens</button>
                        </div>
                    </div>

                    <div style="margin-bottom:16px;">
                        <div class="sb-label" style="margin-bottom:6px;">Budget</div>
                        <div style="display:flex; gap:8px;">
                            <input id="convBudget" type="number" placeholder="Amount..." style="flex:1; padding:10px 12px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); font-family:'JetBrains Mono'; outline:none;" min="0" step="0.01" oninput="updateCaseCount()">
                            <button onclick="setMaxBudget()" class="btn-accent" style="padding:10px 16px;">MAX</button>
                        </div>
                        <div style="margin-top:6px; font-size:11px; color:var(--text-dim); font-family:'JetBrains Mono';" id="convCaseCount">Estimated: 0 cases</div>
                    </div>

                    <button id="btnConfirmConvert" onclick="confirmConvert()" class="big-btn" style="opacity:0.5; cursor:not-allowed;" disabled>CONFIRM</button>
                </div>
            </div>
        </div>
    </div>

    <!-- BOOSTER -->
    <div id="view-boost" class="content">
        <div style="max-width:1200px; margin:0 auto;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:8px;">
                <h2 style="margin:0;">Alt Booster</h2>
                <div style="font-family:'JetBrains Mono'; font-size:12px; color:var(--text-muted);">Boost to global by cycling case opens</div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Account Overview</div>
                    </div>
                    <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
                        <img id="boosterRankImage" src="https://case-clicker.com/img/unknown.png" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2764%27 height=%2764%27%3E%3Crect width=%27100%25%27 height=%27100%25%27 fill=%27%23333%27/%3E%3C/svg%3E'" style="width:46px; height:46px; object-fit:contain; border:1px solid var(--border-soft); border-radius:var(--radius-md); background:var(--bg-elev); padding:4px;">
                        <div>
                            <div style="font-size:10px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; font-weight:700;">Current Rank</div>
                            <div id="boosterRankName" style="font-weight:700; color:#fff; margin-top:2px;">Unknown</div>
                        </div>
                    </div>
                    <div style="font-size:10px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; font-weight:700;">Created at</div>
                    <div id="boosterCreatedAt" style="font-family:'JetBrains Mono'; color:#fff; margin-top:4px; font-size:13px;">Unknown</div>
                </div>
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Case Selection</div>
                    </div>
                    <select id="boosterCaseSelect" style="width:100%; padding:10px 12px; background:var(--bg-elev); border:1px solid var(--border-soft); color:#fff; border-radius:var(--radius-sm); margin-bottom:12px; outline:none; font-family:'Inter'; cursor:pointer;"></select>
                    <label style="display:flex; align-items:center; gap:10px; font-size:12px; color:#e4e4e7; margin-bottom:16px; background:var(--bg-elev); border:1px solid var(--border-soft); border-radius:var(--radius-sm); padding:11px 12px; cursor:pointer;">
                        <input id="boosterClickUntil" type="checkbox" style="width:14px; height:14px; accent-color: var(--accent); cursor:pointer;">
                        <span>Click until boost ends (48h account age)</span>
                    </label>
                    <button id="boosterStartBtn" onclick="startBooster()" class="big-btn" style="background:var(--accent); color:#04110a;">START BOOSTER</button>
                </div>
            </div>
        </div>
    </div>

</div>

<!-- MODAL -->
<div class="modal" id="logModal">
    <div class="term-win">
        <div class="term-head"><div style="font-weight:700; font-size:12px; color:#fff;">TERMINAL</div><div style="cursor:pointer;" onclick="closeModal()">✕</div></div>
        <div class="term-body" id="termLogs"></div>
        <div class="term-foot"><a id="tradeLink" href="#" target="_blank" class="trade-btn hidden">OPEN TRADE</a></div>
    </div>
</div>

<script>
    const DEFAULT_AVATAR_PATH = "M0.877014 7.49988C0.877014 3.84219 3.84216 0.877045 7.49985 0.877045C11.1575 0.877045 14.1227 3.84219 14.1227 7.49988C14.1227 11.1575 11.1575 14.1227 7.49985 14.1227C3.84216 14.1227 0.877014 11.1575 0.877014 7.49988ZM7.49985 1.82704C4.36683 1.82704 1.82701 4.36686 1.82701 7.49988C1.82701 8.97196 2.38774 10.3131 3.30727 11.3213C4.19074 9.94119 5.73818 9.02499 7.50023 9.02499C9.26206 9.02499 10.8093 9.94097 11.6929 11.3208C12.6121 10.3127 13.1727 8.97172 13.1727 7.49988C13.1727 4.36686 10.6328 1.82704 7.49985 1.82704ZM10.9818 11.9787C10.2839 10.7795 8.9857 9.97499 7.50023 9.97499C6.01458 9.97499 4.71624 10.7797 4.01845 11.9791C4.97952 12.7272 6.18765 13.1727 7.49985 13.1727C8.81227 13.1727 10.0206 12.727 10.9818 11.9787ZM5.14999 6.50487C5.14999 5.207 6.20212 4.15487 7.49999 4.15487C8.79786 4.15487 9.84999 5.207 9.84999 6.50487C9.84999 7.80274 8.79786 8.85487 7.49999 8.85487C6.20212 8.85487 5.14999 7.80274 5.14999 6.50487ZM7.49999 5.10487C6.72679 5.10487 6.09999 5.73167 6.09999 6.50487C6.09999 7.27807 6.72679 7.90487 7.49999 7.90487C8.27319 7.90487 8.89999 7.27807 8.89999 6.50487C8.89999 5.73167 8.27319 5.10487 7.49999 5.10487Z";
    const IMG_FALLBACK = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 15 15"><circle cx="7.5" cy="7.5" r="7.5" fill="#1f2937"/><path d="${DEFAULT_AVATAR_PATH}" fill="#d1d5db"/></svg>`)}`;
    let accounts = [];
    let selectedId = null;
    let transferState = { tok: false, skin: false };
    let lastLog = "";
    let isEditMode = false;
    let mainId = "";
    let cases = [];
    let selectedCase = null;
    let sellMethod = null;
    let convertBudget = 0;
    let selectedBoosterCaseId = null;
    let transferSelling = false;
    let transferSellState = 'idle'; // idle | loading | done | error
    let transferSummaryRequestedAt = 0;
    let sellDoneResetTimer = null;
    let accSearchQuery = "";
    let statusFilter = "all"; // "all" | "online" | "offline"
    let selectedIds = new Set();

    const bar = document.querySelector('#nprogress .bar');
    function loadStart() { bar.style.width = '0%'; setTimeout(()=>bar.style.width='60%', 50); }
    function loadEnd() { bar.style.width = '100%'; setTimeout(()=>bar.style.width='0%', 200); }

    async function init() {
        loadStart();
        const s = await (await fetch('/api/settings')).json();
        mainId = s.main_id || "";
        document.getElementById('mainId').value = mainId;
        try {
            const r = await (await fetch('/api/remote_url')).json();
            document.getElementById('remoteUrl').value = r.url || "";
            const hint = document.getElementById('remoteUrlHint');
            if (r.detected) {
                hint.textContent = `Auto-detected: http://${r.detected}:${r.port}`;
            } else {
                hint.textContent = "No Tailscale detected — paste URL manually.";
            }
        } catch (e) { /* non-fatal */ }
        cases = await (await fetch('/api/cases')).json();
        await refresh();
        loadEnd();
        setInterval(refresh, 2000);
    }

    async function refresh() {
        if(isDraggingItem) return;

        const res = await fetch('/api/accounts');
        accounts = await res.json();
        
        renderGlobalStats();
        if(!isEditMode) renderSidebar();
        
        if(selectedId) {
            updateView(selectedId);
            // Update convert tab balance if it's active
            if(document.getElementById('view-conv').classList.contains('active')) {
                renderConvertTab();
            }
        } else {
            renderOverview();
        }
        
        // Always update convert job status if visible
        if(document.getElementById('view-conv').classList.contains('active')) {
            renderConvertTab();
        }
        if(document.getElementById('view-boost').classList.contains('active')) {
            renderBoosterTab();
        }
        if(document.getElementById('view-trans').classList.contains('active')) {
            renderTransferTab();
        }
        
        // Update active jobs
        updateActiveJobs();
    }

    function renderGlobalStats() {
        let tMoney = 0;
        let tTokens = 0;
        let tNet = 0;
        let proCount = 0;

        accounts.forEach(a => {
            const s = a.stats || {};
            tMoney += s.money || 0;
            tTokens += s.tokens || 0;
            tNet += s.networth || 0;
            if(s.membership === 'pro') proCount++;
        });

        document.getElementById('ghUsers').innerText = `${accounts.length} (${proCount} Pro)`;
        document.getElementById('ghMoney').innerText = "$" + Math.floor(tMoney).toLocaleString();
        document.getElementById('ghTokens').innerText = Math.floor(tTokens).toLocaleString();
        document.getElementById('ghNet').innerText = "$" + Math.floor(tNet).toLocaleString();
    }

    function goHome() {
        selectedId = null;
        document.getElementById('topBar').classList.add('hidden');
        document.querySelectorAll('.acc-item').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.content').forEach(x => x.classList.remove('active'));
        document.getElementById('view-overview').classList.add('active');
        renderOverview();
    }

    function renderOverview() {
        const grid = document.getElementById('overviewGrid');
        const summary = document.getElementById('overviewSummary');
        grid.innerHTML = '';

        if (accounts.length === 0) {
            grid.innerHTML = '<div class="ov-empty">No accounts linked yet. Copy the worker link from the sidebar and paste it into Tampermonkey.</div>';
            if (summary) summary.textContent = '';
            return;
        }

        const onlineCount = accounts.filter(accountIsOnline).length;
        if (summary) summary.textContent = `${onlineCount}/${accounts.length} online`;

        accounts.forEach(acc => {
            const s = acc.stats || {};
            const isOnline = accountIsOnline(acc);

            const card = document.createElement('div');
            card.className = 'ov-card';
            card.onclick = () => selectAcc(acc.id);

            card.innerHTML = `
                <div class="ov-avatar-wrap">
                    <img src="${acc.avatar || 'https://case-clicker.com/img/unknown.png'}" class="ov-avatar" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='${IMG_FALLBACK}'">
                    <div class="avatar-status ${isOnline ? 'online' : ''}"></div>
                </div>
                <div class="ov-meta">
                    <div class="ov-name">${acc.username || 'Unknown'}${acc.id === mainId ? ' <span class="main-tag" style="margin-left:4px;">MAIN</span>' : ''}</div>
                    <div class="ov-stats">
                        <div class="ov-stat" style="color:var(--accent)">$${Math.floor(s.money||0).toLocaleString()}</div>
                        <div class="ov-stat" style="color:var(--warn)">T ${Math.floor(s.tokens||0).toLocaleString()}</div>
                        <div class="ov-stat" style="color:var(--text-dim)">${isOnline ? 'Online' : 'Offline'}</div>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    function accountIsOnline(acc) {
        const s = acc.stats || {};
        const now = Date.now() / 1000;
        let online = (now - (acc.last_seen || 0)) < 15;
        if (s.vaultLastCollected) {
            const vaultTime = new Date(s.vaultLastCollected).getTime() / 1000;
            if ((now - vaultTime) > 180) online = false;
        }
        return online;
    }

    function matchesSearch(acc, q) {
        if (!q) return true;
        const query = q.toLowerCase();
        const name = (acc.username || '').toLowerCase();
        const id = (acc.id || '').toLowerCase();
        return name.includes(query) || id.includes(query);
    }

    function renderSidebar() {
        const list = document.getElementById('accList');
        list.innerHTML = '';

        let sortedAccs = [...accounts];
        if(mainId) {
            const mainAcc = sortedAccs.find(a => a.id === mainId);
            if(mainAcc) {
                sortedAccs = sortedAccs.filter(a => a.id !== mainId);
                sortedAccs.unshift(mainAcc);
            }
        }

        const filtered = sortedAccs.filter(acc => {
            if (!matchesSearch(acc, accSearchQuery)) return false;
            const online = accountIsOnline(acc);
            if (statusFilter === 'online' && !online) return false;
            if (statusFilter === 'offline' && online) return false;
            return true;
        });

        if (filtered.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'acc-empty';
            empty.textContent = accounts.length === 0 ? 'No accounts linked yet.' : 'No accounts match your search.';
            list.appendChild(empty);
            updateBulkBar();
            return;
        }

        filtered.forEach((acc, index) => {
            const s = acc.stats || {};
            const isOnline = accountIsOnline(acc);
            const isPro = s.membership === 'pro';
            let premRating = s.premierRating || 0;
            let premColor = '255, 215, 0';
            if (s.premierRank && s.premierRank.color) premColor = s.premierRank.color;
            const premBg = `rgba(${premColor}, 0.2)`;
            const premFg = `rgb(${premColor})`;

            const avatarUrl = acc.avatar && acc.avatar !== "" ? acc.avatar : "https://case-clicker.com/img/unknown.png";
            const isMain = acc.id === mainId;
            const isSelected = selectedIds.has(acc.id);

            const el = document.createElement('div');
            let classes = `acc-item ${acc.id === selectedId ? 'active' : ''} ${isMain ? 'pinned' : ''}`;
            if (isEditMode && isSelected) classes += ' selected';
            el.className = classes;
            el.dataset.id = acc.id;

            if(isEditMode && !isMain) {
                el.classList.add('draggable');
                el.draggable = true;
                el.addEventListener('dragstart', handleDragStart);
                el.addEventListener('dragover', handleDragOver);
                el.addEventListener('drop', handleDrop);
                el.addEventListener('dragend', handleDragEnd);
            }

            el.onclick = (e) => {
                if (isEditMode) {
                    if (e.target.closest('.acc-quick-delete')) return;
                    toggleBulkSelect(acc.id);
                } else {
                    selectAcc(acc.id);
                }
            };

            const money = Math.floor(s.money || 0);
            const tokens = Math.floor(s.tokens || 0);
            const metaLine = `<span class="mm">$${money.toLocaleString()}</span><span class="mt">T ${tokens.toLocaleString()}</span>`;

            el.innerHTML = `
                <div class="acc-check ${isEditMode && !isMain ? 'show' : ''} ${isSelected ? 'checked' : ''}"></div>
                <div class="acc-drag-handle ${isEditMode && !isMain ? 'show' : ''}" title="Drag to reorder">⋮⋮</div>
                <div class="acc-avatar-wrap">
                    <img src="${avatarUrl}" class="acc-avatar" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='${IMG_FALLBACK}'">
                    <div class="avatar-status ${isOnline ? 'online' : ''}"></div>
                </div>
                <div class="acc-info">
                    <div class="acc-name-row">
                        <div class="acc-name">${acc.username || 'Unknown'}</div>
                        ${isMain ? '<span class="main-tag">MAIN</span>' : ''}
                    </div>
                    <div class="badge-row">
                        ${isPro ? '<div class="pro-tag">PRO</div>' : ''}
                        ${premRating > 0 ? `
                        <div class="prem-tag" style="background:${premBg}; color:${premFg};">
                            <div class="prem-stripes"><div class="prem-stripe"></div><div class="prem-stripe"></div></div>
                            <div class="prem-val">${premRating.toLocaleString()}</div>
                        </div>` : ''}
                    </div>
                    <div class="acc-meta">${metaLine}</div>
                </div>
                ${isEditMode && !isMain ? `<button class="acc-quick-delete show" title="Unlink this account">×</button>` : ''}
            `;

            if(isEditMode && !isMain) {
                const delBtn = el.querySelector('.acc-quick-delete');
                if (delBtn) {
                    delBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        quickUnlinkAccount(acc.id, acc.username || acc.id);
                    });
                }
            }

            list.appendChild(el);

            if(isMain && index === 0 && filtered.length > 1) {
                const sep = document.createElement('div');
                sep.className = 'main-separator';
                list.appendChild(sep);
            }
        });

        updateBulkBar();
    }

    function filterAccounts() {
        accSearchQuery = (document.getElementById('accSearch')?.value || '').trim();
        renderSidebar();
    }

    function cycleStatusFilter() {
        const next = { all: 'online', online: 'offline', offline: 'all' };
        statusFilter = next[statusFilter] || 'all';
        const btn = document.getElementById('statusFilterBtn');
        if (btn) {
            btn.textContent = statusFilter === 'all' ? 'All' : statusFilter === 'online' ? 'Online' : 'Offline';
            btn.classList.toggle('active', statusFilter !== 'all');
        }
        renderSidebar();
    }

    // --- EDIT MODE + BULK ACTIONS ---
    function toggleEditMode() {
        isEditMode = !isEditMode;
        document.getElementById('editBtn').classList.toggle('active', isEditMode);
        document.getElementById('editHint').classList.toggle('show', isEditMode);
        if (!isEditMode) {
            selectedIds.clear();
        }
        renderSidebar();
    }

    function toggleBulkSelect(id) {
        if (id === mainId) return; // never select main
        if (selectedIds.has(id)) selectedIds.delete(id);
        else selectedIds.add(id);
        renderSidebar();
    }

    function bulkSelectAll() {
        accounts.forEach(a => { if (a.id !== mainId) selectedIds.add(a.id); });
        renderSidebar();
    }

    function bulkClearSelection() {
        selectedIds.clear();
        renderSidebar();
    }

    async function bulkSetMain() {
        if (selectedIds.size !== 1) {
            alert('Select exactly one account to mark as main.');
            return;
        }
        const newMainId = [...selectedIds][0];
        mainId = newMainId;
        document.getElementById('mainId').value = newMainId;
        await fetch('/api/settings', {
            method: 'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({main_id: mainId})
        });
        selectedIds.clear();
        renderSidebar();
    }

    async function bulkDelete() {
        if (selectedIds.size === 0) return;
        const count = selectedIds.size;
        if (!confirm(`Unlink ${count} account${count > 1 ? 's' : ''}? This cannot be undone.`)) return;
        const ids = [...selectedIds];
        for (const id of ids) {
            await fetch('/api/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            if (id === selectedId) selectedId = null;
        }
        selectedIds.clear();
        await refresh();
    }

    async function quickUnlinkAccount(id, label) {
        if (!confirm(`Unlink "${label}"?`)) return;
        await fetch('/api/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id})
        });
        if (id === selectedId) { selectedId = null; goHome(); }
        selectedIds.delete(id);
        await refresh();
    }

    function updateBulkBar() {
        const bar = document.getElementById('bulkBar');
        const count = document.getElementById('bulkSelectedCount');
        if (!bar || !count) return;
        const show = isEditMode && selectedIds.size > 0;
        bar.classList.toggle('show', show);
        count.textContent = selectedIds.size;
    }

    // --- DRAG AND DROP ---
    let draggedItem = null;
    let isDraggingItem = false;

    function handleDragStart(e) {
        draggedItem = this;
        isDraggingItem = true;
        this.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
    }

    function handleDragOver(e) {
        if (e.preventDefault) e.preventDefault();
        return false;
    }

    function handleDrop(e) {
        e.stopPropagation();
        if (draggedItem !== this) {
            if(this.classList.contains('pinned')) return;
            let allItems = [...document.querySelectorAll('.acc-item:not(.pinned)')];
            let dragIdx = allItems.indexOf(draggedItem);
            let dropIdx = allItems.indexOf(this);

            if(dragIdx < dropIdx) {
                this.parentNode.insertBefore(draggedItem, this.nextSibling);
            } else {
                this.parentNode.insertBefore(draggedItem, this);
            }
            saveOrder();
        }
        return false;
    }

    function handleDragEnd() {
        this.classList.remove('dragging');
        draggedItem = null;
        isDraggingItem = false;
    }

    async function saveOrder() {
        const allIds = [];
        if(mainId) allIds.push(mainId);
        document.querySelectorAll('.acc-item').forEach(el => {
            const id = el.dataset.id;
            if(id !== mainId) allIds.push(id);
        });
        await fetch('/api/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: allIds})
        });
    }

    // --- ACCOUNT DETAIL VIEW ---
    function selectAcc(id) {
        loadStart();
        selectedId = id;
        if (sellDoneResetTimer) { clearTimeout(sellDoneResetTimer); sellDoneResetTimer = null; }
        transferSellState = 'idle';
        document.getElementById('view-overview').classList.remove('active');
        document.getElementById('topBar').classList.remove('hidden');
        setTab('dash');
        updateView(id);
        renderInventory(id);
        loadEnd();
        renderSidebar();
    }

    function updateView(id) {
        const acc = accounts.find(a => a.id === id);
        if(!acc) return;
        const s = acc.stats || {};

        document.getElementById('uhName').innerText = (acc.username || 'Unknown');
        const avatarUrl = acc.avatar && acc.avatar !== "" ? acc.avatar : "https://case-clicker.com/img/unknown.png";
        const uhAvatar = document.getElementById('uhAvatar');
        if (uhAvatar && uhAvatar.src !== avatarUrl) uhAvatar.src = avatarUrl;
        document.getElementById('uhId').innerText = "ID: " + acc.id;

        document.getElementById('dMoney').innerText = Math.floor(s.money).toLocaleString();
        document.getElementById('dTokens').innerText = Math.floor(s.tokens).toLocaleString();
        document.getElementById('dCpc').innerText = s.casesPerClick;
        document.getElementById('dCpcMax').innerText = s.casesPerClickMaxPrice || 0;
        document.getElementById('dXp').innerText = Math.floor(s.xp).toLocaleString();
        document.getElementById('dVpm').innerText = s.vaultMoneyPerMinute;
        document.getElementById('dOpen').innerText = s.caseOpenCount.toLocaleString();
        document.getElementById('dInvSize').innerText = (s.skinCount || 0).toLocaleString();
        
        if(s.premierRating && s.premierRating > 0) {
            document.getElementById('dPrem').innerText = s.premierRating.toLocaleString();
            if(s.premierRank) document.getElementById('dPrem').style.color = `rgb(${s.premierRank.color})`;
        } else {
            document.getElementById('dPrem').innerText = "Unranked";
            document.getElementById('dPrem').style.color = "#888";
        }

        if(s.vaultLastCollected) {
            const date = new Date(s.vaultLastCollected);
            const timeStr = date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
            document.getElementById('dVaultTime').innerText = "Collected: " + timeStr;
        }

        const modal = document.getElementById('logModal');
        if(modal.style.display === 'flex' && acc.status_log !== lastLog) {
            lastLog = acc.status_log;
            const t = document.getElementById('termLogs');
            t.innerHTML += `<div>> ${lastLog}</div>`;
            t.scrollTop = t.scrollHeight;
            if(acc.active_trade) {
                const b = document.getElementById('tradeLink');
                b.href = acc.active_trade;
                b.classList.remove('hidden');
            }
        }
    }

    function renderInventory(id) {
        const acc = accounts.find(a => a.id === id);
        const grid = document.getElementById('invGrid');
        grid.innerHTML = '';
        const items = acc.inventory_items || [];
        document.getElementById('invCount').innerText = items.length;

        if(items.length === 0) {
            grid.innerHTML = '<div style="grid-column:1/-1; text-align:center; color:#444; padding:50px;">No skins in inventory</div>';
            return;
        }

        items.forEach(item => {
            const div = document.createElement('div');
            
            let nameStyle = "";
            let cardClass = "skin-card";
            let customStyle = "";
            
            if (item.event && item.event.gradient) {
                nameStyle = `background: ${item.event.gradient}; -webkit-background-clip: text; -webkit-text-fill-color: transparent; display: inline-block; font-weight: 800;`;
                cardClass += " is-event";
                customStyle = `--event-grad: ${item.event.gradient};`;
            }

            div.className = cardClass;
            div.style = customStyle;
            
            const isFav = item.isFavorite === true;
            const starClass = isFav ? 'star-btn active' : 'star-btn';
            const starIcon = '★';

            let stickersHtml = '';
            if (item.processedStickers && item.processedStickers.length > 0) {
                stickersHtml = '<div class="sticker-container">';
                item.processedStickers.forEach(st => {
                     stickersHtml += `<img src="${st.imgUrl}" class="sticker-mini" title="${st.name}">`;
                });
                stickersHtml += '</div>';
            }

            div.innerHTML = `
                <div class="${starClass}" onclick="triggerAction('favorite', '${item._id}', ${!isFav})">${starIcon}</div>
                ${stickersHtml}
                <img src="${item.img}" class="skin-img" referrerpolicy="no-referrer" onerror="this.onerror=null; this.style.opacity=0">
                <div class="skin-name" style="${nameStyle}">${item.name}</div>
                <div class="skin-price">$${item.price}</div>
                <div class="card-actions">
                    <button class="act-btn mon" onclick="triggerAction('sell_money', '${item._id}')" title="Sell for Money">$</button>
                    <button class="act-btn tok" onclick="triggerAction('sell_tokens', '${item._id}')" title="Sell for Tokens">T</button>
                    <button class="act-btn cpy" onclick="copyLink('${item._id}')" title="Copy Link">📋</button>
                </div>
                <div class="skin-rarity-bar" style="background:#${item.rarityColor}"></div>
            `;
            grid.appendChild(div);
        });
        
        if(items.length > 150) {
            const more = document.createElement('div');
            more.style = "text-align:center; color:#555; padding:20px; grid-column:1/-1;";
            more.innerText = `+ ${items.length - 150} more items hidden...`;
            grid.appendChild(more);
        }
    }

    async function triggerAction(actionType, skinId, extraState = null) {
        if(actionType.includes('sell') && !confirm("Are you sure you want to sell this item?")) return;
        
        if(actionType === 'favorite') {
             const acc = accounts.find(a => a.id === selectedId);
             const item = acc.inventory_items.find(i => i._id === skinId);
             if(item) {
                 item.isFavorite = extraState;
                 renderInventory(selectedId);
             }
        }

        await fetch('/api/queue/skin_action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                acc_id: selectedId,
                action: actionType,
                skin_id: skinId,
                state: extraState
            })
        });
    }

    function copyLink(id) {
        const link = `https://case-clicker.com/api/openedSkin/${id}`;
        navigator.clipboard.writeText(link).then(() => {
            console.log("Copied:", link);
        });
    }

    function _fallbackCopy(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.setAttribute('readonly', '');
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, text.length);
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        document.body.removeChild(ta);
        return ok;
    }

    async function copyWorkerLink() {
        const btn = document.getElementById('copyWorkerLinkBtn');
        if (!btn) return;
        const original = btn.textContent;
        let link = null;
        try {
            const res = await fetch('/config', { cache: 'no-cache' });
            const cfg = await res.json();
            link = `${cfg.base_url}#${cfg.token}`;
        } catch (e) {
            btn.textContent = 'NETWORK ERROR';
            setTimeout(() => { btn.textContent = original; }, 1800);
            return;
        }

        let copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(link);
                copied = true;
            }
        } catch (e) { /* fall through to execCommand */ }
        if (!copied) copied = _fallbackCopy(link);

        if (copied) {
            btn.textContent = 'COPIED!';
            btn.classList.add('copied');
            setTimeout(() => {
                btn.textContent = original;
                btn.classList.remove('copied');
            }, 1400);
        } else {
            // Last resort: show the URL so the user can copy manually.
            window.prompt('Copy the worker link below:', link);
            btn.textContent = original;
        }
    }

    async function checkForUpdate() {
        const btn = document.getElementById('checkUpdateBtn');
        if (!btn) return;
        const original = btn.textContent;
        btn.textContent = 'CHECKING...';
        btn.disabled = true;
        try {
            const res = await fetch('/api/check_update', { method: 'POST' });
            const data = await res.json();
            if (!data.ok) {
                btn.textContent = 'CHECK FAILED';
                setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
                window.alert('Update check failed: ' + (data.error || 'unknown'));
                return;
            }
            if (!data.update_available) {
                btn.textContent = `UP TO DATE (${data.current})`;
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = original;
                    btn.classList.remove('copied');
                    btn.disabled = false;
                }, 1800);
                return;
            }
            if (!data.has_installer) {
                btn.textContent = 'NO INSTALLER FOR YOUR OS';
                setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2200);
                return;
            }
            const go = confirm(`New version available: ${data.latest}\n(you have ${data.current})\n\nDownload and install now?`);
            if (!go) {
                btn.textContent = original;
                btn.disabled = false;
                return;
            }
            btn.textContent = 'DOWNLOADING...';
            const applyRes = await fetch('/api/apply_update', { method: 'POST' });
            const applyData = await applyRes.json();
            if (applyData.ok) {
                btn.textContent = 'INSTALLER LAUNCHED';
                // App will exit shortly; no need to re-enable button.
            } else {
                btn.textContent = 'UPDATE FAILED';
                setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2200);
            }
        } catch (e) {
            btn.textContent = 'CHECK FAILED';
            setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1800);
        }
    }

    function setTab(t) {
        document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
        document.querySelectorAll('.content').forEach(x => x.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById(`view-${t}`).classList.add('active');
        
        if(t === 'conv') {
            renderConvertTab();
            requestCaseSummary(false);
        }
        if(t === 'boost') {
            renderBoosterTab();
        }
        if(t === 'trans') {
            renderTransferTab();
            requestCaseSummary(false);
        }
    }

    function renderTransferTab() {
        const acc = accounts.find(a => a.id === selectedId);
        if(!acc) return;
        const tokenWrap = document.getElementById('tokenAmountWrap');
        if(tokenWrap) tokenWrap.style.display = transferState.tok ? 'block' : 'none';
        const availEl = document.getElementById('tokenAvailable');
        if(availEl) {
            const tokens = Math.floor(Number(acc.stats?.tokens || 0));
            availEl.innerText = `Available: ${tokens.toLocaleString()}`;
        }
        renderConvertCaseSummaryState(acc);
    }

    function setMaxTokens() {
        const acc = accounts.find(a => a.id === selectedId);
        if(!acc || !acc.stats) return;
        const maxTokens = Math.floor(Number(acc.stats.tokens || 0));
        const input = document.getElementById('tokenAmountInput');
        if(input) input.value = maxTokens;
    }

    function renderConvertCaseSummaryState(acc) {
        if(!acc) return;
        const summary = acc.case_summary || {};
        const units = Number(summary.total_amount || 0);
        const value = Number(summary.total_value || 0);
        const sellValue = Number(summary.total_value_70 || 0);
        const updatedAt = Number(summary.updated_at || 0);
        const updatedText = updatedAt > 0 ? `Updated ${new Date(updatedAt * 1000).toLocaleTimeString()}` : 'Not scanned yet';
        const unitsEl = document.getElementById('transCaseUnits');
        const valueEl = document.getElementById('transCaseValue');
        const sellValueEl = document.getElementById('transCaseSellValue');
        const updatedEl = document.getElementById('transCaseUpdated');
        if(unitsEl) unitsEl.innerText = units.toLocaleString();
        if(valueEl) valueEl.innerText = `$${Math.floor(value).toLocaleString()}`;
        if(sellValueEl) sellValueEl.innerText = `$${Math.floor(sellValue).toLocaleString()}`;
        if(updatedEl) updatedEl.innerText = updatedText;

        const log = (acc.status_log || '');
        if (transferSelling) {
            if (log.includes('CASE_SELL_DONE')) {
                transferSelling = false;
                transferSellState = 'done';
                requestCaseSummary(true);
                if (sellDoneResetTimer) clearTimeout(sellDoneResetTimer);
                sellDoneResetTimer = setTimeout(function() {
                    transferSellState = 'idle';
                    sellDoneResetTimer = null;
                    const el = document.getElementById('sellCasesLoader');
                    if (el) el.style.display = 'none';
                }, 5000);
            } else if (log.includes('CASE_SELL_ERROR')) {
                transferSelling = false;
                transferSellState = 'error';
                if (sellDoneResetTimer) clearTimeout(sellDoneResetTimer);
                sellDoneResetTimer = setTimeout(function() {
                    transferSellState = 'idle';
                    sellDoneResetTimer = null;
                    const el = document.getElementById('sellCasesLoader');
                    if (el) el.style.display = 'none';
                }, 5000);
            }
        }
        const loader = document.getElementById('sellCasesLoader');
        const loaderBar = document.getElementById('sellCasesLoaderBar');
        const loaderText = document.getElementById('sellCasesLoaderText');
        const sellBtn = document.getElementById('btnSellCases');
        if(loader) loader.style.display = transferSellState === 'idle' ? 'none' : 'block';
        if(loaderBar && loaderText) {
            if (transferSellState === 'loading') {
                loaderBar.style.width = '35%';
                loaderBar.style.background = 'linear-gradient(90deg, var(--accent), #22d3ee)';
                loaderBar.style.animation = 'pulse 1.1s ease-in-out infinite';
                loaderText.style.color = '#bbb';
                loaderText.innerText = 'Selling snapshot of current cases...';
            } else if (transferSellState === 'done') {
                loaderBar.style.width = '100%';
                loaderBar.style.background = 'linear-gradient(90deg, #22c55e, #86efac)';
                loaderBar.style.animation = 'none';
                loaderText.style.color = '#86efac';
                loaderText.innerText = 'Done. Cases sold successfully.';
            } else if (transferSellState === 'error') {
                loaderBar.style.width = '100%';
                loaderBar.style.background = 'linear-gradient(90deg, #ef4444, #fca5a5)';
                loaderBar.style.animation = 'none';
                loaderText.style.color = '#fca5a5';
                loaderText.innerText = 'Error while selling cases. Try again.';
            }
        }
        if(sellBtn) {
            sellBtn.disabled = transferSelling;
            sellBtn.style.opacity = transferSelling ? '0.65' : '1';
            sellBtn.style.cursor = transferSelling ? 'not-allowed' : 'pointer';
            sellBtn.innerText = transferSelling ? 'SELLING...' : 'SELL CASES';
        }
    }

    async function requestCaseSummary(force = false) {
        if(!selectedId) return;
        const now = Date.now();
        if(!force && now - transferSummaryRequestedAt < 12000) return;
        transferSummaryRequestedAt = now;
        await fetch('/api/queue/scan_case_summary', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({acc_id: selectedId})
        });
    }

    async function sellCasesSnapshot() {
        if(!selectedId) return;
        if(transferSelling) return;
        if (sellDoneResetTimer) { clearTimeout(sellDoneResetTimer); sellDoneResetTimer = null; }
        transferSellState = 'loading';
        transferSelling = true;
        renderTransferTab();
        await fetch('/api/queue/sell_cases', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({acc_id: selectedId})
        });
    }
    
    function renderConvertTab() {
        // Update balance
        const acc = accounts.find(a => a.id === selectedId);
        if(acc && acc.stats) {
            document.getElementById('convBalance').innerText = Math.floor(acc.stats.money || 0).toLocaleString();
        }
        renderConvertCaseSummaryState(acc);
        
        // Update convert job status
        const jobStatus = document.getElementById('convertJobStatus');
        if(acc && acc.convert_job && (acc.convert_job.active || acc.convert_job.error || acc.convert_job.done)) {
            jobStatus.style.display = 'block';
            const job = acc.convert_job;
            document.getElementById('jobCaseName').innerText = job.caseName;
            const sellMethodText = job.sellMethod === 'tokens' ? 'Tokens' : 'Money';
            document.getElementById('jobSellMethod').innerText = sellMethodText;
            document.getElementById('jobOpened').innerText = (job.opened || 0).toLocaleString();
            document.getElementById('jobRemaining').innerText = (job.remaining || 0).toLocaleString();
            document.getElementById('jobTotal').innerText = (job.total || 0).toLocaleString();
            document.getElementById('jobProgressText').innerText = `${job.progress || 0}%`;

            const opened = Number(job.opened) || 0;
            const casePrice = Number(job.casePrice) || 0;
            const spent = opened * casePrice;
            const stats = acc.stats || {};
            const tokensNow = Number(stats.tokens) || 0;
            const moneyNow = Number(stats.money) || 0;
            const tokensStart = Number(job.tokens_start);
            const moneyStart = Number(job.money_start);
            const isTokens = job.sellMethod === 'tokens';
            let earnedText = '-';
            let roiText = '-';
            if (isTokens && !isNaN(tokensStart)) {
                const earnedT = Math.max(0, tokensNow - tokensStart);
                earnedText = earnedT.toLocaleString() + ' T';
                roiText = '—';
            } else if (!isTokens && !isNaN(moneyStart)) {
                const earnedM = moneyNow - moneyStart;
                earnedText = '$' + Math.floor(earnedM).toLocaleString();
                roiText = spent > 0 ? ((earnedM / spent) * 100).toFixed(1) + '%' : '-';
            }
            document.getElementById('jobSpent').innerText = '$' + Math.floor(spent).toLocaleString();
            document.getElementById('jobEarned').innerText = earnedText;
            document.getElementById('jobRoi').innerText = roiText;

            // Update batch info
            const batchSize = job.batchSize || 0;
            const multiplier = job.multiplier || 0;
            if(job.error) {
                document.getElementById('jobBatchInfo').innerText = job.error;
            } else if(batchSize > 0 && multiplier > 0) {
                document.getElementById('jobBatchInfo').innerText = `${batchSize}x${multiplier}`;
            } else {
                document.getElementById('jobBatchInfo').innerText = '-';
            }

            const progressBar = document.getElementById('jobProgressBar');
            progressBar.style.width = `${job.progress || 0}%`;
            if(job.error) {
                progressBar.style.width = `100%`;
                progressBar.innerText = `Error`;
                progressBar.style.background = 'linear-gradient(90deg, #ef4444 0%, #fca5a5 100%)';
            } else if(job.progress >= 100) {
                progressBar.innerText = 'Complete!';
                progressBar.style.background = 'linear-gradient(90deg, #0f0 0%, var(--accent) 100%)';
            } else {
                progressBar.innerText = `${job.progress}%`;
                progressBar.style.background = 'linear-gradient(90deg, var(--accent) 0%, #0f0 100%)';
            }
        } else {
            jobStatus.style.display = 'none';
        }
        
        filterCases();
    }
    
    function filterCases() {
        const searchTerm = (document.getElementById('caseSearch')?.value || '').toLowerCase();
        const casesList = document.getElementById('casesList');
        casesList.innerHTML = '';
        
        // Sort cases by last_used (most recent first), then by name
        const sortedCases = [...cases].sort((a, b) => {
            const aLastUsed = a.last_used || 0;
            const bLastUsed = b.last_used || 0;
            if(bLastUsed !== aLastUsed) {
                return bLastUsed - aLastUsed; // Most recent first
            }
            return (a.name || '').localeCompare(b.name || '');
        });
        
        // Filter and render cases
        const filteredCases = sortedCases.filter(c => {
            if(!searchTerm) return true;
            const name = (c.name || '').toLowerCase();
            const type = (c.type || '').toLowerCase();
            return name.includes(searchTerm) || type.includes(searchTerm);
        });
        
        filteredCases.forEach(c => {
            const div = document.createElement('div');
            div.style = "padding:10px; background:#111; border:1px solid var(--border); border-radius:6px; cursor:pointer; transition:0.2s;";
            div.onmouseover = () => div.style.borderColor = "#555";
            div.onmouseout = () => {
                if(selectedCase && selectedCase._id === c._id) return;
                div.style.borderColor = "var(--border)";
            };
            div.onclick = () => selectCase(c);
            
            if(selectedCase && selectedCase._id === c._id) {
                div.style.borderColor = "var(--accent)";
                div.style.background = "rgba(16, 185, 129, 0.1)";
            }
            
            const isRecent = c.last_used && (Date.now() / 1000 - c.last_used) < 86400; // Used in last 24 hours
            div.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="flex:1;">
                        <div style="font-weight:700; color:#fff; font-size:12px; display:flex; align-items:center; gap:6px;">
                            ${c.name}
                            ${isRecent ? '<span style="background:var(--accent); color:#000; font-size:8px; padding:2px 4px; border-radius:3px; font-weight:800;">RECENT</span>' : ''}
                        </div>
                        <div style="font-size:10px; color:#888; margin-top:2px;">${c.type === 'case' ? 'Case' : 'Capsule'}</div>
                    </div>
                    <div style="font-family:'JetBrains Mono'; color:var(--accent); font-weight:700; font-size:12px;">$${c.price}</div>
                </div>
            `;
            casesList.appendChild(div);
        });
        
        if(filteredCases.length === 0) {
            casesList.innerHTML = '<div style="text-align:center; color:#555; padding:20px;">No cases found</div>';
        }
    }
    
    function selectCase(caseData) {
        selectedCase = caseData;
        document.getElementById('selectedCaseInfo').style.display = 'block';
        document.getElementById('selectedCaseName').innerText = caseData.name;
        document.getElementById('selectedCasePrice').innerText = caseData.price;
        updateCaseCount();
        checkConfirmEnabled();
        renderConvertTab();
    }
    
    function setSellMethod(method) {
        sellMethod = method;
        const money = document.getElementById('btnSellMoney');
        const tokens = document.getElementById('btnSellTokens');
        money.style.background = method === 'money' ? 'var(--accent)' : 'var(--bg-elev)';
        money.style.color = method === 'money' ? '#04110a' : '#fff';
        money.style.borderColor = method === 'money' ? 'var(--accent)' : 'var(--border-soft)';
        tokens.style.background = method === 'tokens' ? 'var(--warn)' : 'var(--bg-elev)';
        tokens.style.color = method === 'tokens' ? '#111' : '#fff';
        tokens.style.borderColor = method === 'tokens' ? 'var(--warn)' : 'var(--border-soft)';
        checkConfirmEnabled();
    }
    
    function setMaxBudget() {
        const acc = accounts.find(a => a.id === selectedId);
        if(acc && acc.stats) {
            const maxMoney = acc.stats.money || 0;
            document.getElementById('convBudget').value = Math.floor(maxMoney);
            convertBudget = Math.floor(maxMoney);
            updateCaseCount();
            checkConfirmEnabled();
        }
    }
    
    function updateCaseCount() {
        const budgetInput = document.getElementById('convBudget');
        const budget = parseFloat(budgetInput.value) || 0;
        convertBudget = budget;
        
        if(selectedCase && budget > 0) {
            const caseCount = Math.floor(budget / selectedCase.price);
            document.getElementById('convCaseCount').innerText = `Estimated: ${caseCount} cases ($${(caseCount * selectedCase.price).toFixed(2)})`;
        } else {
            document.getElementById('convCaseCount').innerText = 'Estimated: 0 cases';
        }
        
        // Check if confirm button should be enabled
        checkConfirmEnabled();
    }
    
    function checkConfirmEnabled() {
        const btn = document.getElementById('btnConfirmConvert');
        if(selectedCase && sellMethod && convertBudget > 0) {
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.style.cursor = 'pointer';
        } else {
            btn.disabled = true;
            btn.style.opacity = '0.5';
            btn.style.cursor = 'not-allowed';
        }
    }
    
    async function confirmConvert() {
        if(!selectedCase || !sellMethod || !convertBudget) return;
        
        loadStart();
        await fetch('/api/queue/convert', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                acc_id: selectedId,
                case_id: selectedCase._id,
                budget: convertBudget,
                sell_method: sellMethod
            })
        });
        loadEnd();
        
        // Reset form
        selectedCase = null;
        sellMethod = null;
        convertBudget = 0;
        document.getElementById('selectedCaseInfo').style.display = 'none';
        document.getElementById('convBudget').value = '';
        document.getElementById('caseSearch').value = '';
        document.getElementById('btnSellMoney').style.background = 'var(--bg-elev)';
        document.getElementById('btnSellMoney').style.color = '#fff';
        document.getElementById('btnSellMoney').style.borderColor = 'var(--border-soft)';
        document.getElementById('btnSellTokens').style.background = 'var(--bg-elev)';
        document.getElementById('btnSellTokens').style.color = '#fff';
        document.getElementById('btnSellTokens').style.borderColor = 'var(--border-soft)';
        checkConfirmEnabled();
        renderConvertTab();
    }

    function renderBoosterTab() {
        const acc = accounts.find(a => a.id === selectedId);
        if(!acc) return;
        const profile = acc.profile || {};
        const stats = acc.stats || {};
        const rankName = profile.rankName || stats?.premierRank?.name || stats?.premierRank?.title || 'Unknown';
        const rankImageRaw = profile.rankImage || stats?.premierRank?.image || stats?.premierRank?.img || 'https://case-clicker.com/img/unknown.png';
        const rankImage = (rankImageRaw && rankImageRaw.startsWith('/')) ? `https://case-clicker.com${rankImageRaw}` : rankImageRaw;
        const createdAt = profile.createdAt ? new Date(profile.createdAt).toLocaleString() : 'Unknown';
        document.getElementById('boosterRankName').innerText = rankName;
        const rankImgEl = document.getElementById('boosterRankImage');
        if (rankImgEl && rankImgEl.src !== rankImage) rankImgEl.src = rankImage;
        document.getElementById('boosterCreatedAt').innerText = createdAt;

        const select = document.getElementById('boosterCaseSelect');
        if(select && select.options.length === 0) {
            const sorted = [...cases].sort((a,b) => (b.last_used || 0) - (a.last_used || 0));
            select.innerHTML = sorted.map(c => `<option value="${c._id}">${c.name} ($${c.price})</option>`).join('');
            if(sorted.length > 0) selectedBoosterCaseId = sorted[0]._id;
            select.onchange = () => selectedBoosterCaseId = select.value;
        }
    }

    async function startBooster() {
        const caseId = selectedBoosterCaseId || document.getElementById('boosterCaseSelect')?.value;
        if(!caseId) return alert('Select a case first');
        const clickUntilBoost = !!document.getElementById('boosterClickUntil')?.checked;
        await fetch('/api/queue/booster', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                acc_id: selectedId,
                case_id: caseId,
                click_until_boost: clickUntilBoost
            })
        });
        alert('Booster started.');
    }

    function toggleT(key) {
        transferState[key] = !transferState[key];
        const el = document.getElementById(key === 'tok' ? 'chkTok' : 'chkSkin');
        const ind = document.getElementById(key === 'tok' ? 'indTok' : 'indSkin');
        if(transferState[key]) {
            el.classList.add('selected');
            ind.innerText = '✓';
        } else {
            el.classList.remove('selected');
            ind.innerText = '';
        }
        renderTransferTab();
    }

    function toggleActiveJobs() {
        const dropdown = document.getElementById('activeJobsDropdown');
        dropdown.classList.toggle('show');
    }
    
    function updateActiveJobs() {
        const activeJobs = [];
        accounts.forEach(acc => {
            if(acc.convert_job && (acc.convert_job.active || acc.convert_job.done)) {
                activeJobs.push({ account: acc, job: acc.convert_job, jobType: 'convert' });
            }
            if(acc.booster_job && (acc.booster_job.active || acc.booster_job.done || acc.booster_job.alert)) {
                activeJobs.push({ account: acc, job: acc.booster_job, jobType: 'booster' });
            }
        });

        const alertCount = activeJobs.filter(j => j.job.alert).length;
        const alertBadge = document.getElementById('activeJobsAlertBadge');
        if(alertBadge) {
            alertBadge.innerText = alertCount > 0 ? `!${alertCount}` : '0';
            alertBadge.style.display = alertCount > 0 ? 'inline-block' : 'none';
            alertBadge.dataset.zero = alertCount > 0 ? '0' : '1';
        }

        const badge = document.getElementById('activeJobsBadge');
        const activeCount = activeJobs.filter(j => j.job.active).length;
        badge.innerText = activeCount > 0 ? activeCount : '';
        badge.dataset.zero = activeCount > 0 ? '0' : '1';

        const btn = document.querySelector('.active-jobs-btn');
        if (btn) {
            btn.classList.toggle('has-active', activeCount > 0);
            btn.classList.toggle('has-alert', alertCount > 0);
        }
        
        const list = document.getElementById('activeJobsList');
        if(activeJobs.length === 0) {
            list.innerHTML = '<div style="padding:20px; text-align:center; color:#666; font-size:12px;">No active jobs</div>';
            return;
        }
        
        list.innerHTML = activeJobs.map(({account, job, jobType}) => {
            const isDone = job.progress >= 100 || job.done;
            const isAlert = !!job.alert;
            const jobTypeLabel = jobType === 'booster' ? 'Booster' : (job.sellMethod === 'tokens' ? 'Convert to Tokens' : 'Convert to Money');
            const progress = job.progress || 0;
            const opened = job.opened || 0;
            const total = job.total || 0;
            const remaining = job.remaining || 0;
            const batchSize = job.batchSize || 0;
            const multiplier = job.multiplier || 0;
            const batchInfo = batchSize > 0 && multiplier > 0 ? `${batchSize}x${multiplier}` : '-';
            const warningText = isAlert ? (job.alert_text || 'Process stopped') : '';
            
            return `
                <div class="active-job-item ${isDone ? 'done' : ''}" data-account-id="${account.id}">
                    <div class="active-job-header">
                        <div>
                            <div class="active-job-user">${account.username || account.id}</div>
                            <div class="active-job-type">${jobTypeLabel}: ${job.caseName || 'Unknown'} ${isAlert ? '⚠' : ''}</div>
                        </div>
                        <button class="active-job-close" onclick="dismissJob('${account.id}', '${jobType}')">×</button>
                    </div>
                    ${isAlert ? `<div style="color:#ef4444; font-size:11px; margin-bottom:8px;">${warningText}</div>` : ''}
                    <div class="active-job-progress">
                        <div class="active-job-progress-bar">
                            <div class="active-job-progress-fill" style="width: ${progress}%">
                                ${progress >= 100 ? 'DONE' : `${progress}%`}
                            </div>
                        </div>
                        <div class="active-job-stats">
                            <span>Opened: ${opened.toLocaleString()}</span>
                            <span>Remaining: ${remaining.toLocaleString()}</span>
                            <span>Total: ${total.toLocaleString()}</span>
                            <span>Opening: ${batchInfo}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    async function dismissJob(accountId, jobType = 'convert') {
        const res = await fetch('/api/job/dismiss', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({accountId: accountId, jobType: jobType})
        });
        if(res.ok) {
            updateActiveJobs();
        }
    }
    
    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('activeJobsDropdown');
        const btn = document.querySelector('.active-jobs-btn');
        if(dropdown && btn && !dropdown.contains(e.target) && !btn.contains(e.target)) {
            dropdown.classList.remove('show');
        }
    });

    async function runTransfer() {
        if(!transferState.tok && !transferState.skin) return alert("Select at least one option");
        let tokenAmount = null;
        if(transferState.tok) {
            const raw = document.getElementById('tokenAmountInput')?.value;
            if(raw && String(raw).trim() !== '') {
                tokenAmount = parseInt(raw, 10);
                if(!Number.isFinite(tokenAmount) || tokenAmount <= 0) {
                    return alert("Token amount must be greater than 0");
                }
            }
        }
        
        document.getElementById('termLogs').innerHTML = '';
        document.getElementById('tradeLink').classList.add('hidden');
        lastLog = "";
        document.getElementById('logModal').style.display = 'flex';
        
        await fetch('/api/queue/transfer', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                sender_id: selectedId,
                send_tokens: transferState.tok,
                send_skins: transferState.skin,
                token_amount: tokenAmount
            })
        });
    }

    async function saveSettings() {
        loadStart();
        mainId = document.getElementById('mainId').value;
        await fetch('/api/settings', {
            method: 'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({main_id: mainId})
        });
        const remoteUrl = (document.getElementById('remoteUrl').value || "").trim();
        const remoteRes = await fetch('/api/remote_url', {
            method: 'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({url: remoteUrl})
        });
        try {
            const remoteData = await remoteRes.json();
            if (remoteData && typeof remoteData.url === 'string') {
                document.getElementById('remoteUrl').value = remoteData.url;
            }
        } catch (_) {}
        loadEnd();
        renderSidebar();
    }

    async function deleteAcc() {
        if(confirm("Unlink account?")) {
            await fetch('/api/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:selectedId})});
            location.reload();
        }
    }
    
    function closeModal() { document.getElementById('logModal').style.display = 'none'; }

    async function refreshInventory() {
        if(!selectedId) return;
        loadStart();
        await fetch('/api/queue/scan_inventory', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({acc_id: selectedId})
        });
        loadEnd();
        // Refresh the view after a short delay to show updated inventory
        setTimeout(() => {
            renderInventory(selectedId);
        }, 2000);
    }

    init();
</script>
</div>
</body>
</html>
"""

def run_server(host: str = HOST, port: int = PORT) -> None:
    print(f"--- Case Clicker Hub {__version__} listening on http://{host}:{port} ---")
    app.run(
        host=host,
        port=port,
        threaded=True,
        use_reloader=False,
    )


if __name__ == '__main__':
    run_server()