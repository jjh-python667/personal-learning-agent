#!/usr/bin/env bash
set -euo pipefail
APP=/opt/study-agent
if [ "$(id -u)" -ne 0 ]; then echo 'Run this script as root.'; exit 1; fi
PATCH_DIR="$(cd "$(dirname "$0")" && pwd -P)"
if [ ! -f "$APP/.env" ] || [ ! -x "$APP/.venv/bin/python" ]; then echo 'Install the main Study Agent first.'; exit 1; fi
if [ ! -f "$PATCH_DIR/qq_bridge.py" ]; then echo 'Keep setup-qq.sh beside the patched qq_bridge.py and wheelhouse directory.'; exit 1; fi
if [ -d "$PATCH_DIR/wheelhouse" ] && "$APP/.venv/bin/python" -c 'import sys,platform; sys.exit(0 if sys.version_info[:2]==(3,14) and platform.machine()=="x86_64" else 1)'; then
  "$APP/.venv/bin/pip" install --no-index --find-links "$PATCH_DIR/wheelhouse" 'qq-botpy==1.2.1'
else
  "$APP/.venv/bin/pip" install 'qq-botpy==1.2.1'
fi
if [ "$PATCH_DIR" != "$APP" ]; then cp "$PATCH_DIR/qq_bridge.py" "$APP/qq_bridge.py"; fi
chmod 644 "$APP/qq_bridge.py"
# stdin remains the terminal, so secrets are entered without displaying them.
"$APP/.venv/bin/python" -c '
from pathlib import Path
from getpass import getpass
import re
p=Path("/opt/study-agent/.env")
app_id=input("QQ AppID: ").strip()
secret=getpass("QQ AppSecret (hidden): ").strip()
owner=input("QQ owner openid (leave empty for initial pairing): ").strip()
if not app_id.isdigit() or not secret or any(c.isspace() for c in secret+owner):
    raise SystemExit("Invalid AppID, AppSecret or owner openid; configuration not changed.")
text=p.read_text()
for name,value in {"QQ_APP_ID":app_id,"QQ_APP_SECRET":secret,"QQ_OWNER_OPENID":owner}.items():
    pattern=r"(?m)^"+name+r"=.*$"
    text=re.sub(pattern,lambda m:name+"="+value,text) if re.search(pattern,text) else text.rstrip()+"\n"+name+"="+value+"\n"
p.write_text(text)
print("QQ configuration saved. Secrets are not displayed.")
'
chown root:studyagent "$APP/.env"
chmod 640 "$APP/.env"
cat > /etc/systemd/system/study-agent-qq.service <<'UNIT'
[Unit]
Description=Study Agent QQ official bridge
After=network-online.target study-agent.service
Wants=network-online.target study-agent.service
[Service]
User=studyagent
Group=studyagent
WorkingDirectory=/opt/study-agent
EnvironmentFile=/opt/study-agent/.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/study-agent/.venv/bin/python qq_bridge.py
Restart=on-failure
RestartSec=15
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
systemctl enable study-agent-qq
systemctl restart study-agent-qq
echo 'Bridge installed. Check: journalctl -u study-agent-qq -n 50 --no-pager'
echo 'Without QQ_OWNER_OPENID, no messages execute tasks. Send yourself a test message and inspect the sender openid in logs.'
