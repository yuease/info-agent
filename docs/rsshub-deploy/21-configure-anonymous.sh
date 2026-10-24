set -eu
install -d -m 700 /opt/rsshub/private
test ! -e /opt/rsshub/private/bilibili.env
docker cp rsshub-rsshub-1:/tmp/rsshub-bili-anonymous.env /opt/rsshub/private/bilibili.env
chmod 600 /opt/rsshub/private/bilibili.env
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.before-anonymous.yaml
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/rsshub/compose.yaml')
s=p.read_text()
assert 'env_file:' not in s
s=s.replace('    environment:\n','    env_file:\n      - path: /opt/rsshub/private/bilibili.env\n        format: raw\n    environment:\n',1)
p.write_text(s)
PY
docker compose -f /opt/rsshub/compose.yaml config --quiet
docker compose -f /opt/rsshub/compose.yaml up -d --pull never
systemctl is-active astrbot
