set -eu
if [ -f /opt/rsshub/bilibili-probe.exit ]; then
  printf 'CURL_EXIT='; cat /opt/rsshub/bilibili-probe.exit
  printf 'HTTP='; cat /opt/rsshub/bilibili-probe.http; printf '\n'
  python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('/opt/rsshub/bilibili-probe.xml')
if p.exists():
    try:
        r=ET.parse(p).getroot()
        print('ROOT:',r.tag,'ITEMS:',len(r.findall('.//item')))
        for item in r.findall('.//item')[:3]:
            print(item.findtext('title'),item.findtext('link'))
    except ET.ParseError:
        print('Response is not valid RSS XML; bytes:',p.stat().st_size)
PY
else echo 'PROBE_RUNNING'; fi
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
systemctl is-active astrbot
free -m
