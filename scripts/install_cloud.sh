#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-bitcoin-trader}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_PATH="${PROJECT_DIR}/deploy/bitcoin-trader.service.template"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$(id -un)}"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
START_SERVICE=0

for arg in "$@"; do
  case "$arg" in
    --start)
      START_SERVICE=1
      ;;
    -h|--help)
      echo "Usage: bash scripts/install_cloud.sh [--start]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required. Please install sudo or run on a standard Ubuntu server." >&2
  exit 1
fi

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "Missing service template: $TEMPLATE_PATH" >&2
  exit 1
fi

echo "[1/6] Installing OS packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git python3 python3-venv python3-pip
else
  echo "This installer currently supports Ubuntu/Debian servers with apt-get." >&2
  exit 1
fi

echo "[2/6] Creating Python virtual environment"
cd "$PROJECT_DIR"
python3 -m venv .venv
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "[3/6] Preparing .env"
if [ ! -f "${PROJECT_DIR}/.env" ]; then
  cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
  chmod 600 "${PROJECT_DIR}/.env"
  echo "Created .env from .env.example. Edit it before starting the service."
else
  chmod 600 "${PROJECT_DIR}/.env"
  echo ".env already exists. Permissions set to 600."
fi

echo "[4/6] Installing systemd service: ${SERVICE_NAME}"
tmp_service="$(mktemp)"
sed \
  -e "s#__USER__#${RUN_USER}#g" \
  -e "s#__PROJECT_DIR__#${PROJECT_DIR}#g" \
  -e "s#__PYTHON__#${PYTHON_BIN}#g" \
  "$TEMPLATE_PATH" > "$tmp_service"
sudo install -m 0644 "$tmp_service" "$SERVICE_PATH"
rm -f "$tmp_service"

echo "[5/6] Reloading systemd"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo "[6/6] Done"
echo
echo "Next steps:"
echo "  1. Edit API keys and settings: nano ${PROJECT_DIR}/.env"
echo "  2. Register this server's public IP in Upbit Open API allowlist."
echo "  3. Test once: ${PYTHON_BIN} ${PROJECT_DIR}/server_bot.py"
echo "  4. Start service: sudo systemctl start ${SERVICE_NAME}"
echo "  5. Watch logs: journalctl -u ${SERVICE_NAME} -f"

if [ "$START_SERVICE" -eq 1 ]; then
  echo
  echo "Starting ${SERVICE_NAME}"
  sudo systemctl start "$SERVICE_NAME"
  sudo systemctl status "$SERVICE_NAME" --no-pager
fi
