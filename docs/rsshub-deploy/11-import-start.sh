set -eu
test -s /opt/rsshub/rsshub-light.tar.gz
echo '77e8404adb6ace7539d3bbadf55ad7248c65ab14880d5a2070cef6a6cd84891a  /opt/rsshub/rsshub-light.tar.gz' | sha256sum -c -
docker load -i /opt/rsshub/rsshub-light.tar.gz
IMAGE=$(docker image inspect ghcr.io/diygod/rsshub:latest --format '{{.Id}}')
case "$IMAGE" in sha256:*) ;; *) exit 1 ;; esac
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.before-pin.yaml
sed -i "s|image: ghcr.io/diygod/rsshub:latest|image: $IMAGE|" /opt/rsshub/compose.yaml
printf 'PINNED_IMAGE_ID=%s\n' "$IMAGE"
docker compose -f /opt/rsshub/compose.yaml config --quiet
docker compose -f /opt/rsshub/compose.yaml up -d --pull never
docker compose -f /opt/rsshub/compose.yaml ps
