set -eu
if [ -f /opt/rsshub/browser-install.exit ]; then printf 'INSTALL_EXIT='; cat /opt/rsshub/browser-install.exit; else echo 'INSTALL_RUNNING'; fi
tail -n 15 /opt/rsshub/browser-install.log
systemctl is-active astrbot
free -m
