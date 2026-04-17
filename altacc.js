// ==UserScript==
// @name         CC Hub Alt Worker
// @namespace    https://github.com/Mikmail02/Alt-manager
// @version      5.0.0
// @description  Alt worker for Case Clicker Hub desktop app
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
// ==/UserScript==

(function() {
    'use strict';

    // --- 1. SOCKET FELLE (MÅ KJØRE FØRST) ---
    function injectTrap() {
        const script = document.createElement('script');
        script.textContent = `
            (() => {
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
                Object.assign(window.WebSocket, OriginalWebSocket);
                window.WebSocket.prototype = OriginalWebSocket.prototype;
            })();
        `;
        (document.head || document.documentElement).appendChild(script);
    }
    injectTrap();

    let inventoryCache = [];
    let isScanning = false;
    let cachedProfileImage = null;
    let cachedProfileMeta = null;
    let lastSocketCheck = Date.now();
    let socketCheckInterval = null;
    let heartbeatInterval = null;
    let heartbeatInFlight = false;
    let consecutiveSocketFailures = 0;
    let lastHeartbeatSuccess = Date.now();
    let lastRefreshAt = 0;
    let statusFetchInFlight = false;
    let statusLastFetchAt = 0;
    let statusCasePriceMap = null;
    let statusCasePriceMapAt = 0;
    let lastSocketWarnLogAt = 0;
    let isConnected = false;
    let consecutiveHeartbeatFailures = 0;
    const HEARTBEAT_FAILURES_BEFORE_STOP = 5;
    const SOCKET_CHECK_MS = 10000;
    const SOCKET_FAILURES_BEFORE_REFRESH = 3;
    const REFRESH_GRACE_MS = 90000;
    let connectedSince = 0;
    const HEARTBEAT_TIMEOUT_MS = 180000;
    const REFRESH_COOLDOWN_MS = 300000;
    const HEARTBEAT_INTERVAL_MS = 8000;
    const STATUS_PANEL_TICK_MS = 1000;
    const STATUS_NETWORK_MS = 20000;
    const STATUS_CASE_CACHE_MS = 300000;
    const MAX_LOG_LINES = 250;

    // Integrated clicker (replaces separate script)
    let clickTimer = null;
    let clickReqCount = 0;
    const CLICK_INTERVAL_MS = 60100;
    const CLICKS_PER_REQ = 500;
    const VAULT_EVERY_N_REQ = 1;
    const CLICK_KEY = 'am.click.enabled.alt';
    const VAULT_KEY = 'am.vault.enabled.alt';
    const NUKE_KEY = 'am.nuke.enabled.alt';
    let totalErrors = 0;
    let nextClickAt = 0;
    let statusUiInterval = null;
    let currentTab = 'console';

    window.addEventListener('load', () => {
        initUI();
        setTimeout(scanInventory, 3000);
        startSocketMonitoring();
    });

    function initUI() {
        if (!document.getElementById('am-ui-style')) {
            const st = document.createElement('style');
            st.id = 'am-ui-style';
            st.textContent = `
                body.am-nuked > *:not(#alt-worker-ui) { display: none !important; }
            `;
            document.documentElement.appendChild(st);
        }

        const div = document.createElement('div');
        div.id = 'alt-worker-ui';
        div.style = "position:fixed; bottom:16px; right:16px; background:#0b0b0b; color:#d4d4d4; padding:0; border-radius:10px; z-index:2147483647; font-family:Inter,monospace; width: 460px; border:1px solid #2c2c2c; font-size:12px; opacity:0.98; box-shadow: 0 12px 40px rgba(0,0,0,0.6); display:flex; flex-direction:column; overflow:hidden;";

        div.innerHTML = `
            <div id="drag-handle" style="padding: 10px; background:#131313; border-bottom:1px solid #2c2c2c; cursor:move; text-align:center; color:#10b981; font-weight:700; user-select:none;">
                Alt Manager Worker
            </div>
            <div style="padding:12px;">
                <div style="display:flex; gap:8px; margin-bottom:8px;">
                    <input id="ngrok_url" placeholder="Lim inn fra CC Hub tray > Kopier worker-link" style="flex:1; padding:8px; background:#1a1a1a; border:1px solid #333; color:#fff; border-radius:6px; box-sizing:border-box;">
                    <button id="btn_link" style="min-width:110px; background:#10b981; border:none; padding:8px; color:#000; cursor:pointer; font-weight:700; border-radius:6px;">CONNECT</button>
                </div>
                <div style="display:flex; gap:8px; margin-bottom:8px;">
                    <button id="btn_ping" style="flex:1; background:#2563eb; border:none; padding:8px; color:#fff; cursor:pointer; font-weight:700; border-radius:6px;">TEST LINK</button>
                    <span id="conn_state" style="display:flex; align-items:center; justify-content:center; min-width:120px; background:#1a1a1a; border:1px solid #333; border-radius:6px; color:#9ca3af; font-weight:700;">DISCONNECTED</span>
                </div>
                <div style="display:flex; gap:10px; margin-top:6px; font-size:11px;">
                    <label style="display:flex; align-items:center; gap:4px; cursor:pointer;">
                        <input id="opt_click" type="checkbox"> Auto Click
                    </label>
                    <label style="display:flex; align-items:center; gap:4px; cursor:pointer;">
                        <input id="opt_vault" type="checkbox"> Auto Vault
                    </label>
                    <label style="display:flex; align-items:center; gap:4px; cursor:pointer;">
                        <input id="opt_nuke" type="checkbox"> Nuke UI
                    </label>
                </div>
                <div style="display:flex; gap:6px; margin-top:10px;">
                    <button id="tab_console" style="flex:1; background:#1f2937; color:#fff; border:1px solid #374151; border-radius:6px; padding:6px; font-weight:700; cursor:pointer;">Console</button>
                    <button id="tab_status" style="flex:1; background:#111; color:#9ca3af; border:1px solid #333; border-radius:6px; padding:6px; font-weight:700; cursor:pointer;">Status</button>
                </div>
                <div id="status" style="margin-top:8px; color:#cfcfcf; border-top:1px solid #2c2c2c; padding-top:8px; height:220px; overflow-y:auto; line-height:1.45; font-family:JetBrains Mono,monospace; font-size:11px;">Waiting...</div>
                <div id="status_panel" style="display:none; margin-top:8px; border-top:1px solid #2c2c2c; padding-top:10px; height:220px; overflow-y:auto; font-family:JetBrains Mono,monospace; font-size:12px; line-height:1.6;">
                    <div style="display:flex; justify-content:space-between;"><span>Waiting...</span><span id="st_timer">--</span></div>
                    <div style="display:flex; justify-content:space-between;"><span>Total Clicks</span><span id="st_clicks">0</span></div>
                    <div style="display:flex; justify-content:space-between;"><span>Effective CPS</span><span id="st_cps">0.00</span></div>
                    <div style="display:flex; justify-content:space-between;"><span>Current Networth</span><span id="st_nw">-</span></div>
                    <div style="display:flex; justify-content:space-between;"><span>Case Sell Value (70%)</span><span id="st_case_val">$0</span></div>
                    <div style="display:flex; justify-content:space-between;"><span>Total Errors</span><span id="st_errors">0</span></div>
                </div>
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
        document.getElementById('tab_console').onclick = () => switchTab('console');
        document.getElementById('tab_status').onclick = () => switchTab('status');
        switchTab('console');
        if (statusUiInterval) clearInterval(statusUiInterval);
        statusUiInterval = setInterval(updateStatusPanel, STATUS_PANEL_TICK_MS);

        document.getElementById('btn_link').onclick = () => {
            if (isConnected) {
                disconnect();
                return;
            }
            const url = normalizeBaseUrl(document.getElementById('ngrok_url').value);
            if (!url) { log("Enter a valid URL, then click CONNECT."); return; }
            GM_setValue('ngrok_url', url);
            startLoop(url);
        };
        document.getElementById('btn_ping').onclick = () => pingLink();
    }

    function log(msg) {
        const s = document.getElementById('status');
        if(!s) return;
        const time = new Date().toLocaleTimeString().split(' ')[0];
        s.insertAdjacentHTML('afterbegin', `<div><span style="color:#555;">[${time}]</span> ${msg}</div>`);
        while (s.childNodes.length > MAX_LOG_LINES) s.removeChild(s.lastChild);
    }

    function switchTab(tab) {
        currentTab = tab;
        const consoleBtn = document.getElementById('tab_console');
        const statusBtn = document.getElementById('tab_status');
        const consoleView = document.getElementById('status');
        const statusView = document.getElementById('status_panel');
        if (!consoleBtn || !statusBtn || !consoleView || !statusView) return;
        if (tab === 'status') {
            statusBtn.style.background = '#1f2937';
            statusBtn.style.color = '#fff';
            statusBtn.style.border = '1px solid #374151';
            consoleBtn.style.background = '#111';
            consoleBtn.style.color = '#9ca3af';
            consoleBtn.style.border = '1px solid #333';
            consoleView.style.display = 'none';
            statusView.style.display = 'block';
        } else {
            consoleBtn.style.background = '#1f2937';
            consoleBtn.style.color = '#fff';
            consoleBtn.style.border = '1px solid #374151';
            statusBtn.style.background = '#111';
            statusBtn.style.color = '#9ca3af';
            statusBtn.style.border = '1px solid #333';
            consoleView.style.display = 'block';
            statusView.style.display = 'none';
        }
    }

    async function updateStatusPanel() {
        const timerEl = document.getElementById('st_timer');
        const clicksEl = document.getElementById('st_clicks');
        const cpsEl = document.getElementById('st_cps');
        const nwEl = document.getElementById('st_nw');
        const caseValEl = document.getElementById('st_case_val');
        const errEl = document.getElementById('st_errors');
        if (!timerEl || !clicksEl || !cpsEl || !nwEl || !caseValEl || !errEl) return;

        const now = Date.now();
        const remainMs = Math.max(0, nextClickAt - now);
        const sec = Math.ceil(remainMs / 1000);
        timerEl.innerText = GM_getValue(CLICK_KEY, true) ? `${sec}s` : '--';
        clicksEl.innerText = (clickReqCount * CLICKS_PER_REQ).toLocaleString();
        cpsEl.innerText = (CLICKS_PER_REQ / (CLICK_INTERVAL_MS / 1000)).toFixed(2);
        errEl.innerText = totalErrors.toString();

        if (currentTab !== 'status' || document.visibilityState !== 'visible') return;
        if (statusFetchInFlight) return;
        if ((Date.now() - statusLastFetchAt) < STATUS_NETWORK_MS) return;
        statusFetchInFlight = true;
        statusLastFetchAt = Date.now();
        try {
            const meRes = await fetch('/api/me', { cache: 'no-cache' });
            if (meRes.ok) {
                const me = await meRes.json();
                nwEl.innerText = Math.floor(me.networth || 0).toLocaleString();
            }
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

    const DEFAULT_BACKEND_URL = 'https://127.0.0.1:5000';
    const TOKEN_KEY = 'cchub_token';

    function normalizeBaseUrl(url) {
        let v = (url || '').trim();
        if (!v) return '';
        // Accept pasted "https://host:port#TOKEN" from CC Hub tray menu.
        const hashIdx = v.indexOf('#');
        if (hashIdx !== -1) {
            const token = v.slice(hashIdx + 1).trim();
            if (token) GM_setValue(TOKEN_KEY, token);
            v = v.slice(0, hashIdx).trim();
        }
        if (!v) return '';
        if (!/^https?:\/\//i.test(v)) v = `https://${v}`;
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

    function setConnState(ok) {
        const el = document.getElementById('conn_state');
        if (!el) return;
        el.textContent = ok ? 'CONNECTED' : 'DISCONNECTED';
        el.style.color = ok ? '#10b981' : '#9ca3af';
        el.style.borderColor = ok ? '#10b98155' : '#333';
        const btn = document.getElementById('btn_link');
        if (btn) {
            btn.textContent = isConnected ? 'DISCONNECT' : 'CONNECT';
            btn.style.background = isConnected ? '#b91c1c' : '#10b981';
        }
    }

    function disconnect() {
        if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
            heartbeatInterval = null;
        }
        consecutiveSocketFailures = 0;
        consecutiveHeartbeatFailures = 0;
        connectedSince = 0;
        isConnected = false;
        setConnState(false);
        log("Disconnected. Change the URL above and click CONNECT to use a different link.");
    }

    function pingLink() {
        const baseUrl = normalizeBaseUrl(document.getElementById('ngrok_url').value);
        if (!baseUrl) {
            log('No URL set.');
            return;
        }
        gmRequest({
            method: "GET",
            url: `${baseUrl}/api/settings`,
            onload: (res) => {
                const ok = res.status >= 200 && res.status < 300;
                setConnState(ok);
                log(`Ping ${ok ? 'OK' : 'FAIL'} (${res.status}) -> ${baseUrl}`);
            },
            onerror: () => {
                setConnState(false);
                log(`Ping ERROR -> ${baseUrl}`);
            }
        });
    }

    function startLoop(url) {
        const baseUrl = normalizeBaseUrl(url);
        if (!baseUrl) {
            log("Invalid URL.");
            setConnState(false);
            return;
        }
        isConnected = false;
        consecutiveHeartbeatFailures = 0;
        setConnState(false);
        if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
            heartbeatInterval = null;
        }
        log("Connecting...");
        connectedSince = Date.now();
        heartbeatInterval = setInterval(function() { heartbeat(baseUrl); }, HEARTBEAT_INTERVAL_MS);
        heartbeat(baseUrl);
        toggleClickLoop();
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
            for (let tries = 0; tries < 3; tries++) {
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
                    await new Promise(r => setTimeout(r, 180));
                } catch (_) {}
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

    /** Fetch URL and parse JSON safely. Retries on HTML response or 429. Avoids "Unexpected token '<'" when server returns error page. */
    async function safeFetchJson(url, options = {}, retries = 2) {
        const opts = { cache: 'no-cache', ...options };
        for (let attempt = 0; attempt <= retries; attempt++) {
            const res = await fetch(url, opts);
            const text = await res.text();
            if (res.status === 429 && attempt < retries) {
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
        log("LOGG: " + msg);
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

    function canRefreshNow() {
        const now = Date.now();
        if (now - lastRefreshAt < REFRESH_COOLDOWN_MS) return false;
        lastRefreshAt = now;
        return true;
    }

    async function runClickBatch() {
        if (!GM_getValue(CLICK_KEY, true)) return;
        nextClickAt = Date.now() + CLICK_INTERVAL_MS;
        try {
            const res = await fetch('/api/caseClick', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ clicks: CLICKS_PER_REQ })
            });
            if (!res.ok) {
                totalErrors++;
                log(`Click batch failed: ${res.status}`);
                return;
            }
            clickReqCount++;
            if (GM_getValue(VAULT_KEY, true) && clickReqCount % VAULT_EVERY_N_REQ === 0) {
                await fetch('/api/vault', { method: 'POST' });
            }
        } catch (e) {
            totalErrors++;
            log(`Click batch error: ${e.message}`);
        }
    }

    function toggleClickLoop() {
        if (clickTimer) {
            clearInterval(clickTimer);
            clickTimer = null;
        }
        if (!GM_getValue(CLICK_KEY, true)) return;
        runClickBatch();
        clickTimer = setInterval(runClickBatch, CLICK_INTERVAL_MS);
    }

    // --- WEBSOCKET MONITORING & AUTO-REFRESH ---
    function startSocketMonitoring() {
        if (socketCheckInterval) clearInterval(socketCheckInterval);
        socketCheckInterval = setInterval(() => {
            const socket = getCapturedSocket();
            const now = Date.now();
            
            // Check if socket is missing or disconnected
            if (!socket || socket.readyState !== 1) {
                consecutiveSocketFailures++;
                if (now - lastSocketWarnLogAt > 60000) {
                    log(`Socket disconnected (${consecutiveSocketFailures} checks)`);
                    lastSocketWarnLogAt = now;
                }
                
                if (consecutiveSocketFailures >= SOCKET_FAILURES_BEFORE_REFRESH && !window.__CONVERT_ACTIVE && !window.__BOOSTER_ACTIVE && canRefreshNow()) {
                    log("Socket failed " + SOCKET_FAILURES_BEFORE_REFRESH + " times in a row. Refreshing page...");
                    setTimeout(() => {
                        window.location.reload();
                    }, 2000);
                    return;
                }
            } else {
                consecutiveSocketFailures = 0;
            }
            
            // Check if heartbeat hasn't succeeded in a while.
            const heartbeatTimeout = now - lastHeartbeatSuccess;
            if (heartbeatTimeout > HEARTBEAT_TIMEOUT_MS && !window.__CONVERT_ACTIVE && !window.__BOOSTER_ACTIVE && canRefreshNow()) {
                log("Heartbeat timeout. Refreshing page...");
                setTimeout(() => {
                    window.location.reload();
                }, 2000);
            }
        }, SOCKET_CHECK_MS);
    }

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

    // --- INVENTORY ---
    async function scanInventory() {
        if(isScanning) return;
        isScanning = true;
        inventoryCache = [];
        log("Scanning inventory...");
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
            log(`Scan: ${inventoryCache.length} items`);

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

    // --- HEARTBEAT ---
    async function heartbeat(baseUrl) {
        if (heartbeatInFlight) return;
        heartbeatInFlight = true;
        let me = { _id: null, name: "Ukjent" };
        try {
            const tokenRes = await fetch('/api/auth/token');
            if(tokenRes.status !== 200) { heartbeatInFlight = false; return; }
            const json = await tokenRes.json();
            const jwt = parseJwt(json.token);
            if(jwt) { me._id = jwt.id || jwt.sub; me.name = jwt.name; }
            if(!me._id) { heartbeatInFlight = false; return; }

            const statsRes = await fetch('/api/me');
            if(statsRes.status !== 200) { heartbeatInFlight = false; return; }
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
                method: "POST", url: `${baseUrl}/api/heartbeat`,
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify(payload),
                onload: (res) => {
                    heartbeatInFlight = false;
                    if(res.status !== 200) {
                        totalErrors++;
                        isConnected = false;
                        consecutiveHeartbeatFailures++;
                        setConnState(false);
                        if (consecutiveHeartbeatFailures >= HEARTBEAT_FAILURES_BEFORE_STOP) {
                            if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
                            log("Link unreachable. Change URL above and click CONNECT.");
                            return;
                        }
                        log("Heartbeat failed (" + consecutiveHeartbeatFailures + "/" + HEARTBEAT_FAILURES_BEFORE_STOP + ")");
                        return;
                    }
                    consecutiveHeartbeatFailures = 0;
                    isConnected = true;
                    setConnState(true);
                    lastHeartbeatSuccess = Date.now();
                    consecutiveSocketFailures = 0;
                    const json = JSON.parse(res.responseText);
                    if(json.commands && json.commands.length > 0) {
                        json.commands.forEach(cmd => {
                            // Check for refresh command from backend
                            // Don't refresh if we're in the middle of a convert job
                            if(cmd.type === 'refresh_page') {
                                if(window.__CONVERT_ACTIVE || window.__BOOSTER_ACTIVE) {
                                    log("Skipping refresh - convert job in progress");
                                    return;
                                }
                                const grace = Date.now() - connectedSince;
                                if (grace < REFRESH_GRACE_MS) {
                                    log("Skipping refresh - connected recently (" + Math.round(grace/1000) + "s ago)");
                                    return;
                                }
                                log("Received refresh command. Refreshing...");
                                setTimeout(() => window.location.reload(), 1000);
                                return;
                            }
                            executeCommand(cmd, me, baseUrl);
                        });
                    }
                },
                onerror: () => {
                    heartbeatInFlight = false;
                    totalErrors++;
                    isConnected = false;
                    consecutiveHeartbeatFailures++;
                    setConnState(false);
                    if (consecutiveHeartbeatFailures >= HEARTBEAT_FAILURES_BEFORE_STOP) {
                        if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
                        log("Link unreachable. Change URL above and click CONNECT.");
                        return;
                    }
                    log("Heartbeat error (" + consecutiveHeartbeatFailures + "/" + HEARTBEAT_FAILURES_BEFORE_STOP + ")");
                }
            });
        } catch(e) { 
            console.error(e);
            heartbeatInFlight = false;
            consecutiveHeartbeatFailures++;
            isConnected = false;
            setConnState(false);
            if (consecutiveHeartbeatFailures >= HEARTBEAT_FAILURES_BEFORE_STOP) {
                if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
                log("Link unreachable. Change URL above and click CONNECT.");
                return;
            }
            log("Heartbeat exception (" + consecutiveHeartbeatFailures + "/" + HEARTBEAT_FAILURES_BEFORE_STOP + ")");
        }
    }

    function sendRaw(socket, eventName, data) {
        const payload = `42${JSON.stringify([eventName, data])}`;
        socket.send(payload);
    }

    // --- COMMANDS ---
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
            reportRemote(baseUrl, me._id, "Scanning inventory...");
            await scanInventory();
            reportRemote(baseUrl, me._id, `Inventory scan complete: ${inventoryCache.length} items`);
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
            const totalCasesFromJob = cmd.totalCases || 0;
            window.__CONVERT_ACTIVE = true;
            
            reportRemote(baseUrl, me._id, `Resuming convert job...`);
            
            try {
                // Get current case count
                const caseInv = await safeFetchJson('/api/cases');
                const targetItem = caseInv.find(item => item._id === caseId);
                
                if(!targetItem || targetItem.amount <= 0) {
                    reportRemote(baseUrl, me._id, `No cases remaining. Convert complete.`);
                    window.__CONVERT_ACTIVE = false;
                    return;
                }
                
                const remainingCasesStart = targetItem.amount;
                const estimatedTotal = totalCasesFromJob > 0 ? totalCasesFromJob : remainingCasesStart;
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
                        const openRes = await fetch(openEndpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(openPayload)
                        });
                        
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
                reportRemote(baseUrl, me._id, `Resume convert complete!`);
                
                // Mark convert as inactive
                window.__CONVERT_ACTIVE = false;
                
                setTimeout(() => scanInventory(), 2000);
            } catch(e) {
                reportRemote(baseUrl, me._id, `ERROR during resume: ${e.message}`);
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
                            reportRemote(baseUrl, me._id, `Sold inventory for tokens (batch ${batchCounter})`);
                        } catch (_) {}
                        batchCounter = 0;
                    }

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
                        reportRemote(baseUrl, me._id, `Final sell for tokens complete`);
                    } catch (_) {}
                }

                reportRemote(baseUrl, me._id, `PROGRESS:${initialCaseCount}:${initialCaseCount}:0:100`);
                reportRemote(baseUrl, me._id, `Convert complete! Opened ${totalOpened} cases. Autosell: ${sellMethod}`);
            };

            reportRemote(baseUrl, me._id, `Starting convert: ${cmd.caseName} (Budget: $${budget}, Sell: ${sellMethod})`);
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
                            reportRemote(baseUrl, me._id, `Attempt ${attempt}/${maxAttempts}: Buying ${maxAffordable} cases...`);
                            const buyResult = await buyCasesRobust(caseId, caseType, maxAffordable);
                            if(!buyResult.ok) {
                                throw new Error(`Failed to buy cases. Status: ${buyResult.status}`);
                            }
                            reportRemote(baseUrl, me._id, `Bought ${buyResult.bought} cases.`);
                            caseInv = await safeFetchJson('/api/cases');
                            targetItem = caseInv.find(item => item._id === caseId);
                        } else {
                            reportRemote(baseUrl, me._id, `Attempt ${attempt}/${maxAttempts}: Found ${ownedBefore} existing cases. Starting open phase...`);
                            const meData = await safeFetchJson('/api/me');
                            const currentMoney = Number(meData.money || 0);
                            const extraAffordable = casePrice > 0 ? Math.floor(Math.min(budget, currentMoney) / casePrice) : 0;
                            if (extraAffordable > 0) {
                                reportRemote(baseUrl, me._id, `Attempt ${attempt}/${maxAttempts}: Optional rebuy ${extraAffordable} cases...`);
                                const buyResult = await buyCasesRobust(caseId, caseType, extraAffordable);
                                if (buyResult.ok && buyResult.bought > 0) {
                                    reportRemote(baseUrl, me._id, `Added ${buyResult.bought} more cases before opening.`);
                                    caseInv = await safeFetchJson('/api/cases');
                                    targetItem = caseInv.find(item => item._id === caseId);
                                }
                            }
                        }

                        const initialCaseCount = Number(targetItem?.amount || 0);
                        if (initialCaseCount <= 0) {
                            throw new Error('No target cases available to open after buy/retry');
                        }
                        reportRemote(baseUrl, me._id, `Opening cases with autosell: ${sellMethod}...`);
                        await openOwnedCases(initialCaseCount);
                        window.__CONVERT_ACTIVE = false;
                        setTimeout(() => scanInventory(), 2000);
                        return;
                    } catch (attemptErr) {
                        lastErr = attemptErr?.message || String(attemptErr);
                        reportRemote(baseUrl, me._id, `Attempt ${attempt}/${maxAttempts} failed: ${lastErr}`);
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

        if(cmd.type === 'start_trade_socket') {
            const socket = getCapturedSocket();
            if (!socket) { reportRemote(baseUrl, me._id, "ERROR: No socket found (Refresh page!)."); return; }

            reportRemote(baseUrl, me._id, "1/4: Creating trade...");
            let tradeId;
            try {
                const r = await fetch('https://case-clicker.com/api/trading', { method: 'POST', headers: {'Content-Type': 'application/json'} });
                const j = await r.json();
                tradeId = j._id;
            } catch(e) { reportRemote(baseUrl, me._id, "ERROR: API blocked."); return; }

            if(!tradeId) { reportRemote(baseUrl, me._id, "ERROR: No Trade ID."); return; }

            reportRemote(baseUrl, me._id, `2/4: Joining trade room...`);
            sendRaw(socket, "watchTrade", tradeId);

            // Send trade link immediately so main can join
            const link = `https://case-clicker.com/trading/${tradeId}`;
            GM_setClipboard(link);
            reportRemote(baseUrl, me._id, `Trade created: ${link}`);

            setTimeout(() => {
                let steps = [];
                if(cmd.sendTokens) {
                    steps.push(() => {
                        fetch('/api/me').then(r=>r.json()).then(s => {
                             const available = Math.floor(s.tokens || 0);
                             const requested = Number(cmd.tokenAmount || 0);
                             const amt = requested > 0 ? Math.min(available, Math.floor(requested)) : available;
                             if (amt <= 0) {
                                 reportRemote(baseUrl, me._id, `3/4: Token amount is 0. Skipping tokens.`);
                                 return;
                             }
                             reportRemote(baseUrl, me._id, `3/4: Adding ${amt} tokens...`);
                             sendRaw(socket, "tradeAddTokens", { userId: me._id, tokens: amt, tradeId: tradeId });
                        });
                    });
                }
                if(cmd.sendSkins && inventoryCache.length > 0) {
                    steps.push(() => {
                        const ids = inventoryCache.map(i => i._id);
                        reportRemote(baseUrl, me._id, `3/4: Adding ${ids.length} skins...`);
                        sendRaw(socket, "tradeAddSkins", { userId: me._id, skinIds: ids, tradeId: tradeId });
                    });
                }
                
                if(steps.length === 0) {
                    reportRemote(baseUrl, me._id, "WARNING: No tokens or skins to add!");
                }
                
                let stepDelay = 1000; // Increased delay for stability
                steps.forEach((fn, idx) => setTimeout(fn, idx * stepDelay));
                const finalDelay = (steps.length * stepDelay) + 1500; // More time before accept

                setTimeout(() => {
                    reportRemote(baseUrl, me._id, "4/4: Accepting trade...");
                    sendRaw(socket, "tradeAccept", { userId: me._id, tradeId: tradeId });
                    setTimeout(() => {
                         reportRemote(baseUrl, me._id, `SUCCESS! Trade link: ${link}`);
                    }, 600);
                }, finalDelay);
            }, 1000); // Increased initial delay
        }

        if(cmd.type === 'toggle_favorite') {
             // ... Samme logikk ...
            try {
                await fetch('https://case-clicker.com/api/inventory/skin/favorite', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ skinId: cmd.skinId, isFavorite: cmd.isFavorite, inStorageUnit: false })
                });
                scanInventory();
            } catch(e) {}
        }

        if(cmd.type === 'sell_tokens' || cmd.type === 'sell_money') {
            const url = cmd.type === 'sell_tokens' ? 'https://case-clicker.com/api/casino/skinToTokens' : 'https://case-clicker.com/api/inventory/sell';
            try {
                await fetch(url, {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ skinIds: cmd.skinIds })
                });
                scanInventory();
            } catch(e) {}
        }
    }
})();

