#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-data-collector}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. This uninstaller is for Linux systems using systemd." >&2
  exit 1
fi

sudo systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
sudo rm -f "$UNIT_PATH"
sudo systemctl daemon-reload

echo "Removed ${SERVICE_NAME}.service"
