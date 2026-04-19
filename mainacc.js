// ==UserScript==
// @name         CC Hub Main Worker
// @namespace    https://github.com/Mikmail02/Alt-manager
// @version      1.1.0
// @description  Main worker for Case Clicker Hub desktop app
// @author       Mikmail
// @match        *://*.case-clicker.com/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_setClipboard
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// @connect      *
// @updateURL    https://raw.githubusercontent.com/Mikmail02/Alt-manager/main/mainacc.js
// @downloadURL  https://raw.githubusercontent.com/Mikmail02/Alt-manager/main/mainacc.js
// @supportURL   https://github.com/Mikmail02/Alt-manager/issues
// ==/UserScript==

(function() {
    'use strict';

    let inventoryCache = [];
    let isScanning = false;
    let cachedProfileImage = null;
    let cachedProfileMeta = null;
    let heartbeatTimer = null;
    let heartbeatInFlight = false;
    let statusFetchInFlight = false;
    let statusLastFetchAt = 0;
    let statusCasePriceMap = null;
    let statusCasePriceMapAt = 0;
    let isConnected = false;
    let connState = 'idle'; // idle | connecting | connected | reconnecting
    let activeBaseUrl = '';
    let cachedMe = null;
    let connectedSince = 0;
    let lastHeartbeatSuccess = 0;
    let totalErrors = 0;
    let heartbeatBackoffMs = 8000;
    const HEARTBEAT_INTERVAL_MS = 8000;
    const HEARTBEAT_BACKOFF_MAX_MS = 120000;
    const HEARTBEAT_REQUEST_TIMEOUT_MS = 15000;
    const REFRESH_GRACE_MS = 90000;
    const STATUS_PANEL_TICK_MS = 1000;
    const STATUS_NETWORK_MS = 20000;
    const STATUS_CASE_CACHE_MS = 300000;
    const MAX_LOG_LINES = 250;

    // Integrated clicker (recursive setTimeout + visual countdown)
    let clickTimer = null;
    let countdownTimer = null;
    let clickReqCount = 0;
    const CLICK_INTERVAL_MS = 60100;
    const CLICKS_PER_REQ = 500;
    const VAULT_EVERY_N_REQ = 1;
    const CLICK_KEY = 'am.click.enabled.main';
    const VAULT_KEY = 'am.vault.enabled.main';
    const NUKE_KEY = 'am.nuke.enabled.main';
    let nextClickAt = 0;
    let statusUiInterval = null;

    // Networth tracker
    let nwTimer = null;
    let lastNW = null;
    let lastNWT = null;
    const NW_POLL_MS = 60000;

    // --- 1. SOCKET TRAP ---
    function injectTrap() {
        const script = document.createElement('script');
        script.textContent = `
            (function() {
                if (window.__CC_WS_TRAP_INSTALLED__) return;
                window.__CC_WS_TRAP_INSTALLED__ = true;
                const OriginalWebSocket = window.WebSocket;
                window.WebSocket = function(...args) {
                    const socket = new OriginalWebSocket(...args);
                    window.__CC_SOCKETS__ = window.__CC_SOCKETS__ || [];
                    window.__CC_SOCKETS__.push(socket);
                    if (window.__CC_SOCKETS__.length > 20) window.__CC_SOCKETS__.shift();
                    window.__CC_SOCKET__ = socket;
                    socket.addEventListener('open', () => { window.__CC_SOCKET__ = socket; });
                    socket.addEventListener('close', () => {
                        if (window.__CC_SOCKET__ === socket) window.__CC_SOCKET__ = null;
                    });
                    return socket;
                };
                window.WebSocket.prototype = OriginalWebSocket.prototype;
                window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
                window.WebSocket.OPEN = OriginalWebSocket.OPEN;
                window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
                window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;
            })();
        `;
        (document.head || document.documentElement).appendChild(script);
    }
    injectTrap();

    // --- 2. UI & INIT ---
    window.addEventListener('load', () => {
        initUI();
        setTimeout(scanInventory, 3000);
        // Auto-reconnect if we had a connected session before a reload.
        const savedUrl = GM_getValue('ngrok_url', '');
        const autoOn = GM_getValue('am.autoconnect.main', false);
        if (autoOn && savedUrl && cchubToken()) {
            setTimeout(() => startLoop(savedUrl), 500);
        }
    });

    function initUI() {
        if (!document.getElementById('am-ui-style')) {
            const st = document.createElement('style');
            st.id = 'am-ui-style';
            st.textContent = `
                body.am-nuked > *:not(#alt-worker-ui) { display: none !important; }
                #alt-worker-ui, #alt-worker-ui * { box-sizing: border-box; }
                #alt-worker-ui { font-family: 'Inter', system-ui, -apple-system, sans-serif; }
                #alt-worker-ui .aw-card { background: rgba(28,26,16,0.6); border: 1px solid rgba(124,111,58,0.4); border-radius: 10px; padding: 10px; backdrop-filter: blur(8px); }
                #alt-worker-ui .aw-row { display: flex; gap: 8px; }
                #alt-worker-ui .aw-input { flex: 1; padding: 9px 11px; background: rgba(16,16,8,0.8); border: 1px solid #3c3620; color: #f3e8b5; border-radius: 8px; font-size: 12px; outline: none; transition: border-color .15s; }
                #alt-worker-ui .aw-input:focus { border-color: #f59e0b; }
                #alt-worker-ui .aw-btn { padding: 9px 14px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 12px; transition: filter .15s, transform .05s; letter-spacing: .02em; }
                #alt-worker-ui .aw-btn:hover { filter: brightness(1.1); }
                #alt-worker-ui .aw-btn:active { transform: translateY(1px); }
                #alt-worker-ui .aw-btn-primary { background: #f59e0b; color: #1a1200; }
                #alt-worker-ui .aw-btn-secondary { background: #2a2616; color: #f3e8b5; border: 1px solid #4a4224; }
                #alt-worker-ui .aw-pill { display:flex; align-items:center; justify-content:center; gap:6px; min-width: 130px; padding: 0 12px; background: rgba(16,16,8,0.6); border: 1px solid #3c3620; border-radius: 8px; font-weight: 700; font-size: 11px; letter-spacing: .05em; }
                #alt-worker-ui .aw-toggles { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
                #alt-worker-ui .aw-toggle { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; background: rgba(16,16,8,0.5); border: 1px solid #3c3620; border-radius: 8px; cursor: pointer; font-size: 11px; color: #ddd7b8; user-select: none; }
                #alt-worker-ui .aw-toggle:hover { border-color: #6b5d2d; }
                #alt-worker-ui .aw-switch { position: relative; width: 28px; height: 16px; background: #4a4224; border-radius: 8px; transition: background .18s; flex-shrink: 0; }
                #alt-worker-ui .aw-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; background: #f3e8b5; border-radius: 50%; transition: left .18s; }
                #alt-worker-ui .aw-toggle input { display: none; }
                #alt-worker-ui .aw-toggle input:checked ~ .aw-switch { background: #f59e0b; }
                #alt-worker-ui .aw-toggle input:checked ~ .aw-switch::after { left: 14px; background: #1a1200; }
                #alt-worker-ui .aw-stat-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 11.5px; }
                #alt-worker-ui .aw-stat-row span:first-child { color: #a39871; }
                #alt-worker-ui .aw-stat-row span:last-child { color: #f3e8b5; font-weight: 600; font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono', ui-monospace, monospace; }
                #alt-worker-ui .aw-progress-track { margin-top: 8px; height: 6px; background: rgba(16,16,8,0.6); border-radius: 3px; overflow: hidden; border: 1px solid #3c3620; }
                #alt-worker-ui .aw-progress-bar { height: 100%; width: 0%; background: linear-gradient(90deg, #f59e0b, #ffd700); border-radius: 3px; transition: width .15s linear; }
                #alt-worker-ui .aw-divider { height: 1px; background: linear-gradient(90deg, transparent, #3c3620, transparent); margin: 10px 0; }
                #alt-worker-ui #status::-webkit-scrollbar { width: 6px; }
                #alt-worker-ui #status::-webkit-scrollbar-track { background: transparent; }
                #alt-worker-ui #status::-webkit-scrollbar-thumb { background: #4a4224; border-radius: 3px; }
            `;
            document.documentElement.appendChild(st);
        }

        const div = document.createElement('div');
        div.id = 'alt-worker-ui';
        div.style = "position:fixed; bottom:16px; right:16px; background: linear-gradient(145deg, rgba(16,16,8,0.95), rgba(28,26,16,0.95)); color:#f3e8b5; padding:0; border-radius:14px; z-index:2147483647; width: 460px; border:1px solid rgba(124,111,58,0.5); font-size:12px; box-shadow: 0 20px 60px rgba(0,0,0,0.75), 0 0 0 1px rgba(255,215,0,0.05) inset; display:flex; flex-direction:column; overflow:hidden; backdrop-filter: blur(12px);";

        div.innerHTML = `
            <div id="drag-handle" style="padding: 12px 14px; background: linear-gradient(180deg, rgba(255,215,0,0.08), transparent); border-bottom: 1px solid rgba(124,111,58,0.4); cursor: move; display: flex; align-items: center; gap: 10px; user-select: none;">
                <div id="aw-dot" style="width: 8px; height: 8px; border-radius: 50%; background: #6b7280; box-shadow: 0 0 8px rgba(107,114,128,0.5); transition: all .2s;"></div>
                <div style="font-weight: 700; color: #ffd700; font-size: 13px; letter-spacing: .02em; flex: 1;">Main Manager Worker</div>
                <div style="font-size: 10px; color: #8b7d45; font-variant-numeric: tabular-nums;">v1.0.9</div>
            </div>
            <div style="padding: 12px;">
                <div class="aw-row" style="margin-bottom: 8px;">
                    <input id="ngrok_url" class="aw-input" placeholder="Paste link from CC Hub tray > Copy worker link">
                    <button id="btn_link" class="aw-btn aw-btn-primary" style="min-width: 130px;">CONNECT MAIN</button>
                </div>
                <div class="aw-row" style="margin-bottom: 10px;">
                    <button id="btn_ping" class="aw-btn aw-btn-secondary" style="flex: 1;">TEST LINK</button>
                    <span id="conn_state" class="aw-pill" style="color: #a39871;">DISCONNECTED</span>
                </div>
                <div class="aw-toggles">
                    <label class="aw-toggle"><span>Auto Click</span><input id="opt_click" type="checkbox"><span class="aw-switch"></span></label>
                    <label class="aw-toggle"><span>Auto Vault</span><input id="opt_vault" type="checkbox"><span class="aw-switch"></span></label>
                    <label class="aw-toggle"><span>Nuke UI</span><input id="opt_nuke" type="checkbox"><span class="aw-switch"></span></label>
                </div>
                <div class="aw-divider"></div>
                <div class="aw-card" style="margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span id="aw-status-text" style="color: #f3e8b5; font-weight: 600; font-size: 12px;">Idle</span>
                        <span id="st_timer" style="color: #ffd700; font-weight: 700; font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px;">--</span>
                    </div>
                    <div class="aw-progress-track"><div id="aw-progress" class="aw-progress-bar"></div></div>
                </div>
                <div class="aw-card" style="margin-bottom: 8px;">
                    <div class="aw-stat-row"><span>Total Clicks</span><span id="st_clicks">0</span></div>
                    <div class="aw-stat-row"><span>Effective CPS</span><span id="st_cps">0.00</span></div>
                    <div class="aw-stat-row"><span>Networth</span><span id="st_nw">-</span></div>
                    <div class="aw-stat-row"><span>Change (1m)</span><span id="st_delta" style="color:#8b7d45!important;">-</span></div>
                    <div class="aw-stat-row"><span>Hourly Rate</span><span id="st_hourly" style="color:#8b7d45!important;">-</span></div>
                    <div class="aw-stat-row"><span>Cases Sell (70%)</span><span id="st_case_val">$0</span></div>
                    <div class="aw-stat-row"><span>Errors</span><span id="st_errors">0</span></div>
                </div>
                <div id="status" style="color:#a39871; background: rgba(16,16,8,0.4); border: 1px solid #3c3620; border-radius: 8px; padding: 8px 10px; height: 100px; overflow-y: auto; line-height: 1.5; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 10.5px;">Waiting...</div>
            </div>
        `;
        document.body.appendChild(div);

        const dragHandle = div.querySelector('#drag-handle');
        let isDragging = false;
        let startX, startY, initialLeft, initialTop;
        dragHandle.addEventListener('mousedown', (e) => {
            isDragging = true; startX = e.clientX; startY = e.clientY;
            const rect = div.getBoundingClientRect(); initialLeft = rect.left; initialTop = rect.top;
            div.style.bottom = 'auto'; div.style.right = 'auto'; div.style.left = initialLeft + 'px'; div.style.top = initialTop + 'px';
            dragHandle.style.cursor = 'grabbing';
        });
        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            div.style.left = (initialLeft + (e.clientX - startX)) + 'px';
            div.style.top = (initialTop + (e.clientY - startY)) + 'px';
        });
        window.addEventListener('mouseup', () => { isDragging = false; dragHandle.style.cursor = 'move'; });

        const savedUrl = GM_getValue('ngrok_url', DEFAULT_BACKEND_URL);
        document.getElementById('ngrok_url').value = savedUrl;
        const clickCb = document.getElementById('opt_click');
        const vaultCb = document.getElementById('opt_vault');
        const nukeCb = document.getElementById('opt_nuke');
        clickCb.checked = GM_getValue(CLICK_KEY, true);
        vaultCb.checked = GM_getValue(VAULT_KEY, true);
        nukeCb.checked = GM_getValue(NUKE_KEY, true);
        clickCb.onchange = () => {
            GM_setValue(CLICK_KEY, clickCb.checked);
            toggleClickLoop();
        };
        vaultCb.onchange = () => GM_setValue(VAULT_KEY, vaultCb.checked);
        nukeCb.onchange = () => {
            GM_setValue(NUKE_KEY, nukeCb.checked);
            document.body.classList.toggle('am-nuked', nukeCb.checked);
        };
        document.body.classList.toggle('am-nuked', nukeCb.checked);
        if (statusUiInterval) clearInterval(statusUiInterval);
        statusUiInterval = setInterval(updateStatusPanel, STATUS_PANEL_TICK_MS);
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(updateCountdown, 150);

        const urlInput = document.getElementById('ngrok_url');
        // Keep the input usable: stop the host page from eating paste / key events.
        ['paste', 'copy', 'cut', 'keydown', 'keyup', 'keypress', 'input'].forEach(ev => {
            urlInput.addEventListener(ev, (e) => e.stopPropagation(), true);
        });
        urlInput.addEventListener('input', refreshConnectButton);

        document.getElementById('btn_link').onclick = () => {
            const raw = urlInput.value;
            const url = normalizeBaseUrl(raw);
            // Active connection states (connecting/connected/reconnecting): button is "DISCONNECT MAIN"
            // unless the URL has changed, in which case it reconnects with the new URL.
            if (connState === 'connected' || connState === 'connecting' || connState === 'reconnecting') {
                if (url && url !== activeBaseUrl) {
                    GM_setValue('ngrok_url', url);
                    log('URL changed — reconnecting to ' + url);
                    disconnect(/*keepAutoReconnect*/ true);
                    setTimeout(() => startLoop(url), 200);
                    return;
                }
                disconnect();
                return;
            }
            if (!url) { log("Enter a valid URL, then click CONNECT MAIN."); return; }
            GM_setValue('ngrok_url', url);
            startLoop(url);
        };
        document.getElementById('btn_ping').onclick = () => pingLink();
        refreshConnectButton();
    }

    function refreshConnectButton() {
        const btn = document.getElementById('btn_link');
        const inp = document.getElementById('ngrok_url');
        if (!btn || !inp) return;
        const urlNow = normalizeBaseUrl(inp.value);
        const active = connState === 'connected' || connState === 'connecting' || connState === 'reconnecting';
        if (active && urlNow && urlNow !== activeBaseUrl) {
            btn.textContent = 'APPLY URL';
            btn.style.background = '#2563eb';
            btn.style.color = '#fff';
            btn.title = 'Reconnect using the new URL';
        } else if (active) {
            btn.textContent = 'DISCONNECT MAIN';
            btn.style.background = '#b91c1c';
            btn.style.color = '#fff';
            btn.title = '';
        } else {
            btn.textContent = 'CONNECT MAIN';
            btn.style.background = '#f59e0b';
            btn.style.color = '#111';
            btn.title = '';
        }
    }

    function log(msg) {
        const s = document.getElementById('status');
        if(!s) return;
        const time = new Date().toLocaleTimeString().split(' ')[0];
        s.insertAdjacentHTML('afterbegin', `<div><span style="color:#777;">[${time}]</span> ${msg}</div>`);
        while (s.childNodes.length > MAX_LOG_LINES) s.removeChild(s.lastChild);
    }

    function updateCountdown() {
        const progEl = document.getElementById('aw-progress');
        const timerEl = document.getElementById('st_timer');
        const statusEl = document.getElementById('aw-status-text');
        if (!progEl || !timerEl || !statusEl) return;

        const enabled = GM_getValue(CLICK_KEY, true);
        if (!enabled) {
            progEl.style.width = '0%';
            timerEl.innerText = '--';
            if (statusEl.dataset.state !== 'paused') {
                statusEl.dataset.state = 'paused';
                statusEl.innerText = 'Paused';
                statusEl.style.color = '#fbbf24';
            }
            return;
        }
        const now = Date.now();
        const remainMs = Math.max(0, nextClickAt - now);
        const sec = Math.ceil(remainMs / 1000);
        const pct = Math.max(0, Math.min(100, 100 - (remainMs / CLICK_INTERVAL_MS) * 100));
        progEl.style.width = pct + '%';
        timerEl.innerText = `${sec}s`;
        if (statusEl.dataset.state === 'sending' || statusEl.dataset.state === 'error') return;
        if (statusEl.dataset.state !== 'waiting') {
            statusEl.dataset.state = 'waiting';
            statusEl.innerText = 'Waiting';
            statusEl.style.color = '#f3e8b5';
        }
    }

    async function updateStatusPanel() {
        const clicksEl = document.getElementById('st_clicks');
        const cpsEl = document.getElementById('st_cps');
        const caseValEl = document.getElementById('st_case_val');
        const errEl = document.getElementById('st_errors');
        if (!clicksEl || !cpsEl || !caseValEl || !errEl) return;

        clicksEl.innerText = (clickReqCount * CLICKS_PER_REQ).toLocaleString();
        cpsEl.innerText = (CLICKS_PER_REQ / (CLICK_INTERVAL_MS / 1000)).toFixed(2);
        errEl.innerText = totalErrors.toString();

        if (document.visibilityState !== 'visible') return;
        if (statusFetchInFlight) return;
        if ((Date.now() - statusLastFetchAt) < STATUS_NETWORK_MS) return;
        statusFetchInFlight = true;
        statusLastFetchAt = Date.now();
        try {
            if (!statusCasePriceMap || (Date.now() - statusCasePriceMapAt) > STATUS_CASE_CACHE_MS) {
                const allCasesRes = await fetch('/api/cases/cases', { cache: 'no-cache' });
                if (allCasesRes.ok) {
                    const allCases = await allCasesRes.json();
                    statusCasePriceMap = {};
                    allCases.forEach(c => { statusCasePriceMap[c._id] = Number(c.price || 0); });
                    statusCasePriceMapAt = Date.now();
                }
            }
            const userCasesRes = await fetch('/api/cases', { cache: 'no-cache' });
            if (userCasesRes.ok && statusCasePriceMap) {
                const userCases = await userCasesRes.json();
                let totalCaseValue = 0;
                userCases.forEach(uc => {
                    const price = statusCasePriceMap[uc._id] || 0;
                    totalCaseValue += price * Number(uc.amount || 0) * 0.7;
                });
                caseValEl.innerText = `$${Math.floor(totalCaseValue).toLocaleString()}`;
            } else {
                caseValEl.innerText = '-';
            }
        } catch (_) {
            // keep last visible values
        } finally {
            statusFetchInFlight = false;
        }
    }

    async function pollNW() {
        const nwEl = document.getElementById('st_nw');
        const deltaEl = document.getElementById('st_delta');
        const hourlyEl = document.getElementById('st_hourly');
        if (!nwEl || !deltaEl || !hourlyEl) return;
        try {
            const res = await fetch('/api/me', { cache: 'no-cache' });
            if (!res.ok) return;
            const me = await res.json();
            const nw = Math.floor(Number(me.networth || 0));
            const t = Date.now();
            nwEl.innerText = '$' + nw.toLocaleString();
            if (lastNW !== null && lastNWT !== null) {
                const delta = nw - lastNW;
                const dtMs = t - lastNWT;
                const perHour = dtMs > 0 ? Math.round(delta * 3600000 / dtMs) : 0;
                const sign = delta >= 0 ? '+' : '';
                const color = delta > 0 ? '#10b981' : delta < 0 ? '#ef4444' : '#8b7d45';
                deltaEl.innerText = `${sign}$${delta.toLocaleString()}`;
                deltaEl.style.color = color;
                const hSign = perHour >= 0 ? '+' : '';
                hourlyEl.innerText = `${hSign}$${perHour.toLocaleString()}/h`;
                hourlyEl.style.color = color;
            }
            lastNW = nw;
            lastNWT = t;
        } catch (_) {}
    }

    function startNWTracker() {
        if (nwTimer) clearInterval(nwTimer);
        pollNW();
        nwTimer = setInterval(pollNW, NW_POLL_MS);
    }

    function stopNWTracker() {
        if (nwTimer) { clearInterval(nwTimer); nwTimer = null; }
        lastNW = null;
        lastNWT = null;
    }

    const DEFAULT_BACKEND_URL = 'http://127.0.0.1:5000';
    const TOKEN_KEY = 'cchub_token';

    function normalizeBaseUrl(url) {
        let v = (url || '').trim();
        if (!v) return '';
        const hashIdx = v.indexOf('#');
        if (hashIdx !== -1) {
            const token = v.slice(hashIdx + 1).trim();
            if (token) GM_setValue(TOKEN_KEY, token);
            v = v.slice(0, hashIdx).trim();
        }
        if (!v) return '';
        if (!/^https?:\/\//i.test(v)) v = `http://${v}`;
        v = v.replace(/\/+$/, '');
        v = v.replace(/\/api(?:\/.*)?$/i, '');
        return v;
    }

    function cchubToken() {
        return GM_getValue(TOKEN_KEY, '');
    }

    function gmRequest(opts) {
        const token = cchubToken();
        const headers = Object.assign({}, opts.headers || {});
        if (token) headers['X-Alt-Token'] = token;
        return GM_xmlhttpRequest(Object.assign({}, opts, { headers }));
    }

    function setConnState(state) {
        connState = state;
        isConnected = state === 'connected';
        const el = document.getElementById('conn_state');
        const dot = document.getElementById('aw-dot');
        const map = {
            idle:         ['DISCONNECTED',   '#a1a1aa', '#444',      '#6b7280'],
            connecting:   ['CONNECTING...',  '#eab308', '#eab30855', '#eab308'],
            connected:    ['CONNECTED',      '#10b981', '#10b98155', '#10b981'],
            reconnecting: ['RECONNECTING...','#f59e0b', '#f59e0b55', '#f59e0b'],
        };
        const [label, color, border, dotColor] = map[state] || map.idle;
        if (el) {
            el.textContent = label;
            el.style.color = color;
            el.style.borderColor = border;
        }
        if (dot) {
            dot.style.background = dotColor;
            dot.style.boxShadow = `0 0 10px ${dotColor}aa`;
        }
        refreshConnectButton();
    }

    function disconnect(keepAutoReconnect) {
        if (heartbeatTimer) { clearTimeout(heartbeatTimer); heartbeatTimer = null; }
        heartbeatInFlight = false;
        heartbeatBackoffMs = HEARTBEAT_INTERVAL_MS;
        activeBaseUrl = '';
        cachedMe = null;
        connectedSince = 0;
        stopNWTracker();
        if (!keepAutoReconnect) GM_setValue('am.autoconnect.main', false);
        setConnState('idle');
        log("Main: Disconnected.");
    }

    function pingLink() {
        const baseUrl = normalizeBaseUrl(document.getElementById('ngrok_url').value);
        if (!baseUrl) { log('No URL set.'); return; }
        gmRequest({
            method: "GET",
            url: `${baseUrl}/api/settings`,
            timeout: 10000,
            onload: (res) => {
                const ok = res.status >= 200 && res.status < 300;
                log(`Ping ${ok ? 'OK' : 'FAIL'} (${res.status}) -> ${baseUrl}`);
            },
            onerror: () => log(`Ping ERROR -> ${baseUrl}`),
            ontimeout: () => log(`Ping TIMEOUT -> ${baseUrl}`),
        });
    }

    function startLoop(url) {
        const baseUrl = normalizeBaseUrl(url);
        if (!baseUrl) { log("Invalid URL."); return; }
        if (heartbeatTimer) { clearTimeout(heartbeatTimer); heartbeatTimer = null; }
        heartbeatInFlight = false;
        activeBaseUrl = baseUrl;
        heartbeatBackoffMs = HEARTBEAT_INTERVAL_MS;
        connectedSince = Date.now();
        GM_setValue('am.autoconnect.main', true);
        setConnState('connecting');
        log("Main: Connecting to " + baseUrl);
        heartbeat(baseUrl);
        toggleClickLoop();
        startNWTracker();
    }

    function scheduleHeartbeat(delayMs) {
        if (heartbeatTimer) clearTimeout(heartbeatTimer);
        if (!activeBaseUrl) return;
        heartbeatTimer = setTimeout(() => {
            heartbeatTimer = null;
            heartbeat(activeBaseUrl);
        }, Math.max(500, delayMs | 0));
    }

    function onHeartbeatSuccess(res, me) {
        heartbeatBackoffMs = HEARTBEAT_INTERVAL_MS;
        lastHeartbeatSuccess = Date.now();
        cachedMe = me;
        const wasConnected = connState === 'connected';
        setConnState('connected');
        if (!wasConnected) log('Main: Link ok.');
        try {
            const json = JSON.parse(res.responseText);
            if (json && Array.isArray(json.commands) && json.commands.length > 0) {
                json.commands.forEach(cmd => {
                    // Main account never refreshes from backend (avoids reload loop after connect)
                    if (cmd.type === 'refresh_page') {
                        log("Main: Ignoring refresh command (main account does not auto-refresh).");
                        return;
                    }
                    executeCommand(cmd, me, activeBaseUrl);
                });
            }
        } catch (_) {}
        scheduleHeartbeat(heartbeatBackoffMs);
    }

    function onHeartbeatFailure(reason) {
        totalErrors++;
        if (connState === 'connected') {
            setConnState('reconnecting');
            log('Main: Link lost (' + reason + '). Retrying with backoff…');
        } else if (connState === 'connecting') {
            setConnState('reconnecting');
            log('Main: Connect failed (' + reason + '). Retrying…');
        }
        heartbeatBackoffMs = Math.min(HEARTBEAT_BACKOFF_MAX_MS, Math.max(HEARTBEAT_INTERVAL_MS, Math.round(heartbeatBackoffMs * 1.6)));
        scheduleHeartbeat(heartbeatBackoffMs);
    }

    function parseJwt(token) {
        try { return JSON.parse(atob(token.split('.')[1])); } catch (e) { return null; }
    }

    async function buyCasesRobust(caseId, caseType, amount) {
        let remaining = Math.max(0, Math.floor(Number(amount) || 0));
        let bought = 0;
        let lastStatus = 0;
        let chunkMax = 1000;
        // Prime endpoint similarly to in-game buy modal flow.
        try { await fetch(`/api/cases?id=${encodeURIComponent(caseId)}`, { method: 'GET', cache: 'no-cache' }); } catch (_) {}
        while (remaining > 0) {
            let chunk = Math.min(chunkMax, remaining);
            if (chunk <= 0) break;
            let ok = false;
            // Primary flow: exact format used by the stable reference script.
            for (let tries = 0; tries < 5; tries++) {
                try {
                    const res = await fetch('/api/cases', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: caseId, type: caseType, amount: chunk })
                    });
                    lastStatus = res.status;
                    if (res.ok) {
                        ok = true;
                        break;
                    }
                    const backoff = (res.status === 403 || res.status === 429 || res.status >= 500) ? 800 * (tries + 1) : 180;
                    await new Promise(r => setTimeout(r, backoff));
                } catch (_) {
                    await new Promise(r => setTimeout(r, 800 * (tries + 1)));
                }
            }
            if (!ok && chunk === 1) {
                // Last-resort fallback when backend expects query id shape.
                try {
                    const res = await fetch(`/api/cases?id=${encodeURIComponent(caseId)}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: caseId, amount: 1 })
                    });
                    lastStatus = res.status;
                    if (res.ok) ok = true;
                } catch (_) {}
            }
            if (!ok) {
                if (lastStatus === 400 && chunk > 1) {
                    // Dynamic backoff for strict server-side amount validation.
                    chunkMax = Math.max(1, Math.floor(chunk / 2));
                    continue;
                }
                return { ok: false, status: lastStatus || 0, bought };
            }
            bought += chunk;
            remaining -= chunk;
            await new Promise(r => setTimeout(r, 120));
        }
        return { ok: true, status: 200, bought };
    }

    /** Fetch with backoff retry on transient CC statuses (403/429/5xx) and network errors.
     *  Keeps worker "connected" on temporary case-clicker.com hiccups instead of dropping the hub link. */
    async function fetchWithRetry(url, options, maxAttempts = 3) {
        const opts = options || {};
        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            const isLast = attempt === maxAttempts - 1;
            try {
                const res = await fetch(url, opts);
                if ((res.status === 403 || res.status === 429 || res.status >= 500) && !isLast) {
                    await new Promise(r => setTimeout(r, 600 * (attempt + 1)));
                    continue;
                }
                return res;
            } catch (e) {
                if (isLast) throw e;
                await new Promise(r => setTimeout(r, 600 * (attempt + 1)));
            }
        }
        throw new Error('fetchWithRetry exhausted');
    }

    /** Fetch URL and parse JSON safely. Retries on HTML response or 429. Avoids "Unexpected token '<'" when server returns error page. */
    async function safeFetchJson(url, options = {}, retries = 2) {
        const opts = { cache: 'no-cache', ...options };
        for (let attempt = 0; attempt <= retries; attempt++) {
            const res = await fetch(url, opts);
            const text = await res.text();
            if ((res.status === 403 || res.status === 429 || res.status >= 500) && attempt < retries) {
                const wait = 2000 * (attempt + 1);
                await new Promise(r => setTimeout(r, wait));
                continue;
            }
            const trimmed = text.trim();
            if (trimmed.startsWith('<')) {
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, 1500 * (attempt + 1)));
                    continue;
                }
                throw new Error('Server returned HTML instead of JSON (rate limit or error page)');
            }
            try {
                return JSON.parse(text);
            } catch (e) {
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, 1500 * (attempt + 1)));
                    continue;
                }
                throw new Error(e.message || 'Invalid JSON');
            }
        }
        throw new Error('Request failed after retries');
    }

    function reportRemote(baseUrl, id, msg) {
        log("LOG: " + msg);
        gmRequest({
            method: "POST", url: `${baseUrl}/api/log_status`,
            headers: { "Content-Type": "application/json" },
            data: JSON.stringify({ id: id, msg: msg })
        });
    }

    function getCapturedSocket() {
        if (unsafeWindow.__CC_SOCKET__ && unsafeWindow.__CC_SOCKET__.readyState === 1) return unsafeWindow.__CC_SOCKET__;
        const pool = unsafeWindow.__CC_SOCKETS__ || [];
        for (let i = pool.length - 1; i >= 0; i--) {
            if (pool[i] && pool[i].readyState === 1) {
                unsafeWindow.__CC_SOCKET__ = pool[i];
                return pool[i];
            }
        }
        return null;
    }

    function setClickStatus(state, text, color) {
        const el = document.getElementById('aw-status-text');
        if (!el) return;
        el.dataset.state = state;
        el.innerText = text;
        el.style.color = color;
    }

    async function runClickBatch() {
        if (!GM_getValue(CLICK_KEY, true)) return;
        setClickStatus('sending', 'Sending...', '#6366f1');
        try {
            const res = await fetch('/api/caseClick', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ clicks: CLICKS_PER_REQ })
            });
            if (!res.ok) {
                totalErrors++;
                log(`Main click batch failed: ${res.status}`);
                setClickStatus('error', `Error ${res.status}`, '#ef4444');
                setTimeout(() => {
                    const el = document.getElementById('aw-status-text');
                    if (el && el.dataset.state === 'error') el.dataset.state = '';
                }, 2000);
                return;
            }
            clickReqCount++;
            if (GM_getValue(VAULT_KEY, true) && clickReqCount % VAULT_EVERY_N_REQ === 0) {
                try { await fetch('/api/vault', { method: 'POST' }); } catch (_) {}
            }
            setClickStatus('', 'Waiting', '#f3e8b5');
        } catch (e) {
            totalErrors++;
            log(`Main click batch error: ${e.message}`);
            setClickStatus('error', 'Network error', '#ef4444');
            setTimeout(() => {
                const el = document.getElementById('aw-status-text');
                if (el && el.dataset.state === 'error') el.dataset.state = '';
            }, 2000);
        }
    }

    function scheduleNextClick() {
        if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
        if (!GM_getValue(CLICK_KEY, true)) return;
        nextClickAt = Date.now() + CLICK_INTERVAL_MS;
        clickTimer = setTimeout(async () => {
            await runClickBatch();
            scheduleNextClick();
        }, CLICK_INTERVAL_MS);
    }

    function toggleClickLoop() {
        if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
        if (!GM_getValue(CLICK_KEY, true)) {
            setClickStatus('paused', 'Paused', '#fbbf24');
            return;
        }
        runClickBatch().then(scheduleNextClick);
    }

    // Passive socket observer — we never reload the page or touch the socket.
    // case-clicker.com owns the socket and will reconnect on its own. This function
    // is intentionally a no-op kept for API compatibility with older versions.
    function startSocketMonitoring() { /* disabled; see onHeartbeatFailure for CC Hub link recovery */ }

    // --- NEXT.JS IMAGE FETCH ---
    async function fetchPublicProfileImage(userId) {
        try {
            let buildId = unsafeWindow?.__NEXT_DATA__?.buildId;
            if (!buildId) {
                const scriptTag = document.getElementById('__NEXT_DATA__');
                if (scriptTag) {
                    const data = JSON.parse(scriptTag.textContent);
                    buildId = data.buildId;
                }
            }
            if (!buildId) {
                const nextScript = document.querySelector('script[src*="/_next/static/"]');
                const src = nextScript?.getAttribute('src') || '';
                const m = src.match(/\/_next\/static\/([^/]+)\//);
                if (m) buildId = m[1];
            }
            const normalizeImg = (img) => {
                if (!img) return '';
                if (img.startsWith('http')) return img;
                if (img.startsWith('/')) return `https://case-clicker.com${img}`;
                return img;
            };
            const extractProfileMeta = (json) => {
                try {
                    let userData = json?.pageProps?.user;
                    if (typeof userData === 'string') userData = JSON.parse(userData);
                    let rankObj = userData?.rank;
                    if (typeof rankObj === 'string') {
                        try { rankObj = JSON.parse(rankObj); } catch (_) { rankObj = null; }
                    }
                    return {
                        image: normalizeImg(userData?.image || ''),
                        createdAt: userData?.createdAt || null,
                        rankName: rankObj?.name || '',
                        rankImage: normalizeImg(rankObj?.image || '')
                    };
                } catch (_) {
                    return null;
                }
            };
            if (buildId) {
                const lang = (document.documentElement?.lang || 'en').split('-')[0] || 'en';
                const urls = [
                    `https://case-clicker.com/_next/data/${buildId}/${lang}/profile/${userId}.json?id=${userId}`,
                    `https://case-clicker.com/_next/data/${buildId}/en/profile/${userId}.json?id=${userId}`,
                    `https://case-clicker.com/_next/data/${buildId}/profile/${userId}.json?id=${userId}`
                ];
                for (const url of urls) {
                    try {
            const res = await fetch(url);
                        if (!res.ok) continue;
            const json = await res.json();
                        const meta = extractProfileMeta(json);
                        if (meta && (meta.image || meta.rankName || meta.rankImage || meta.createdAt)) return meta;
                    } catch (_) {}
                }
            }
            const nextUser = unsafeWindow?.__NEXT_DATA__?.props?.pageProps?.user;
            if (nextUser) {
                let rankObj = nextUser?.rank;
                if (typeof rankObj === 'string') {
                    try { rankObj = JSON.parse(rankObj); } catch (_) { rankObj = null; }
                }
                return {
                    image: normalizeImg(nextUser.image || ''),
                    createdAt: nextUser.createdAt || null,
                    rankName: rankObj?.name || '',
                    rankImage: normalizeImg(rankObj?.image || '')
                };
            }
            const navAvatar = document.querySelector('img[src*="avatar"], img[src*="steam"], img[class*="avatar"]');
            if (navAvatar && navAvatar.src) return { image: navAvatar.src, createdAt: null, rankName: '', rankImage: '' };

        } catch (e) { console.error(e); }
        return { image: '', createdAt: null, rankName: '', rankImage: '' };
    }

    // --- 3. INVENTORY SCANNER ---
    async function scanInventory() {
        if(isScanning) return;
        isScanning = true;
        inventoryCache = [];
        log("Scanning main inventory...");

        try {
            const r1 = await fetch('https://case-clicker.com/api/inventory?page=1&sort=true&showStickers=true&showUpgradedSkins=true');
            const d1 = await r1.json();

            const resolveUrl = (url, type = 'skin') => {
                if (!url || typeof url !== 'string') return "https://case-clicker.com/img/unknown.png";
                if (url.startsWith('-')) return `https://steamcommunity-a.akamaihd.net/economy/image/${url}`;
                if (url.startsWith('http')) return url;
                if (url.startsWith('/')) return `https://case-clicker.com${url}`;
                const folder = type === 'sticker' ? 'stickers' : 'skins';
                return `https://case-clicker.com/pictures/${folder}/${url}`;
            };

            const process = (list) => {
                list.forEach(s => {
                    let item = Object.assign({}, s);
                    let rawImg = s.iconUrl || s.imageUrl || s.image;
                    item.img = resolveUrl(rawImg, 'skin');
                    if (item.stickers && Array.isArray(item.stickers)) {
                        item.processedStickers = item.stickers.map(st => ({
                            ...st,
                            imgUrl: resolveUrl(st.iconUrl, 'sticker')
                        }));
                    }
                    inventoryCache.push(item);
                });
            };

            if(d1.skins) process(d1.skins);
            else if(Array.isArray(d1)) process(d1);

            const totalPages = d1.pages || 0;
            if(totalPages > 1) {
                for(let i=2; i<=totalPages; i++) {
                    const r = await fetch(`https://case-clicker.com/api/inventory?page=${i}&sort=true&showStickers=true&showUpgradedSkins=true`);
                    const d = await r.json();
                    if(d.skins) process(d.skins);
                    else if(Array.isArray(d)) process(d);
                }
            }
            log(`Main scan: ${inventoryCache.length} items`);

            const url = GM_getValue('ngrok_url', '');
            const meRes = await fetch('/api/auth/token');
            const meJson = await meRes.json();
            const me = parseJwt(meJson.token);

            if(url && me) {
                const userId = me.id || me.sub;
                gmRequest({
                    method: "POST", url: `${url}/api/update_inventory`,
                    headers: { "Content-Type": "application/json" },
                    data: JSON.stringify({ id: userId, items: inventoryCache })
                });
            }
        } catch(e) { log("Scan failed."); }
        isScanning = false;
    }

    async function fetchCaseTypeMap() {
        const map = {};
        const groups = ['cases', 'capsules', 'souvenir'];
        for (const group of groups) {
            try {
                const res = await fetch(`/api/cases/${group}`, { cache: 'no-cache' });
                if (!res.ok) continue;
                const arr = await res.json();
                const type = group === 'capsules' ? 'capsule' : (group === 'souvenir' ? 'souvenir' : 'case');
                arr.forEach(item => { map[item._id] = type; });
            } catch (_) {}
        }
        return map;
    }

    async function reportCaseSummary(baseUrl, userId) {
        try {
            const [invRes, casesRes, capsulesRes, souvenirRes] = await Promise.all([
                fetch('/api/cases', { cache: 'no-cache' }),
                fetch('/api/cases/cases', { cache: 'no-cache' }),
                fetch('/api/cases/capsules', { cache: 'no-cache' }),
                fetch('/api/cases/souvenir', { cache: 'no-cache' })
            ]);
            if (!invRes.ok) return;
            const inv = await invRes.json();
            const priceMap = {};
            if (casesRes.ok) (await casesRes.json()).forEach(c => { priceMap[c._id] = Number(c.price || 0); });
            if (capsulesRes.ok) (await capsulesRes.json()).forEach(c => { priceMap[c._id] = Number(c.price || 0); });
            if (souvenirRes.ok) (await souvenirRes.json()).forEach(c => { priceMap[c._id] = Number(c.price || 0); });
            let totalItems = 0;
            let totalAmount = 0;
            let totalValue = 0;
            inv.forEach(it => {
                const amount = Number(it.amount || 0);
                if (amount <= 0) return;
                totalItems += 1;
                totalAmount += amount;
                totalValue += (priceMap[it._id] || 0) * amount;
            });
            gmRequest({
                method: "POST",
                url: `${baseUrl}/api/case_summary`,
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify({
                    id: userId,
                    summary: {
                        total_items: totalItems,
                        total_amount: totalAmount,
                        total_value: totalValue,
                        total_value_70: totalValue * 0.7
                    }
                })
            });
        } catch (_) {}
    }

    async function sellCasesSnapshot(baseUrl, userId) {
        try {
            const typeMap = await fetchCaseTypeMap();
            const invRes = await fetch('/api/cases', { cache: 'no-cache' });
            if (!invRes.ok) {
                reportRemote(baseUrl, userId, 'CASE_SELL_ERROR:Could not read case inventory');
                return;
            }
            const inv = await invRes.json();
            const snapshot = inv
                .filter(it => Number(it.amount || 0) > 0)
                .map(it => ({ id: it._id, amount: Number(it.amount || 0), type: typeMap[it._id] || 'case' }));
            if (snapshot.length === 0) {
                reportRemote(baseUrl, userId, 'CASE_SELL_DONE:No cases to sell');
                await reportCaseSummary(baseUrl, userId);
                return;
            }
            reportRemote(baseUrl, userId, `CASE_SELL_START:${snapshot.length}`);
            let sold = 0;
            for (const item of snapshot) {
                const res = await fetch('/api/cases', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: item.id, amount: item.amount, type: item.type })
                });
                if (res.ok) sold += 1;
                await new Promise(r => setTimeout(r, 120));
            }
            reportRemote(baseUrl, userId, `CASE_SELL_DONE:${sold}/${snapshot.length}`);
            await reportCaseSummary(baseUrl, userId);
        } catch (e) {
            reportRemote(baseUrl, userId, `CASE_SELL_ERROR:${e.message}`);
        }
    }

    // --- 4. HEARTBEAT ---
    async function heartbeat(baseUrl) {
        if (baseUrl !== activeBaseUrl) return;
        if (heartbeatInFlight) { scheduleHeartbeat(2000); return; }
        heartbeatInFlight = true;

        // Watchdog: if the callback never fires, force-retry rather than hang.
        const watchdog = setTimeout(() => {
            if (!heartbeatInFlight) return;
            heartbeatInFlight = false;
            if (baseUrl === activeBaseUrl) onHeartbeatFailure('watchdog');
        }, HEARTBEAT_REQUEST_TIMEOUT_MS + 5000);

        const fail = (reason) => {
            clearTimeout(watchdog);
            heartbeatInFlight = false;
            if (baseUrl !== activeBaseUrl) return;
            onHeartbeatFailure(reason);
        };

        let me = { _id: null, name: "Unknown" };
        try {
            const tokenRes = await fetchWithRetry('/api/auth/token', {}, 2);
            if (tokenRes.status !== 200) return fail('auth/token ' + tokenRes.status);
            const json = await tokenRes.json();
            const jwt = parseJwt(json.token);
            if (jwt) { me._id = jwt.id || jwt.sub; me.name = jwt.name; }
            if (!me._id) return fail('no jwt id');

            const statsRes = await fetchWithRetry('/api/me', {}, 2);
            if (statsRes.status !== 200) return fail('me ' + statsRes.status);
            const fullStats = await statsRes.json();
            if (!cachedProfileMeta) cachedProfileMeta = await fetchPublicProfileImage(me._id);
            if (!cachedProfileImage && cachedProfileMeta?.image) cachedProfileImage = cachedProfileMeta.image;
            const statsAvatar = fullStats?.image || fullStats?.avatar || fullStats?.profileImage;
            if (!cachedProfileImage && statsAvatar) cachedProfileImage = statsAvatar;
            let finalImage = cachedProfileImage || "https://case-clicker.com/img/unknown.png";

            const payload = {
                id: me._id,
                username: me.name,
                avatar: finalImage,
                stats: fullStats,
                skinCount: inventoryCache.length > 0 ? inventoryCache.length : (fullStats.inventorySize || 0),
                profile: {
                    image: cachedProfileMeta?.image || finalImage || '',
                    createdAt: cachedProfileMeta?.createdAt || fullStats?.createdAt || fullStats?.user?.createdAt || null,
                    rankName: cachedProfileMeta?.rankName || fullStats?.premierRank?.name || '',
                    rankImage: cachedProfileMeta?.rankImage || fullStats?.premierRank?.image || fullStats?.premierRank?.img || ''
                }
            };

            gmRequest({
                method: "POST",
                url: `${baseUrl}/api/heartbeat`,
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify(payload),
                timeout: HEARTBEAT_REQUEST_TIMEOUT_MS,
                onload: (res) => {
                    clearTimeout(watchdog);
                    heartbeatInFlight = false;
                    if (baseUrl !== activeBaseUrl) return;
                    if (res.status !== 200) { onHeartbeatFailure('http ' + res.status); return; }
                    onHeartbeatSuccess(res, me);
                },
                onerror: () => fail('network'),
                ontimeout: () => fail('timeout'),
                onabort: () => fail('abort'),
            });
        } catch (e) {
            console.error(e);
            fail('exception: ' + (e?.message || e));
        }
    }

    function sendRaw(socket, eventName, data) {
        const payload = `42${JSON.stringify([eventName, data])}`;
        socket.send(payload);
    }

    // --- 5. COMMANDS (INCLUDING CONFIRM) ---
    async function executeCommand(cmd, me, baseUrl) {
        if(cmd.type === 'scan_case_summary') {
            await reportCaseSummary(baseUrl, me._id);
            return;
        }

        if(cmd.type === 'sell_cases_snapshot') {
            await sellCasesSnapshot(baseUrl, me._id);
            return;
        }

        if(cmd.type === 'scan_inventory') {
            reportRemote(baseUrl, me._id, "Main: Scanning inventory...");
            await scanInventory();
            reportRemote(baseUrl, me._id, `Main: Inventory scan complete: ${inventoryCache.length} items`);
            return;
        }

        if(cmd.type === 'start_booster' || cmd.type === 'resume_booster') {
            const caseId = cmd.caseId;
            const caseType = cmd.caseType || 'case';
            const casePrice = Number(cmd.casePrice || 0);
            const caseName = cmd.caseName || 'Unknown case';
            const clickUntilBoost = !!cmd.clickUntilBoost;
            const createdAtTs = Number(cmd.createdAtTs || 0);
            const autosellVariant = 'money';
            let cycle = Number(cmd.cycle || 0);
            window.__BOOSTER_ACTIVE = true;

            reportRemote(baseUrl, me._id, `BOOSTER_STATUS:running:${caseName}`);

            try {
                while (window.__BOOSTER_ACTIVE) {
                    const meData = await safeFetchJson('/api/me');
                    const rankName = (meData?.premierRank?.name || meData?.premierRank?.title || '').toLowerCase();
                    const money = Number(meData?.money || 0);

                    if (rankName.includes('global')) {
                        reportRemote(baseUrl, me._id, `BOOSTER_DONE:Reached global rank`);
                        break;
                    }

                    if (clickUntilBoost && createdAtTs > 0) {
                        const nowTs = Date.now() / 1000;
                        const ageSec = nowTs - createdAtTs;
                        const boostRemain = Math.max(0, 172800 - ageSec);
                        if (boostRemain > 0) {
                            reportRemote(baseUrl, me._id, `BOOSTER_WAIT:${Math.floor(boostRemain)}`);
                            const clickRes = await fetch('/api/click', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ clicks: 500 })
                            });
                            if (!clickRes.ok) {
                                reportRemote(baseUrl, me._id, `BOOSTER_ALERT:Money click failed (${clickRes.status})`);
                                break;
                            }
                            try { await fetch('/api/vault', { method: 'POST' }); } catch(_) {}
                            await new Promise(r => setTimeout(r, 60000));
                            continue;
                        }
                    }

                    if (!casePrice || money < casePrice) {
                        reportRemote(baseUrl, me._id, `BOOSTER_DONE:Not enough money to continue`);
                        break;
                    }

                    const amountToBuy = Math.floor(money / casePrice);
                    if (amountToBuy <= 0) {
                        reportRemote(baseUrl, me._id, `BOOSTER_DONE:No affordable cases`);
                        break;
                    }

                    const buyResult = await buyCasesRobust(caseId, caseType, amountToBuy);
                    if (!buyResult.ok) {
                        reportRemote(baseUrl, me._id, `BOOSTER_ALERT:Buy failed (${buyResult.status})`);
                        break;
                    }

                    const inv = await safeFetchJson('/api/cases');
                    const item = inv.find(x => x._id === caseId);
                    const total = item ? item.amount : 0;
                    if (total <= 0) {
                        reportRemote(baseUrl, me._id, `BOOSTER_ALERT:No cases after buy`);
                        break;
                    }

                    let keepOpening = true;
                    while (keepOpening) {
                        const inv2 = await safeFetchJson('/api/cases');
                        const item2 = inv2.find(x => x._id === caseId);
                        if (!item2 || item2.amount <= 0) {
                            keepOpening = false;
                            break;
                        }

                        const remaining = item2.amount;
                        const opened = total - remaining;
                        const progress = total > 0 ? Math.floor((opened / total) * 100) : 0;
                        const meBatch = await safeFetchJson('/api/me');
                        const multiplier = meBatch.maxCaseOpenMultiplier || 1;
                        const caseOpenCount = meBatch.caseOpenCount || 1;
                        const batchSize = Math.min(caseOpenCount, Math.ceil(item2.amount / multiplier));
                        reportRemote(baseUrl, me._id, `BOOSTER_PROGRESS:${opened}:${total}:${remaining}:${progress}:${batchSize}x${multiplier}:${cycle}`);

                        const openEndpoint = caseType === 'capsule' ? '/api/open/capsule' : '/api/open/case';
                        const openRes = await fetch(openEndpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                id: caseId,
                                quickOpen: true,
                                autoOpenConfig: { autosellActivated: true, autosellVariant },
                                count: String(batchSize)
                            })
                        });
                        if (!openRes.ok) {
                            reportRemote(baseUrl, me._id, `BOOSTER_ALERT:Open failed (${openRes.status})`);
                            keepOpening = false;
                            break;
                        }
                        await new Promise(r => setTimeout(r, 700));
                    }

                    cycle += 1;
                    await new Promise(r => setTimeout(r, 1200));
                }
            } catch (e) {
                reportRemote(baseUrl, me._id, `BOOSTER_ALERT:${e.message}`);
            } finally {
                window.__BOOSTER_ACTIVE = false;
            }
            return;
        }

        if(cmd.type === 'resume_convert') {
            const caseId = cmd.caseId;
            const caseType = cmd.caseType || 'case';
            const sellMethod = cmd.sellMethod || 'tokens';
            const totalCasesFromJob = cmd.totalCases || 0; // Total cases from original job
            
            // Mark convert as active
            window.__CONVERT_ACTIVE = true;
            
            reportRemote(baseUrl, me._id, `Main: Resuming convert job...`);
            
            try {
                // Get current case count
                const caseInv = await safeFetchJson('/api/cases');
                const targetItem = caseInv.find(item => item._id === caseId);
                
                if(!targetItem || targetItem.amount <= 0) {
                    reportRemote(baseUrl, me._id, `Main: No cases remaining. Convert complete.`);
                    window.__CONVERT_ACTIVE = false;
                    return;
                }
                
                const remainingCases = targetItem.amount;
                // Calculate how many we've already opened by comparing with total
                const estimatedTotal = totalCasesFromJob > 0 ? totalCasesFromJob : remainingCases;
                const casesAlreadyOpened = estimatedTotal - remainingCases;
                const initialCaseCount = remainingCases; // Current remaining is our starting point
                
                reportRemote(baseUrl, me._id, `Main: Resuming: ${remainingCases} cases remaining (estimated ${casesAlreadyOpened} already opened)`);
                
                const autosellVariant = sellMethod === 'tokens' ? 'tokens' : 'money';
                
                // Open cases in batches until we run out
                let keepProcessing = true;
                
                let batchCounter = 0;
                while(keepProcessing) {
                    const caseInv2 = await safeFetchJson('/api/cases');
                    const targetItem2 = caseInv2.find(item => item._id === caseId);
                    
                    if(!targetItem2 || targetItem2.amount <= 0) {
                        keepProcessing = false;
                        break;
                    }
                    
                    const remainingCases = targetItem2.amount;
                    // Calculate progress based on estimated total
                    const casesOpened = estimatedTotal - remainingCases;
                    const progressPercent = estimatedTotal > 0 ? Math.floor((casesOpened / estimatedTotal) * 100) : 0;
                    
                    const meDataBatch = await safeFetchJson('/api/me');
                    const multiplier = meDataBatch.maxCaseOpenMultiplier || 1;
                    const caseOpenCount = meDataBatch.caseOpenCount || 1;
                    const batchSize = Math.min(caseOpenCount, Math.ceil(targetItem2.amount / multiplier));
                    const totalCasesInBatch = batchSize * multiplier;
                    
                    reportRemote(baseUrl, me._id, `PROGRESS:${casesOpened}:${estimatedTotal}:${remainingCases}:${progressPercent}:${batchSize}x${multiplier}`);
                    
                    // Every 10 batches, sell inventory for tokens if using tokens
                    batchCounter++;
                    if(batchCounter >= 10 && sellMethod === 'tokens') {
                        try {
                            await fetch('/api/inventory', {
                                method: 'DELETE',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ type: 'price', value: 999999, currency: 'tokens' })
                            });
                            batchCounter = 0;
                        } catch(e) {}
                    }
                    
                    const openEndpoint = caseType === 'capsule' ? '/api/open/capsule' : '/api/open/case';
                    const openPayload = {
                        id: caseId,
                        quickOpen: true,
                        autoOpenConfig: {
                            autosellActivated: true,
                            autosellVariant: autosellVariant
                        },
                        count: String(batchSize)
                    };
                    
                    try {
                        const openRes = await fetchWithRetry(openEndpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(openPayload)
                        }, 5);

                        if(!openRes.ok) break;
                        await new Promise(resolve => setTimeout(resolve, 900));
                    } catch(e) {
                        break;
                    }
                }
                
                // Final sell for tokens if needed
                if(sellMethod === 'tokens') {
                    try {
                        await fetch('/api/inventory', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ type: 'price', value: 999999, currency: 'tokens' })
                        });
                    } catch(e) {}
                }
                
                const finalProgress = estimatedTotal > 0 ? Math.floor(((estimatedTotal - 0) / estimatedTotal) * 100) : 100;
                reportRemote(baseUrl, me._id, `PROGRESS:${estimatedTotal}:${estimatedTotal}:0:${finalProgress}`);
                reportRemote(baseUrl, me._id, `Main: Resume convert complete!`);
                
                // Mark convert as inactive
                window.__CONVERT_ACTIVE = false;
                
                setTimeout(() => scanInventory(), 2000);
            } catch(e) {
                reportRemote(baseUrl, me._id, `Main: ERROR during resume: ${e.message}`);
                window.__CONVERT_ACTIVE = false;
            }
            return;
        }

        if(cmd.type === 'convert_cases') {
            const caseId = cmd.caseId;
            const budget = Number(cmd.budget || 0);
            const casePrice = Number(cmd.casePrice || 0);
            const sellMethod = cmd.sellMethod;
            const caseType = cmd.caseType || 'case';
            const autosellVariant = sellMethod === 'tokens' ? 'tokens' : 'money';
            const maxAttempts = 3;
            window.__CONVERT_ACTIVE = true;

            const openOwnedCases = async (initialCaseCount) => {
                let totalOpened = 0;
                let batchCounter = 0;
                while (true) {
                    const caseInv = await safeFetchJson('/api/cases');
                    const targetItem = caseInv.find(item => item._id === caseId);
                    if (!targetItem || targetItem.amount <= 0) break;

                    const remainingCases = targetItem.amount;
                    const casesOpened = initialCaseCount - remainingCases;
                    const progressPercent = initialCaseCount > 0 ? Math.floor((casesOpened / initialCaseCount) * 100) : 0;

                    const meDataBatch = await safeFetchJson('/api/me');
                    const multiplier = meDataBatch.maxCaseOpenMultiplier || 1;
                    const caseOpenCount = meDataBatch.caseOpenCount || 1;
                    const batchSize = Math.min(caseOpenCount, Math.ceil(targetItem.amount / multiplier));
                    const totalCasesInBatch = batchSize * multiplier;
                    reportRemote(baseUrl, me._id, `PROGRESS:${casesOpened}:${initialCaseCount}:${remainingCases}:${progressPercent}:${batchSize}x${multiplier}`);

                    batchCounter++;
                    if (batchCounter >= 10 && sellMethod === 'tokens') {
                        try {
                            await fetch('/api/inventory', {
                                method: 'DELETE',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ type: 'price', value: 999999, currency: 'tokens' })
                            });
                            reportRemote(baseUrl, me._id, `Main: Sold inventory for tokens (batch ${batchCounter})`);
                        } catch (_) {}
                        batchCounter = 0;
                    }

                    const openEndpoint = caseType === 'capsule' ? '/api/open/capsule' : '/api/open/case';
                    const openRes = await fetchWithRetry(openEndpoint, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            id: caseId,
                            quickOpen: true,
                            autoOpenConfig: { autosellActivated: true, autosellVariant },
                            count: String(batchSize)
                        })
                    }, 5);
                    if (!openRes.ok) {
                        throw new Error(`Open failed (${openRes.status})`);
                    }
                    totalOpened += totalCasesInBatch;
                    await new Promise(resolve => setTimeout(resolve, 900));
                }

                if (sellMethod === 'tokens') {
                    try {
                        await fetch('/api/inventory', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ type: 'price', value: 999999, currency: 'tokens' })
                        });
                        reportRemote(baseUrl, me._id, `Main: Final sell for tokens complete`);
                    } catch (_) {}
                }

                reportRemote(baseUrl, me._id, `PROGRESS:${initialCaseCount}:${initialCaseCount}:0:100`);
                reportRemote(baseUrl, me._id, `Main: Convert complete! Opened ${totalOpened} cases. Autosell: ${sellMethod}`);
            };

            reportRemote(baseUrl, me._id, `Main: Starting convert: ${cmd.caseName} (Budget: $${budget}, Sell: ${sellMethod})`);
            try {
                const startMe = await safeFetchJson('/api/me');
                const tokensStart = Number(startMe.tokens) || 0;
                const moneyStart = Number(startMe.money) || 0;
                reportRemote(baseUrl, me._id, `CONVERT_START:${tokensStart},${moneyStart}`);
            } catch (_) {}
            let lastErr = 'Unknown convert error';
            try {
                for (let attempt = 1; attempt <= maxAttempts; attempt++) {
                    try {
                        let caseInv = await safeFetchJson('/api/cases');
                        let targetItem = caseInv.find(item => item._id === caseId);
                        let ownedBefore = Number(targetItem?.amount || 0);

                        if (ownedBefore <= 0) {
                            const meData = await safeFetchJson('/api/me');
                            const currentMoney = Number(meData.money || 0);
                            const maxAffordable = casePrice > 0 ? Math.floor(Math.min(budget, currentMoney) / casePrice) : 0;
                            if (maxAffordable <= 0) {
                                throw new Error(`Cannot afford any cases. Need $${casePrice}, have $${currentMoney}`);
                            }
                            reportRemote(baseUrl, me._id, `Main: Attempt ${attempt}/${maxAttempts}: Buying ${maxAffordable} cases...`);
                            const buyResult = await buyCasesRobust(caseId, caseType, maxAffordable);
                            if(!buyResult.ok) {
                                throw new Error(`Failed to buy cases. Status: ${buyResult.status}`);
                            }
                            reportRemote(baseUrl, me._id, `Main: Bought ${buyResult.bought} cases.`);
                            caseInv = await safeFetchJson('/api/cases');
                            targetItem = caseInv.find(item => item._id === caseId);
                        } else {
                            reportRemote(baseUrl, me._id, `Main: Attempt ${attempt}/${maxAttempts}: Found ${ownedBefore} existing cases. Starting open phase...`);
                            const meData = await safeFetchJson('/api/me');
                            const currentMoney = Number(meData.money || 0);
                            const extraAffordable = casePrice > 0 ? Math.floor(Math.min(budget, currentMoney) / casePrice) : 0;
                            if (extraAffordable > 0) {
                                reportRemote(baseUrl, me._id, `Main: Attempt ${attempt}/${maxAttempts}: Optional rebuy ${extraAffordable} cases...`);
                                const buyResult = await buyCasesRobust(caseId, caseType, extraAffordable);
                                if (buyResult.ok && buyResult.bought > 0) {
                                    reportRemote(baseUrl, me._id, `Main: Added ${buyResult.bought} more cases before opening.`);
                                    caseInv = await safeFetchJson('/api/cases');
                                    targetItem = caseInv.find(item => item._id === caseId);
                                }
                            }
                        }

                        const initialCaseCount = Number(targetItem?.amount || 0);
                        if (initialCaseCount <= 0) {
                            throw new Error('No target cases available to open after buy/retry');
                        }
                        reportRemote(baseUrl, me._id, `Main: Opening cases with autosell: ${sellMethod}...`);
                        await openOwnedCases(initialCaseCount);
                        window.__CONVERT_ACTIVE = false;
                        setTimeout(() => scanInventory(), 2000);
                        return;
                    } catch (attemptErr) {
                        lastErr = attemptErr?.message || String(attemptErr);
                        reportRemote(baseUrl, me._id, `Main: Attempt ${attempt}/${maxAttempts} failed: ${lastErr}`);
                        if (attempt < maxAttempts) {
                            await new Promise(r => setTimeout(r, 1200));
                        }
                    }
                }
                reportRemote(baseUrl, me._id, `CONVERT_FAILED:${lastErr}`);
            } finally {
                window.__CONVERT_ACTIVE = false;
            }
        }

        // COMMAND: JOIN AND CONFIRM TRADE (For Main Account)
        if(cmd.type === 'join_and_confirm') {
            const tradeId = cmd.tradeId;
            if(!tradeId) {
                reportRemote(baseUrl, me._id, "Main: ERROR - No trade ID provided");
                return;
            }

            reportRemote(baseUrl, me._id, `Main: Received trade command for ${tradeId}`);
            const socket = getCapturedSocket();
            if (!socket) { 
                reportRemote(baseUrl, me._id, "Main: ERROR - No socket found! Refresh page."); 
                return; 
            }

            reportRemote(baseUrl, me._id, `Main: 1/4 Joining trade ${tradeId}...`);
            sendRaw(socket, "watchTrade", tradeId);

            // Wait 7 seconds to let Alt add skins/tokens and accept (increased from 5)
            setTimeout(() => {
                reportRemote(baseUrl, me._id, "Main: 2/4 Accepting trade...");
                sendRaw(socket, "tradeAccept", { userId: me._id, tradeId: tradeId });

                // Wait 2 seconds before confirming (increased from 1)
                setTimeout(() => {
                    reportRemote(baseUrl, me._id, "Main: 3/4 Confirming trade...");
                    sendRaw(socket, "tradeConfirm", { userId: me._id, tradeId: tradeId });

                    setTimeout(() => {
                        reportRemote(baseUrl, me._id, "Main: 4/4 Trade completed!");
                        scanInventory(); // Update inventory after trade
                    }, 1000);

                }, 2000);
            }, 7000);
        }

        // --- Standard functions (if you want to use Main as an Alt too) ---
        if(cmd.type === 'toggle_favorite') {
            try {
                await fetch('https://case-clicker.com/api/inventory/skin/favorite', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ skinId: cmd.skinId, isFavorite: cmd.isFavorite, inStorageUnit: false })
                });
                scanInventory();
            } catch(e) {}
        }

        if(cmd.type === 'sell_tokens' || cmd.type === 'sell_money') {
            const url = cmd.type === 'sell_tokens' ? 'https://case-clicker.com/api/casino/skinToTokens' : 'https://case-clicker.com/api/inventory/sell';
            try {
                await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ skinIds: cmd.skinIds })
                });
                scanInventory();
            } catch(e) {}
        }
    }
})();



