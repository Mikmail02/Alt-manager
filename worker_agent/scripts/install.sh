#!/usr/bin/env bash
# CC Hub Worker Agent — VPS installer (Ubuntu 22.04+ / Debian 12+).
# Run as root (or via sudo). Idempotent — safe to re-run for updates.
set -euo pipefail

INSTALL_DIR="/opt/cchub-agent"
SERVICE_USER="cchub"
REPO_URL="${CCHUB_REPO_URL:-https://github.com/Mikmail02/Alt-manager.git}"
BRANCH="${CCHUB_BRANCH:-feat/worker-agent}"

echo "==> CC Hub Worker Agent installer"
echo "    install dir: $INSTALL_DIR"
echo "    repo:        $REPO_URL ($BRANCH)"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root (sudo $0)" >&2
    exit 1
fi

echo "==> apt: installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git ca-certificates curl

echo "==> creating service user ($SERVICE_USER)"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> fetching source"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" fetch --depth=1 origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
    mkdir -p "$INSTALL_DIR"
    git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

echo "==> creating virtualenv"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/worker_agent/requirements.txt"

echo "==> installing Chromium + system deps (this is the slow step)"
PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.playwright" \
    "$INSTALL_DIR/venv/bin/python" -m playwright install --with-deps chromium

echo "==> layout under $INSTALL_DIR"
# The agent runs with cwd=/opt/cchub-agent, so symlink the Python package up so
# `python -m agent` works without PYTHONPATH gymnastics.
ln -sfn "$INSTALL_DIR/worker_agent/agent" "$INSTALL_DIR/agent"

mkdir -p "$INSTALL_DIR/cookies" "$INSTALL_DIR/user_data"
if [[ ! -f "$INSTALL_DIR/config.toml" ]]; then
    cp "$INSTALL_DIR/worker_agent/config.example.toml" "$INSTALL_DIR/config.toml"
    echo "    >> wrote default config.toml — EDIT IT before starting the service"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

echo "==> installing systemd unit"
cp "$INSTALL_DIR/worker_agent/scripts/cchub-agent.service" /etc/systemd/system/
systemctl daemon-reload

echo
echo "Install complete. Next steps:"
echo "  1. sudo nano $INSTALL_DIR/config.toml    # fill in hub_url + token + alts"
echo "  2. Drop cookie JSON files into $INSTALL_DIR/cookies/"
echo "  3. sudo chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR/cookies"
echo "  4. sudo systemctl enable --now cchub-agent"
echo "  5. sudo journalctl -u cchub-agent -f       # watch logs"
