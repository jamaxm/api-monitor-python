#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/bootstrap-server.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$SCRIPT_DIR/api-monitor.service" || ! -f "$SCRIPT_DIR/deploy-api-monitor.sh" ]]; then
  echo "api-monitor.service and deploy-api-monitor.sh must be next to this script." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip sudo

if ! id api-monitor >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /opt/api-monitor --shell /usr/sbin/nologin api-monitor
fi
if ! id api-deploy >/dev/null 2>&1; then
  useradd --create-home --user-group --shell /bin/bash api-deploy
fi

install -d -o root -g api-monitor -m 0750 /opt/api-monitor /opt/api-monitor/releases
install -d -o api-deploy -g api-deploy -m 0700 /home/api-deploy/.ssh /home/api-deploy/deploy
install -o root -g root -m 0644 "$SCRIPT_DIR/api-monitor.service" /etc/systemd/system/api-monitor.service
install -o root -g root -m 0750 "$SCRIPT_DIR/deploy-api-monitor.sh" /usr/local/sbin/deploy-api-monitor

cat > /etc/sudoers.d/api-monitor-deploy <<'SUDOERS'
api-deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-api-monitor
SUDOERS
chmod 0440 /etc/sudoers.d/api-monitor-deploy
visudo -cf /etc/sudoers.d/api-monitor-deploy

if [[ ! -f /etc/api-monitor.env ]]; then
  cat > /etc/api-monitor.env <<'ENVFILE'
# Replace DB_PASSWORD before the first deployment. Use a strong password.
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=api_monitor
DB_USER=api_monitor_app
DB_PASSWORD=CHANGE_ME
API_URL=https://jsonplaceholder.typicode.com/posts/1
INTERVAL_SECONDS=300
REQUEST_TIMEOUT_SECONDS=20
ENVFILE
fi
chown root:root /etc/api-monitor.env
chmod 0600 /etc/api-monitor.env

systemctl daemon-reload
systemctl enable api-monitor

echo "Server bootstrap complete. Configure /etc/api-monitor.env and add the GitHub Actions public key to /home/api-deploy/.ssh/authorized_keys."
