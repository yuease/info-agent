set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m
