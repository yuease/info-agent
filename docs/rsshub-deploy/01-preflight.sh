set -eu
date -Is
free -m
df -h /
docker version --format '{{.Server.Version}}'
docker compose version
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
systemctl show astrbot -p ActiveState -p MemoryCurrent
if [ -e /opt/rsshub ]; then ls -ld /opt/rsshub; else echo 'RSSHUB_DIRECTORY_ABSENT'; fi
ss -lnt '( sport = :1200 )'
for endpoint in https://registry-1.docker.io/v2/ https://ghcr.io/v2/; do
  curl -sS -o /dev/null --connect-timeout 5 --max-time 8 -w '%{http_code}\n' "$endpoint" || true
done
