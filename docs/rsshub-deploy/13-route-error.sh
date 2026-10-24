set -eu
python3 - <<'PY'
from pathlib import Path
import re,html
s=Path('/opt/rsshub/bilibili-probe.xml').read_text()
for tag in ('h1','h2','pre'):
    for text in re.findall(r'<'+tag+r'\b[^>]*>(.*?)</'+tag+'>',s,re.S):
        print(html.unescape(re.sub('<[^>]+>',' ',text))[:2500])
PY
docker logs --tail 18 rsshub-rsshub-1 2>&1
