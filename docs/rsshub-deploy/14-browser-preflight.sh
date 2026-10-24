set -eu
docker compose -f /opt/rsshub/compose.yaml ps
docker exec rsshub-rsshub-1 sh -c 'node --version; cat /etc/os-release | head -n 3'
free -m
df -h /
systemctl is-active astrbot
