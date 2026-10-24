set -eu
python3 - <<'PY'
from pathlib import Path
import os,signal
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        args=(p/'cmdline').read_bytes().decode().split('\0')
        if Path(args[0]).name in ('docker','docker-compose') and '/opt/rsshub/compose.yaml' in args and 'pull' in args:
            print('Stopping RSSHub pull client:',p.name)
            os.kill(int(p.name),signal.SIGTERM)
    except (FileNotFoundError,ProcessLookupError,PermissionError): pass
PY
systemctl is-active astrbot
