set -eu
test -s /opt/rsshub/rsshub-chromium-bundled.tar.gz
test -s /opt/rsshub/browser-archive.sha256
cd /opt/rsshub
sha256sum -c browser-archive.sha256
docker load -i rsshub-chromium-bundled.tar.gz
IMAGE=$(docker image inspect ghcr.io/diygod/rsshub:chromium-bundled --format '{{.Id}}')
case "$IMAGE" in sha256:*) ;; *) exit 1 ;; esac
test ! -e compose.lightweight.yaml
cp -p compose.yaml compose.lightweight.yaml
sed -i "s|^    image: .*|    image: $IMAGE|" compose.yaml
docker compose -f /opt/rsshub/compose.yaml config --quiet
docker compose -f /opt/rsshub/compose.yaml up -d --pull never
docker compose -f /opt/rsshub/compose.yaml ps
