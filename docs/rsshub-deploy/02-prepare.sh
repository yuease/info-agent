set -eu
test ! -e /opt/rsshub
install -d -m 750 /opt/rsshub
cat > /opt/rsshub/compose.yaml <<'YAML'
name: rsshub
services:
  rsshub:
    image: ghcr.io/diygod/rsshub:chromium-bundled
    restart: unless-stopped
    init: true
    ports:
      - '127.0.0.1:1200:1200'
    mem_limit: 512m
    memswap_limit: 768m
    cpus: 0.75
    pids_limit: 256
    environment:
      NODE_ENV: production
      NODE_OPTIONS: '--max-old-space-size=256'
      CACHE_TYPE: memory
      CACHE_EXPIRE: '1800'
      CACHE_CONTENT_EXPIRE: '3600'
      MEMORY_MAX: '32'
      REQUEST_RETRY: '1'
      REQUEST_TIMEOUT: '15000'
      NO_LOGFILES: 'true'
      DEBUG_INFO: 'false'
    healthcheck:
      test: ['CMD', 'curl', '-fsS', 'http://127.0.0.1:1200/healthz']
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 90s
    logging:
      driver: json-file
      options:
        max-size: '5m'
        max-file: '2'
YAML
chmod 640 /opt/rsshub/compose.yaml
docker compose -f /opt/rsshub/compose.yaml config --quiet
nohup sh -c 'docker compose -f /opt/rsshub/compose.yaml pull > /opt/rsshub/pull.log 2>&1; echo $? > /opt/rsshub/pull.exit' </dev/null >/opt/rsshub/pull-launch.log 2>&1 &
echo "PULL_PID=$!"
