#!/usr/bin/env bash
# Run after uploading and extracting the archive under /opt/study-agent.
set -euo pipefail
APP=/opt/study-agent
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo bash deploy.sh'; exit 1; fi
if [ "$(cd "$(dirname "$0")" && pwd -P)" != "$APP" ]; then echo 'Extract the project to /opt/study-agent first.'; exit 1; fi
apt-get update
apt-get install -y python3-venv python3-pip ca-certificates curl
if ! id studyagent >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$APP" --no-create-home --shell /usr/sbin/nologin studyagent
fi
if [ "$(id -u studyagent)" -eq 0 ]; then echo 'Invalid service user'; exit 1; fi
install -d -o studyagent -g studyagent -m 750 "$APP/data"
python3 -m venv "$APP/.venv"
if [ -d "$APP/wheelhouse" ] && "$APP/.venv/bin/python" -c 'import sys,platform; sys.exit(0 if sys.version_info[:2]==(3,14) and platform.machine()=="x86_64" else 1)'; then
  "$APP/.venv/bin/pip" install --no-index --find-links "$APP/wheelhouse" -r "$APP/requirements.txt"
else
  "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
fi
if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  APP_DIR="$APP" "$APP/.venv/bin/python" - <<'PY'
import os, secrets
from pathlib import Path
env = Path(os.environ['APP_DIR']) / '.env'
env.write_text(env.read_text().replace('APP_PASSWORD=\n','APP_PASSWORD='+secrets.token_urlsafe(24)+'\n'),encoding='utf-8')
PY
fi
chown root:studyagent "$APP/.env"
chmod 640 "$APP/.env"
cat > /etc/systemd/system/study-agent.service <<'UNIT'
[Unit]
Description=Personal Study Agent
After=network-online.target
Wants=network-online.target
[Service]
User=studyagent
Group=studyagent
WorkingDirectory=/opt/study-agent
EnvironmentFile=/opt/study-agent/.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/study-agent/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/study-agent/data
UMask=0027
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now study-agent
sleep 2
systemctl --no-pager --full status study-agent
echo
echo 'Installed. Service listens on 127.0.0.1:8000 only.'
echo 'Edit /opt/study-agent/.env: set DEEPSEEK_API_KEY and keep the generated APP_PASSWORD.'
echo 'Then: systemctl restart study-agent'
echo 'Use an SSH tunnel or HTTPS reverse proxy to access the web interface.'
echo 'View your generated web password locally: grep ^APP_PASSWORD= /opt/study-agent/.env'
