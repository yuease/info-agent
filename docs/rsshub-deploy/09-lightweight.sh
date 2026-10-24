set -eu
test ! -f /opt/rsshub/pull.exit || true
for pid in $(pgrep -f '^/usr/libexec/docker/cli-plugins/docker-compose -f /opt/rsshub/compose.yaml pull$' || true); do kill -TERM "$pid"; done
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.browser-plan.yaml
sed -i 's|image: ghcr.io/diygod/rsshub:chromium-bundled|image: ghcr.io/diygod/rsshub:latest|' /opt/rsshub/compose.yaml
docker compose -f /opt/rsshub/compose.yaml config --quiet
nohup sh -c 'docker compose -f /opt/rsshub/compose.yaml pull > /opt/rsshub/pull-light.log 2>&1; echo $? > /opt/rsshub/pull-light.exit' </dev/null >/opt/rsshub/pull-light-launch.log 2>&1 &
echo "LIGHT_PULL_PID=$!"
ps -eo pid,args | grep '[d]ocker-compose -f /opt/rsshub/compose.yaml pull' || true
