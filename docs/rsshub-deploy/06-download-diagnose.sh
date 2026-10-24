set -eu
if [ -f /opt/rsshub/pull.exit ]; then cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
ps -eo pid,etime,comm | grep -E 'docker|compose' || true
ls -lh /opt/rsshub/pull.log
tail -n 25 /opt/rsshub/pull.log
docker image ls ghcr.io/diygod/rsshub
