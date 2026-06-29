#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-data-collector}"
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. This installer is for Linux systems using systemd." >&2
  exit 1
fi

if [ ! -x "$PYTHON" ]; then
  echo "Python executable not found or not executable: $PYTHON" >&2
  echo "Create the virtualenv and install requirements first." >&2
  exit 1
fi

if [ ! -f "$ROOT/src/download_server.py" ]; then
  echo "Could not find src/download_server.py under: $ROOT" >&2
  exit 1
fi

mkdir -p "$ROOT/logs"

sudo tee "$UNIT_PATH" >/dev/null <<SERVICE
[Unit]
Description=Data Collector Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${ROOT}
Environment=PYTHONPATH=${ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m uvicorn src.download_server:app --host ${HOST} --port ${PORT} --workers 1 --log-level info
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo "Installed and started ${SERVICE_NAME}.service"
echo "Dashboard: http://localhost:${PORT}/"
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
