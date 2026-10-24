set -eu
test ! -e /opt/rsshub/browser-install.exit
nohup sh -c 'sh /opt/rsshub/install-browser-image.sh > /opt/rsshub/browser-install.log 2>&1; echo $? > /opt/rsshub/browser-install.exit' </dev/null >/opt/rsshub/browser-install-launch.log 2>&1 &
echo "INSTALL_PID=$!"
