set -eu
test "$(cat /opt/rsshub/pull.exit)" = 0
IMAGE=$(docker image inspect ghcr.io/diygod/rsshub:chromium-bundled --format '{{index .RepoDigests 0}}')
case "$IMAGE" in ghcr.io/diygod/rsshub@sha256:*) ;; *) echo 'Unexpected image digest'; exit 1 ;; esac
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.before-pin.yaml
sed -i "s|image: ghcr.io/diygod/rsshub:chromium-bundled|image: $IMAGE|" /opt/rsshub/compose.yaml
printf 'PINNED_IMAGE=%s\n' "$IMAGE"
docker compose -f /opt/rsshub/compose.yaml config --quiet
docker compose -f /opt/rsshub/compose.yaml up -d
docker compose -f /opt/rsshub/compose.yaml ps
