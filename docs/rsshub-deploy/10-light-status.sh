set -eu
if [ -f /opt/rsshub/pull-light.exit ]; then printf 'EXIT='; cat /opt/rsshub/pull-light.exit; else echo 'RUNNING'; fi
tail -n 5 /opt/rsshub/pull-light.log
