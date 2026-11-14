#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_BASE="/opt/api-monitor"
readonly APP_USER="api-monitor"
readonly APP_GROUP="api-monitor"
readonly SERVICE_NAME="api-monitor"

if [[ "$#" -ne 3 ]]; then
  echo "Usage: deploy-api-monitor /home/api-deploy/deploy/api-monitor-main.py /home/api-deploy/deploy/api-monitor-requirements.txt <40-char-git-sha>-<run-id>" >&2
  exit 2
fi

main_src="$1"
requirements_src="$2"
release_id="$3"

if [[ "$main_src" != "/home/api-deploy/deploy/api-monitor-main.py" || "$requirements_src" != "/home/api-deploy/deploy/api-monitor-requirements.txt" ]]; then
  echo "Unexpected upload paths" >&2
  exit 2
fi
if [[ ! "$release_id" =~ ^[0-9a-f]{40}-[0-9]+$ ]]; then
  echo "Invalid release identifier" >&2
  exit 2
fi
if [[ ! -f "$main_src" || -L "$main_src" || ! -f "$requirements_src" || -L "$requirements_src" ]]; then
  echo "Uploaded source files are missing or invalid" >&2
  exit 2
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "Service user $APP_USER does not exist. Run scripts/bootstrap-server.sh first." >&2
  exit 1
fi
if ! systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "systemd unit $SERVICE_NAME is not installed. Run scripts/bootstrap-server.sh first." >&2
  exit 1
fi

trap 'rm -f -- "$main_src" "$requirements_src"' EXIT

release_dir="$APP_BASE/releases/$release_id"
if [[ -e "$release_dir" ]]; then
  echo "Release already exists: $release_id" >&2
  exit 1
fi

install -d -o root -g "$APP_GROUP" -m 0750 "$release_dir"
install -o root -g "$APP_GROUP" -m 0640 "$main_src" "$release_dir/main.py"
install -o root -g "$APP_GROUP" -m 0640 "$requirements_src" "$release_dir/requirements.txt"

install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$release_dir/.venv"
runuser -u "$APP_USER" -- python3 -m venv "$release_dir/.venv"
runuser -u "$APP_USER" -- "$release_dir/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$release_dir/requirements.txt"
runuser -u "$APP_USER" -- "$release_dir/.venv/bin/python" -m pip check

chown -R root:"$APP_GROUP" "$release_dir/.venv"
chmod -R go-w "$release_dir/.venv"
chown root:"$APP_GROUP" "$release_dir"
chmod 0750 "$release_dir"

previous_target="$(readlink "$APP_BASE/current" 2>/dev/null || true)"
rm -f "$APP_BASE/current.new" "$APP_BASE/current.rollback"
ln -s "$release_dir" "$APP_BASE/current.new"
mv -Tf "$APP_BASE/current.new" "$APP_BASE/current"

if ! systemctl restart "$SERVICE_NAME"; then
  echo "Service restart command failed; attempting rollback" >&2
  rm -f "$APP_BASE/current.new" "$APP_BASE/current.rollback"
  if [[ -n "$previous_target" && -d "$previous_target" ]]; then
    ln -s "$previous_target" "$APP_BASE/current.rollback"
    mv -Tf "$APP_BASE/current.rollback" "$APP_BASE/current"
    systemctl restart "$SERVICE_NAME" || true
  else
    rm -f "$APP_BASE/current"
    systemctl stop "$SERVICE_NAME" || true
  fi
  journalctl -u "$SERVICE_NAME" -n 50 --no-pager >&2 || true
  exit 1
fi

sleep 3
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "Service did not become active; attempting rollback" >&2
  rm -f "$APP_BASE/current.new" "$APP_BASE/current.rollback"
  if [[ -n "$previous_target" && -d "$previous_target" ]]; then
    ln -s "$previous_target" "$APP_BASE/current.rollback"
    mv -Tf "$APP_BASE/current.rollback" "$APP_BASE/current"
    systemctl restart "$SERVICE_NAME" || true
  else
    rm -f "$APP_BASE/current"
    systemctl stop "$SERVICE_NAME" || true
  fi
  journalctl -u "$SERVICE_NAME" -n 50 --no-pager >&2 || true
  exit 1
fi

current_release="$(readlink -f "$APP_BASE/current")"
mapfile -t old_releases < <(
  find "$APP_BASE/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | awk 'NR > 5 { $1=""; sub(/^ /, ""); print }'
)
for old_release in "${old_releases[@]}"; do
  [[ -n "$old_release" && "$old_release" != "$current_release" ]] && rm -rf -- "$old_release"
done

echo "Deployment successful: $release_id"
systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,12p'
