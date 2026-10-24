set -eu
docker compose -f /opt/rsshub/compose.yaml ps
curl -sS --max-time 15 -w '\nHTTP=%{http_code}\n' http://127.0.0.1:1200/healthz
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
docker inspect rsshub-rsshub-1 --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} NanoCPUs={{.HostConfig.NanoCpus}}'
systemctl is-active astrbot
free -m
df -h /
