set -eu
for file in /opt/rsshub/bilibili-probe.exit /opt/rsshub/bilibili-probe.http /opt/rsshub/bilibili-probe.err /opt/rsshub/bilibili-probe.xml; do
  if [ -f "$file" ]; then mv "$file" "$file.previous"; fi
done
nohup sh -c 'curl -sS --max-time 90 -o /opt/rsshub/bilibili-probe.xml -w "%{http_code}" http://127.0.0.1:1200/bilibili/vsearch/LangGraph > /opt/rsshub/bilibili-probe.http 2>/opt/rsshub/bilibili-probe.err; echo $? > /opt/rsshub/bilibili-probe.exit' </dev/null >/opt/rsshub/probe-launch.log 2>&1 &
echo "PROBE_PID=$!"
