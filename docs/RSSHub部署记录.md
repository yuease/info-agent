# RSSHub 部署记录

日期：2026-10-23。服务器：现有阿里云 Ubuntu ECS，2 核、约 1.7 GiB 可见内存。

## 目标与边界

- 独立目录 `/opt/rsshub`、独立 Compose 项目，不修改 `/opt/qqbot`。
- 首先只绑定 `127.0.0.1:1200`，不给公网直接暴露服务。
- 限制内存、CPU 和日志体积；不改变现有机器人、登录状态和数据卷。
- 先完成服务部署，再区分健康检查成功与平台采集成功。
- 不记录密钥、Cookie、密码和聊天内容。

## 连接方式

通过已配置的 Workbench CLI 执行以下包装命令，`$remote` 为每节记录的 Shell 命令：

```powershell
& 'D:\Tools\workbench\workbench.exe' exec -i i-2zebyfc3w77fj4edjmgb -r cn-beijing -P astrbot -o json --timeout 30 -c $remote
```

官方部署参考：https://github.com/DIYgod/RSSHub/blob/master/docker-compose.yml

以下内容随操作追加。命令输出是执行时的快照。

## 1. 部署前只读检查

执行时间：2026-10-23T16:33:06.4811620+08:00

```bash
set -eu
date -Is
free -m
df -h /
docker version --format '{{.Server.Version}}'
docker compose version
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
systemctl show astrbot -p ActiveState -p MemoryCurrent
if [ -e /opt/rsshub ]; then ls -ld /opt/rsshub; else echo 'RSSHUB_DIRECTORY_ABSENT'; fi
ss -lnt '( sport = :1200 )'
for endpoint in https://registry-1.docker.io/v2/ https://ghcr.io/v2/; do
  curl -sS -o /dev/null --connect-timeout 5 --max-time 8 -w '%{http_code}\n' "$endpoint" || true
done

```

```text
远端退出码：0
2026-10-23T16:33:21+08:00
               total        used        free      shared  buff/cache   available
Mem:            1740        1006          89           3         817         734
Swap:           4095        1074        3021
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda3        40G   19G   19G  51% /
29.1.3
Docker Compose version 2.40.3+ds1-0ubuntu1~24.04.1
NAMES      STATUS        PORTS
snowluma   Up 17 hours   3000-3001/tcp, 127.0.0.1:5099->5099/tcp, 5900/tcp, 127.0.0.1:6081->6081/tcp
MemoryCurrent=259178496
ActiveState=active
RSSHUB_DIRECTORY_ABSENT
State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess
000
401

curl: (28) Failed to connect to registry-1.docker.io port 443 after 5000 ms: Timeout was reached

```


## 2. 创建独立配置并后台拉取镜像

执行时间：2026-10-23T16:33:38.2907676+08:00

```bash
set -eu
test ! -e /opt/rsshub
install -d -m 750 /opt/rsshub
cat > /opt/rsshub/compose.yaml <<'YAML'
name: rsshub
services:
  rsshub:
    image: ghcr.io/diygod/rsshub:chromium-bundled
    restart: unless-stopped
    init: true
    ports:
      - '127.0.0.1:1200:1200'
    mem_limit: 512m
    memswap_limit: 768m
    cpus: 0.75
    pids_limit: 256
    environment:
      NODE_ENV: production
      NODE_OPTIONS: '--max-old-space-size=256'
      CACHE_TYPE: memory
      CACHE_EXPIRE: '1800'
      CACHE_CONTENT_EXPIRE: '3600'
      MEMORY_MAX: '32'
      REQUEST_RETRY: '1'
      REQUEST_TIMEOUT: '15000'
      NO_LOGFILES: 'true'
      DEBUG_INFO: 'false'
    healthcheck:
      test: ['CMD', 'curl', '-fsS', 'http://127.0.0.1:1200/healthz']
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 90s
    logging:
      driver: json-file
      options:
        max-size: '5m'
        max-file: '2'
YAML
chmod 640 /opt/rsshub/compose.yaml
docker compose -f /opt/rsshub/compose.yaml config --quiet
nohup sh -c 'docker compose -f /opt/rsshub/compose.yaml pull > /opt/rsshub/pull.log 2>&1; echo $? > /opt/rsshub/pull.exit' </dev/null >/opt/rsshub/pull-launch.log 2>&1 &
echo "PULL_PID=$!"

```

```text
远端退出码：0
PULL_PID=103182


```


## 3. 检查镜像下载进度

执行时间：2026-10-23T16:33:46.6819652+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 3870604c140d Downloading [==>                                                ]  2.097MB/46.87MB
 fa05dfa0597d Downloading [==>                                                ]  6.291MB/108MB
 539966a9067a Downloading [====================================>              ]  18.87MB/25.89MB
 3870604c140d Downloading [==>                                                ]  2.097MB/46.87MB
 fa05dfa0597d Downloading [==>                                                ]  6.291MB/108MB
 fa05dfa0597d Downloading [===>                                               ]   7.34MB/108MB
 539966a9067a Downloading [====================================>              ]  18.87MB/25.89MB
 3870604c140d Downloading [==>                                                ]  2.097MB/46.87MB
               total        used        free      shared  buff/cache   available
Mem:            1740         965         117           3         830         775
Swap:           4095        1171        2924


```


## 4. 再次检查下载状态

执行时间：2026-10-23T16:34:12.7956686+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 3870604c140d Extracting 2 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Pull complete 
               total        used        free      shared  buff/cache   available
Mem:            1740         979          91           3         844         761
Swap:           4095        1185        2910


```


## 5. 检查镜像拉取结果

执行时间：2026-10-23T16:34:41.5579188+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 3870604c140d Extracting 2 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Extracting 3 s
 3870604c140d Pull complete 
               total        used        free      shared  buff/cache   available
Mem:            1740         977          90           3         848         763
Swap:           4095        1184        2911


```


## 6. 查看下载进程与剩余镜像层

执行时间：2026-10-23T16:34:54.0664768+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
ps -eo pid,etime,comm | grep -E 'docker|compose' || true
ls -lh /opt/rsshub/pull.log
tail -n 25 /opt/rsshub/pull.log
docker image ls ghcr.io/diygod/rsshub

```

```text
远端退出码：0
PULL_RUNNING
  14555    17:42:15 dockerd
  21597    16:53:12 docker-proxy
  21613    16:53:12 docker-proxy
 103183       01:15 docker
 103196       01:15 docker-compose
-rw-r--r-- 1 root root 54K Oct 23 16:35 /opt/rsshub/pull.log
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
 c96e935e8d3e Downloading [==================================>                ]  1.049MB/1.537MB
IMAGE   ID             DISK USAGE   CONTENT SIZE   EXTRA

WARNING: This output is designed for human readability. For machine-readable output, please use --format.

```


## 7. 检查下载是否完成

执行时间：2026-10-23T16:35:16.2661390+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
               total        used        free      shared  buff/cache   available
Mem:            1740        1008          90           3         817         732
Swap:           4095        1184        2911


```


## 8. 下载进度与内存检查

执行时间：2026-10-23T16:36:13.9476483+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [==>                                                ]  1.049MB/26.18MB
               total        used        free      shared  buff/cache   available
Mem:            1740         960         137           3         817         780
Swap:           4095        1183        2912


```


## 9. 检查剩余镜像层

执行时间：2026-10-23T16:36:35.2553508+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  1.049MB/298.8MB
               total        used        free      shared  buff/cache   available
Mem:            1740         940         154           3         820         800
Swap:           4095        1183        2912


```


## 10. 恢复部署：确认下载状态

执行时间：2026-10-23T16:37:43.8575939+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull.exit ]; then printf 'PULL_EXIT='; cat /opt/rsshub/pull.exit; else echo 'PULL_RUNNING'; fi
tail -n 8 /opt/rsshub/pull.log
free -m

```

```text
远端退出码：0
PULL_RUNNING
 bbd2a5058ca5 Downloading [>                                                  ]  2.097MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  2.097MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  2.097MB/298.8MB
 672696039afc Downloading [====>                                              ]  2.097MB/26.18MB
 bbd2a5058ca5 Downloading [>                                                  ]  2.097MB/298.8MB
               total        used        free      shared  buff/cache   available
Mem:            1740         918         168           3         828         822
Swap:           4095        1183        2912


```


## 11. 改用轻量镜像，保留原配置

执行时间：2026-10-23T16:38:17.7936776+08:00

```bash
set -eu
test ! -f /opt/rsshub/pull.exit || true
for pid in $(pgrep -f '^/usr/libexec/docker/cli-plugins/docker-compose -f /opt/rsshub/compose.yaml pull$' || true); do kill -TERM "$pid"; done
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.browser-plan.yaml
sed -i 's|image: ghcr.io/diygod/rsshub:chromium-bundled|image: ghcr.io/diygod/rsshub:latest|' /opt/rsshub/compose.yaml
docker compose -f /opt/rsshub/compose.yaml config --quiet
nohup sh -c 'docker compose -f /opt/rsshub/compose.yaml pull > /opt/rsshub/pull-light.log 2>&1; echo $? > /opt/rsshub/pull-light.exit' </dev/null >/opt/rsshub/pull-light-launch.log 2>&1 &
echo "LIGHT_PULL_PID=$!"
ps -eo pid,args | grep '[d]ocker-compose -f /opt/rsshub/compose.yaml pull' || true

```

```text
远端退出码：0
LIGHT_PULL_PID=103465


```


## 12. 轻量镜像下载结果

执行时间：2026-10-23T16:38:25.3342013+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull-light.exit ]; then printf 'EXIT='; cat /opt/rsshub/pull-light.exit; else echo 'RUNNING'; fi
tail -n 5 /opt/rsshub/pull-light.log

```

```text
远端退出码：0
RUNNING
 672696039afc Downloading [======>                                            ]  3.146MB/26.18MB
 672696039afc Downloading [======>                                            ]  3.146MB/26.18MB
 672696039afc Downloading [======>                                            ]  3.146MB/26.18MB
 672696039afc Downloading [======>                                            ]  3.146MB/26.18MB
 672696039afc Downloading [======>                                            ]  3.146MB/26.18MB


```


## 13. 本机中转下载备选
服务器镜像层下载过慢，尝试在本机从同一官方 GHCR 仓库下载 linux/amd64 镜像，逐个核验 SHA-256，制作 docker load 归档。脚本不输出短期 Registry token。
命令：dependencies/python/python.exe docs/rsshub-deploy/download-image.py


## 14. 使用用户指定的本机代理
首次本机直连下载遇到 IncompleteRead。按用户建议，仅下载脚本使用 HTTP/HTTPS 代理 http://127.0.0.1:7890，不修改全局代理或服务器配置。重新运行 download-image.py；官方仓库与摘要验证规则保持不变。

代理端口可达，但大文件仍被提前断开。下载脚本改为每次 512 KiB 的 HTTP Range 分段请求，最多重试 5 次，最终核验完整镜像层 SHA-256。


## 15. 检查服务器侧下载

执行时间：2026-10-23T16:40:14.2794043+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull-light.exit ]; then printf 'EXIT='; cat /opt/rsshub/pull-light.exit; else echo 'RUNNING'; fi
tail -n 5 /opt/rsshub/pull-light.log

```

```text
远端退出码：0
RUNNING
 1d6fc71bfd2d Downloading [=>                                                 ]  1.049MB/29.84MB
 672696039afc Downloading [========>                                          ]  4.194MB/26.18MB
 1d6fc71bfd2d Downloading [=>                                                 ]  1.049MB/29.84MB
 672696039afc Downloading [========>                                          ]  4.194MB/26.18MB
 1d6fc71bfd2d Downloading [=>                                                 ]  1.049MB/29.84MB


```


## 16. 服务器下载状态复查

执行时间：2026-10-23T16:42:59.7639556+08:00

```bash
set -eu
if [ -f /opt/rsshub/pull-light.exit ]; then printf 'EXIT='; cat /opt/rsshub/pull-light.exit; else echo 'RUNNING'; fi
tail -n 5 /opt/rsshub/pull-light.log

```

```text
远端退出码：0
RUNNING
 1d6fc71bfd2d Downloading [=====>                                             ]  3.146MB/29.84MB
 672696039afc Downloading [============>                                      ]  6.291MB/26.18MB
 1d6fc71bfd2d Downloading [=====>                                             ]  3.146MB/29.84MB
 672696039afc Downloading [============>                                      ]  6.291MB/26.18MB
 1d6fc71bfd2d Downloading [=====>                                             ]  3.146MB/29.84MB


```


后续优化：仅停止本次本机 download-image.py 进程，保留已校验缓存。分块下载使用 12 个并发连接并保存 .part 文件，完整层拼接后核验 SHA-256。

部分分块连接仍可能提前中断；已完成分块保存在本机，重运行仅补缺失分块。


## 17. 停止本次服务器直连下载客户端

执行时间：2026-10-23T16:46:33.1490294+08:00

```bash
set -eu
python3 - <<'PY'
from pathlib import Path
import os,signal
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        args=(p/'cmdline').read_bytes().decode().split('\0')
        if Path(args[0]).name in ('docker','docker-compose') and '/opt/rsshub/compose.yaml' in args and 'pull' in args:
            print('Stopping RSSHub pull client:',p.name)
            os.kill(int(p.name),signal.SIGTERM)
    except (FileNotFoundError,ProcessLookupError,PermissionError): pass
PY
systemctl is-active astrbot

```

```text
远端退出码：0
Stopping RSSHub pull client: 103183
Stopping RSSHub pull client: 103196
Stopping RSSHub pull client: 103468
Stopping RSSHub pull client: 103483
active


```


镜像全部层下载并通过 SHA-256 校验。打包时发现包含 Zstandard 压缩层，已补充解压支持。本机依赖安装命令：python -m pip install zstandard --target docs/rsshub-deploy/local-deps --proxy http://127.0.0.1:7890；不修改服务器 Python 或机器人环境。


## 18. 上传归档
归档大小：144792635 字节。SHA-256：77E8404ADB6ACE7539D3BBADF55AD7248C65AB14880D5A2070CEF6A6CD84891A。
实际命令：
workbench.exe upload 本机归档 /opt/rsshub/rsshub-light.tar.gz --force -i i-2zebyfc3w77fj4edjmgb -r cn-beijing -P astrbot -o json


## 19. 校验归档、导入镜像并启动容器

执行时间：2026-10-23T16:49:19.2518896+08:00

```bash
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

```

```text
远端退出码：0
/opt/rsshub/rsshub-light.tar.gz: OK
Loaded image: ghcr.io/diygod/rsshub:latest
PINNED_IMAGE_ID=sha256:2cac25eca1ed6a953c0e174500f864512543ea8c92bf578a0860331dc838a81b
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED         STATUS                                     PORTS
rsshub-rsshub-1   sha256:2cac25eca1ed6a953c0e174500f864512543ea8c92bf578a0860331dc838a81b   "dumb-init -- npm ru…"   rsshub    2 seconds ago   Up Less than a second (health: starting)   127.0.0.1:1200->1200/tcp

 Network rsshub_default  Creating
 Network rsshub_default  Created
 Container rsshub-rsshub-1  Creating
 Container rsshub-rsshub-1  Created
 Container rsshub-rsshub-1  Starting
 Container rsshub-rsshub-1  Started

```


## 20. 健康检查与机器人共存检查

执行时间：2026-10-23T16:49:43.0833871+08:00

```bash
set -eu
docker compose -f /opt/rsshub/compose.yaml ps
curl -sS --max-time 15 -w '\nHTTP=%{http_code}\n' http://127.0.0.1:1200/healthz
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
docker inspect rsshub-rsshub-1 --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} NanoCPUs={{.HostConfig.NanoCpus}}'
systemctl is-active astrbot
free -m
df -h /

```

```text
远端退出码：56
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED         STATUS                            PORTS
rsshub-rsshub-1   sha256:2cac25eca1ed6a953c0e174500f864512543ea8c92bf578a0860331dc838a81b   "dumb-init -- npm ru…"   rsshub    6 seconds ago   Up 5 seconds (health: starting)   127.0.0.1:1200->1200/tcp

HTTP=000

curl: (56) Recv failure: Connection reset by peer

```


## 21. 启动预热后复查

执行时间：2026-10-23T16:50:05.8624079+08:00

```bash
set -eu
docker compose -f /opt/rsshub/compose.yaml ps
curl -sS --max-time 15 -w '\nHTTP=%{http_code}\n' http://127.0.0.1:1200/healthz
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
docker inspect rsshub-rsshub-1 --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} NanoCPUs={{.HostConfig.NanoCpus}}'
systemctl is-active astrbot
free -m
df -h /

```

```text
远端退出码：0
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED          STATUS                    PORTS
rsshub-rsshub-1   sha256:2cac25eca1ed6a953c0e174500f864512543ea8c92bf578a0860331dc838a81b   "dumb-init -- npm ru…"   rsshub    29 seconds ago   Up 27 seconds (healthy)   127.0.0.1:1200->1200/tcp
ok
HTTP=200
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   0.00%     262.9MiB / 512MiB
snowluma          3.01%     243MiB / 1.7GiB
OOMKilled=false Restarts=0 Memory=536870912 MemorySwap=805306368 NanoCPUs=750000000
active
               total        used        free      shared  buff/cache   available
Mem:            1740        1072          90           3         760         668
Swap:           4095        1207        2888
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda3        40G   18G   20G  49% /


```


## 22. 测试 B 站关键词搜索路由

执行时间：2026-10-23T16:50:17.1884063+08:00

```bash
set -eu
nohup sh -c 'curl -sS --max-time 90 -o /opt/rsshub/bilibili-probe.xml -w "%{http_code}" http://127.0.0.1:1200/bilibili/vsearch/LangGraph > /opt/rsshub/bilibili-probe.http 2>/opt/rsshub/bilibili-probe.err; echo $? > /opt/rsshub/bilibili-probe.exit' </dev/null >/opt/rsshub/probe-launch.log 2>&1 &
echo "PROBE_PID=$!"

```

```text
远端退出码：0
PROBE_PID=104676


```


## 23. 启动本机访问转发
实际命令：powershell -File docs/rsshub-deploy/Start-RSSHubTunnel.ps1。该脚本仅新增本机 1200 到服务器 1200 的通道，不重启已有转发。


## 24. 路由测试及运行内存

执行时间：2026-10-23T16:50:26.7087433+08:00

```bash
set -eu
if [ -f /opt/rsshub/bilibili-probe.exit ]; then
  printf 'CURL_EXIT='; cat /opt/rsshub/bilibili-probe.exit
  printf 'HTTP='; cat /opt/rsshub/bilibili-probe.http; printf '\n'
  python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('/opt/rsshub/bilibili-probe.xml')
if p.exists():
    try:
        r=ET.parse(p).getroot()
        print('ROOT:',r.tag,'ITEMS:',len(r.findall('.//item')))
        for item in r.findall('.//item')[:3]:
            print(item.findtext('title'),item.findtext('link'))
    except ET.ParseError:
        print('Response is not valid RSS XML; bytes:',p.stat().st_size)
PY
else echo 'PROBE_RUNNING'; fi
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
CURL_EXIT=0
HTTP=503
ROOT: html ITEMS: 0
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   0.69%     285.4MiB / 512MiB
snowluma          2.06%     269.9MiB / 1.7GiB
active
               total        used        free      shared  buff/cache   available
Mem:            1740        1079          98           3         745         661
Swap:           4095        1208        2887


```

本机转发验证：HTTP 200，正文 ok

## 25. 定位 B 站路由失败原因

执行时间：2026-10-23T16:50:42.7250144+08:00

```bash
set -eu
python3 - <<'PY'
from pathlib import Path
import re,html
s=Path('/opt/rsshub/bilibili-probe.xml').read_text()
for tag in ('h1','h2','pre'):
    for text in re.findall(r'<'+tag+r'\b[^>]*>(.*?)</'+tag+'>',s,re.S):
        print(html.unescape(re.sub('<[^>]+>',' ',text))[:2500])
PY
docker logs --tail 18 rsshub-rsshub-1 2>&1

```

```text
远端退出码：0
Looks like something went wrong
[32minfo[39m: 🔗 Network: 👉 http://[::]:1200
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 97ms
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 5ms
[32minfo[39m: <-- GET /bilibili/vsearch/LangGraph
[31merror[39m: Error in /bilibili/vsearch/LangGraph: Error: browserType.launch: Executable doesn't exist at /app/node_modules/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell
╔════════════════════════════════════════════════════════════╗
║ Looks like Playwright was just installed or updated.       ║
║ Please run the following command to download new browsers: ║
║                                                            ║
║     npx playwright install                                 ║
║                                                            ║
║ <3 Patchright Team                                         ║
╚════════════════════════════════════════════════════════════╝
[32minfo[39m: --> GET /bilibili/vsearch/LangGraph [31m503[0m 2s
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 5ms


```


## 26. 当前验收结论和操作指南

- 已部署：独立 Compose 项目 rsshub，目录 /opt/rsshub。
- 镜像：官方 ghcr.io/diygod/rsshub 轻量版，经本机代理分块下载，逐层校验后通过 docker load 导入；Compose 固定实际导入镜像 ID。
- 服务：健康检查 HTTP 200、healthy；当前没有 OOM 和重启。
- 资源：本次采样 RSSHub 约 263–285 MiB，上限 512 MiB；CPU 上限 0.75 核；内存与 Swap 合计上限 768 MiB。
- 监听：仅服务器 127.0.0.1:1200，不开放公网。
- 本机访问：http://127.0.0.1:1200/ ，健康检查：http://127.0.0.1:1200/healthz 。转发进程需保持运行。
- 原有 AstrBot 保持 active，SnowLuma 容器仍在运行；未修改机器人配置和数据卷，未发送 QQ 消息。
- B 站关键词搜索：/bilibili/vsearch/LangGraph 返回 HTTP 503。日志显示缺少 chromium_headless_shell-1248。该路由尚未可用，不能据此宣称采集已完成。
- X 未配置认证、第三方接口或代理，也未验证，不能承诺可用。
- RSSHub 没有配置定时订阅、历史归档或消息推送；部署服务本身不会自动生成简报。

### 连接断开后恢复本机转发

在 PowerShell 执行：

```powershell
& 'D:\notebooks\agent\docs\rsshub-deploy\Start-RSSHubTunnel.ps1'
```

若提示端口占用，先检查原通道是否仍正常，不要直接结束其他转发进程。

### 服务器维护命令

通过文首 Workbench 包装执行：

```bash
docker compose -f /opt/rsshub/compose.yaml ps
curl -fsS http://127.0.0.1:1200/healthz
docker logs --tail 50 rsshub-rsshub-1
docker stats --no-stream rsshub-rsshub-1
```

仅停止 RSSHub（不会停止机器人）：

```bash
docker compose -f /opt/rsshub/compose.yaml stop
```

重新启动已有 RSSHub：

```bash
docker compose -f /opt/rsshub/compose.yaml start
```

### 后续待完成

B 站搜索还需要独立配置兼容浏览器或有效 Cookie 方案，并重新测试。原带浏览器方案配置保留于 /opt/rsshub/compose.browser-plan.yaml；该文件仅作参考，当前实际生效的是 compose.yaml。不要覆盖机器人浏览器环境。

本机镜像缓存、分段文件和服务器上传归档暂时保留，便于断点续传与排障。它们属于部署中间文件，不应作为项目源码提交。

## 27. 补齐浏览器前检查

执行时间：2026-10-23T16:52:26.4030326+08:00

```bash
set -eu
docker compose -f /opt/rsshub/compose.yaml ps
docker exec rsshub-rsshub-1 sh -c 'node --version; cat /etc/os-release | head -n 3'
free -m
df -h /
systemctl is-active astrbot

```

```text
远端退出码：0
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED         STATUS                   PORTS
rsshub-rsshub-1   sha256:2cac25eca1ed6a953c0e174500f864512543ea8c92bf578a0860331dc838a81b   "dumb-init -- npm ru…"   rsshub    2 minutes ago   Up 2 minutes (healthy)   127.0.0.1:1200->1200/tcp
v24.22.1
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
               total        used        free      shared  buff/cache   available
Mem:            1740        1025         125           3         772         715
Swap:           4095        1208        2887
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda3        40G   18G   20G  49% /
active


```


## 28. 下载官方浏览器镜像
选用官方 chromium-bundled 镜像，连同匹配的系统库与浏览器一起补齐。通过本机 7890 代理下载，复用已校验层，保留原轻量版镜像和配置作回退。实际命令：dependencies/python/python.exe docs/rsshub-deploy/download-image.py --tag chromium-bundled


## 29. 上传浏览器镜像归档
大小：580618350 字节；SHA-256：0b1cb39a60fd282f3cbcb21daa00af1c4049111cffd45043590372e5d35c7151。
实际命令：workbench.exe upload docs/rsshub-deploy/image-cache/rsshub-chromium-bundled.tar.gz /opt/rsshub/rsshub-chromium-bundled.tar.gz --force -i i-2zebyfc3w77fj4edjmgb -r cn-beijing -P astrbot -o json

上传已完成。服务器将执行的安装脚本如下：
```bash
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

```

## 30. 后台导入浏览器镜像并切换 RSSHub

执行时间：2026-10-23T16:59:24.9301355+08:00

```bash
set -eu
test ! -e /opt/rsshub/browser-install.exit
nohup sh -c 'sh /opt/rsshub/install-browser-image.sh > /opt/rsshub/browser-install.log 2>&1; echo $? > /opt/rsshub/browser-install.exit' </dev/null >/opt/rsshub/browser-install-launch.log 2>&1 &
echo "INSTALL_PID=$!"

```

```text
远端退出码：0
INSTALL_PID=105359


```


## 31. 检查镜像导入与切换进度

执行时间：2026-10-23T16:59:35.7247217+08:00

```bash
set -eu
if [ -f /opt/rsshub/browser-install.exit ]; then printf 'INSTALL_EXIT='; cat /opt/rsshub/browser-install.exit; else echo 'INSTALL_RUNNING'; fi
tail -n 15 /opt/rsshub/browser-install.log
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
INSTALL_RUNNING
rsshub-chromium-bundled.tar.gz: OK
active
               total        used        free      shared  buff/cache   available
Mem:            1740         951          97           2         874         789
Swap:           4095        1234        2861


```


## 32. 镜像切换结果

执行时间：2026-10-23T17:00:06.3415404+08:00

```bash
set -eu
if [ -f /opt/rsshub/browser-install.exit ]; then printf 'INSTALL_EXIT='; cat /opt/rsshub/browser-install.exit; else echo 'INSTALL_RUNNING'; fi
tail -n 15 /opt/rsshub/browser-install.log
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
INSTALL_RUNNING
rsshub-chromium-bundled.tar.gz: OK
active
               total        used        free      shared  buff/cache   available
Mem:            1740        1040          91           2         791         700
Swap:           4095        1245        2850


```


## 33. 检查浏览器镜像启动

执行时间：2026-10-23T17:00:37.7669883+08:00

```bash
set -eu
if [ -f /opt/rsshub/browser-install.exit ]; then printf 'INSTALL_EXIT='; cat /opt/rsshub/browser-install.exit; else echo 'INSTALL_RUNNING'; fi
tail -n 15 /opt/rsshub/browser-install.log
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
INSTALL_EXIT=0
rsshub-chromium-bundled.tar.gz: OK
Loaded image: ghcr.io/diygod/rsshub:chromium-bundled
 Container rsshub-rsshub-1  Recreate
 Container rsshub-rsshub-1  Recreated
 Container rsshub-rsshub-1  Starting
 Container rsshub-rsshub-1  Started
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED         STATUS                                     PORTS
rsshub-rsshub-1   sha256:97f83a6eb45b246b8cd6af7438a542ccd7d91d8dc88b2caaf27cdb0595ed00c2   "dumb-init -- npm ru…"   rsshub    2 seconds ago   Up Less than a second (health: starting)   127.0.0.1:1200->1200/tcp
active
               total        used        free      shared  buff/cache   available
Mem:            1740        1017          96           1         807         723
Swap:           4095        1262        2833


```


## 34. Chromium 本地页面启动验证

执行时间：2026-10-23T17:00:43.3345392+08:00

```bash
set -eu
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "playwright";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const page=await browser.newPage();
 await page.setContent("<title>RSSHub browser smoke test</title><p>ok</p>");
 console.log("Browser version:",browser.version());
 console.log("Page title:",await page.title());
} finally {await browser.close();}
'

```

```text
远端退出码：1

node:internal/modules/package_json_reader:331
  throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);
        ^

Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'playwright' imported from /app/[eval1]
    at Object.getPackageJSONURL (node:internal/modules/package_json_reader:331:9)
    at packageResolve (node:internal/modules/esm/resolve:784:25)
    at moduleResolve (node:internal/modules/esm/resolve:873:18)
    at defaultResolve (node:internal/modules/esm/resolve:1006:11)
    at #cachedDefaultResolve (node:internal/modules/esm/loader:705:20)
    at #resolveAndMaybeBlockOnLoaderThread (node:internal/modules/esm/loader:725:38)
    at ModuleLoader.resolveSync (node:internal/modules/esm/loader:763:56)
    at #resolve (node:internal/modules/esm/loader:687:17)
    at ModuleLoader.getOrCreateModuleJob (node:internal/modules/esm/loader:607:35)
    at ModuleJob.syncLink (node:internal/modules/esm/module_job:276:33) {
  code: 'ERR_MODULE_NOT_FOUND'
}

Node.js v24.22.1

```


## 35. 使用镜像内实际 Patchright 包验证浏览器

执行时间：2026-10-23T17:00:50.7710957+08:00

```bash
set -eu
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const page=await browser.newPage();
 await page.setContent("<title>RSSHub browser smoke test</title><p>ok</p>");
 console.log("Browser version:",browser.version());
 console.log("Page title:",await page.title());
} finally {await browser.close();}
'

```

```text
远端退出码：0
Browser version: 156.0.8078.4
Page title: RSSHub browser smoke test


```


## 36. 补齐浏览器后重测 B 站搜索

执行时间：2026-10-23T17:01:04.8053030+08:00

```bash
set -eu
for file in /opt/rsshub/bilibili-probe.exit /opt/rsshub/bilibili-probe.http /opt/rsshub/bilibili-probe.err /opt/rsshub/bilibili-probe.xml; do
  if [ -f "$file" ]; then mv "$file" "$file.previous"; fi
done
nohup sh -c 'curl -sS --max-time 90 -o /opt/rsshub/bilibili-probe.xml -w "%{http_code}" http://127.0.0.1:1200/bilibili/vsearch/LangGraph > /opt/rsshub/bilibili-probe.http 2>/opt/rsshub/bilibili-probe.err; echo $? > /opt/rsshub/bilibili-probe.exit' </dev/null >/opt/rsshub/probe-launch.log 2>&1 &
echo "PROBE_PID=$!"

```

```text
远端退出码：0
PROBE_PID=105936


```


## 37. 浏览器版健康检查与资源限制

执行时间：2026-10-23T17:01:05.1464989+08:00

```bash
set -eu
docker compose -f /opt/rsshub/compose.yaml ps
curl -sS --max-time 15 -w '\nHTTP=%{http_code}\n' http://127.0.0.1:1200/healthz
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
docker inspect rsshub-rsshub-1 --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} NanoCPUs={{.HostConfig.NanoCpus}}'
systemctl is-active astrbot
free -m
df -h /

```

```text
远端退出码：0
NAME              IMAGE                                                                     COMMAND                  SERVICE   CREATED          STATUS                    PORTS
rsshub-rsshub-1   sha256:97f83a6eb45b246b8cd6af7438a542ccd7d91d8dc88b2caaf27cdb0595ed00c2   "dumb-init -- npm ru…"   rsshub    40 seconds ago   Up 38 seconds (healthy)   127.0.0.1:1200->1200/tcp
ok
HTTP=200
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   32.37%    405.9MiB / 512MiB
snowluma          1.14%     100.8MiB / 1.7GiB
OOMKilled=false Restarts=0 Memory=536870912 MemorySwap=805306368 NanoCPUs=750000000
active
               total        used        free      shared  buff/cache   available
Mem:            1740         970          86           1         862         770
Swap:           4095        1364        2731
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda3        40G   21G   18G  54% /


```


## 38. 搜索路由结果

执行时间：2026-10-23T17:01:15.8236973+08:00

```bash
set -eu
if [ -f /opt/rsshub/bilibili-probe.exit ]; then
  printf 'CURL_EXIT='; cat /opt/rsshub/bilibili-probe.exit
  printf 'HTTP='; cat /opt/rsshub/bilibili-probe.http; printf '\n'
  python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('/opt/rsshub/bilibili-probe.xml')
if p.exists():
    try:
        r=ET.parse(p).getroot()
        print('ROOT:',r.tag,'ITEMS:',len(r.findall('.//item')))
        for item in r.findall('.//item')[:3]:
            print(item.findtext('title'),item.findtext('link'))
    except ET.ParseError:
        print('Response is not valid RSS XML; bytes:',p.stat().st_size)
PY
else echo 'PROBE_RUNNING'; fi
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
PROBE_RUNNING
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   75.75%    380.2MiB / 512MiB
snowluma          1.22%     100.8MiB / 1.7GiB
active
               total        used        free      shared  buff/cache   available
Mem:            1740        1018          77           7         828         722
Swap:           4095        1417        2678


```


## 39. 等待搜索结果后复查

执行时间：2026-10-23T17:01:45.6761446+08:00

```bash
set -eu
if [ -f /opt/rsshub/bilibili-probe.exit ]; then
  printf 'CURL_EXIT='; cat /opt/rsshub/bilibili-probe.exit
  printf 'HTTP='; cat /opt/rsshub/bilibili-probe.http; printf '\n'
  python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('/opt/rsshub/bilibili-probe.xml')
if p.exists():
    try:
        r=ET.parse(p).getroot()
        print('ROOT:',r.tag,'ITEMS:',len(r.findall('.//item')))
        for item in r.findall('.//item')[:3]:
            print(item.findtext('title'),item.findtext('link'))
    except ET.ParseError:
        print('Response is not valid RSS XML; bytes:',p.stat().st_size)
PY
else echo 'PROBE_RUNNING'; fi
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
CURL_EXIT=0
HTTP=503
ROOT: html ITEMS: 0
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   0.00%     283.6MiB / 512MiB
snowluma          0.95%     111.2MiB / 1.7GiB
active
               total        used        free      shared  buff/cache   available
Mem:            1740         885         111           1         921         855
Swap:           4095        1389        2706


```


## 40. 确认剩余采集障碍

执行时间：2026-10-23T17:01:51.7707989+08:00

```bash
set -eu
python3 - <<'PY'
from pathlib import Path
import re,html
s=Path('/opt/rsshub/bilibili-probe.xml').read_text()
for tag in ('h1','h2','pre'):
    for text in re.findall(r'<'+tag+r'\b[^>]*>(.*?)</'+tag+'>',s,re.S):
        print(html.unescape(re.sub('<[^>]+>',' ',text))[:2500])
PY
docker logs --tail 18 rsshub-rsshub-1 2>&1

```

```text
远端退出码：0
Looks like something went wrong
npm warn Unknown project config "package-manager-strict". This will stop working in the next major version of npm. See `npm help npmrc` for supported config options.

> rsshub@1.0.0 start
> cross-env NODE_ENV=production NODE_OPTIONS='--max-http-header-size=32768' node dist/index.mjs

[32minfo[39m: 🎉 RSSHub is running on port 1200! Cheers!
[32minfo[39m: 🔗 Local: 👉 http://localhost:1200
[32minfo[39m: 🔗 Network: 👉 http://172.19.0.2:1200
[32minfo[39m: 🔗 Network: 👉 http://[::]:1200
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 55ms
[32minfo[39m: <-- GET /bilibili/vsearch/LangGraph
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 5ms
[31merror[39m: Error in /bilibili/vsearch/LangGraph: TimeoutError: page.waitForResponse: Timeout 12000ms exceeded while waiting for event "response"
[32minfo[39m: --> GET /bilibili/vsearch/LangGraph [31m503[0m 25s
[32minfo[39m: <-- GET /healthz
[32minfo[39m: --> GET /healthz [32m200[0m 9ms


```


## 41. 浏览器补齐验收结论

本节更新第 26 节的浏览器缺失状态：

- 已切换到官方 chromium-bundled 镜像，当前 Compose 固定导入镜像 ID。
- 原轻量版配置备份：/opt/rsshub/compose.lightweight.yaml。
- 浏览器自检通过：使用镜像中的 patchright，成功启动 Chromium 156.0.8078.4，创建页面并读取测试标题。
- 最初自检导入 playwright 报模块不存在，改为镜像实际使用的 patchright 后成功；未安装额外 Node 包。
- RSSHub 健康检查 HTTP 200，healthy；采集时内存采样约 380–406 MiB，上限仍为 512 MiB；没有 OOM 和重启。
- RSSHub 的 npm 启动命令会覆盖 NODE_OPTIONS，因此 compose 内的旧 Node 堆参数不能作为已生效保证；Docker 内存限制已实际验证。
- B 站 /bilibili/vsearch/LangGraph 仍返回 HTTP 503；当前错误为 page.waitForResponse 等待平台响应超时（12000ms），不再是浏览器可执行文件缺失。
- 尚未配置 B 站 Cookie，未宣称 B 站采集已经可用。下一步应针对页面响应、服务器访问和登录要求做诊断。
- AstrBot 始终 active，SnowLuma 保持运行；未修改其镜像、浏览器、数据或登录状态。

需要回退时，仅对 RSSHub 执行：

```bash
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.browser-active.yaml
cp -p /opt/rsshub/compose.lightweight.yaml /opt/rsshub/compose.yaml
docker compose -f /opt/rsshub/compose.yaml up -d --pull never
```

以上是备用回退命令，当前未执行。

## 42. B 站网络和浏览器响应诊断

执行时间：2026-10-23T19:21:58.2193200+08:00

```bash
set -eu
for url in 'https://www.bilibili.com' 'https://search.bilibili.com/all?keyword=LangGraph' 'https://api.bilibili.com/x/web-interface/nav'; do
 curl -sS -o /dev/null --connect-timeout 5 --max-time 12 -w 'HTTP=%{http_code} time=%{time_total}\n' "$url" || true
done
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const page=await browser.newPage();
 page.on("response",r=>{const u=new URL(r.url()); if(u.hostname.endsWith("bilibili.com")&&(u.pathname.includes("search")||r.status()>=400))console.log("RESPONSE",r.status(),u.hostname,u.pathname);});
 page.on("requestfailed",r=>{const u=new URL(r.url());console.log("FAILED",u.hostname,u.pathname,r.failure()?.errorText);});
 try {const r=await page.goto("https://search.bilibili.com/all?keyword=LangGraph",{waitUntil:"domcontentloaded",timeout:20000});console.log("NAV",r?.status());await page.waitForTimeout(6000);console.log("TITLE",await page.title());console.log("TEXT",(await page.locator("body").innerText()).slice(0,900));}catch(e){console.log("ERROR",e.message.slice(0,300));}
}finally{await browser.close();}
'

```

```text
远端退出码：0
HTTP=200 time=0.153853
HTTP=200 time=0.155019
HTTP=200 time=0.092909
NAV 200
TITLE LangGraph-哔哩哔哩_bilibili
TEXT 搜索
综合
视频
99+
番剧
0
影视
0
直播
0
专栏
71
用户
4
综合排序
最多播放
最新发布
最多弹幕
最多收藏
更多筛选 
84.1万
4803
21:16:49
尚硅谷LangGraph教程，langgraph入门，langgraph智能体项目实战
尚硅谷
· 7月15日
5万
80
19:04
挑战19分钟搞定，LangGraph快速入门与原理剖析
敲代码的码农
· 2月3日
1.7万
359
工具用不好？一勺料汁搞定
小佛懒羊
· 2天前
108
0
12:36:27
【LangGraph】LangGraph速通教程，LangGraph+WorkFlow+Agent智能体企业级项目开发实战！
码士集团-Java技术库
· 1小时前
13万
1315
03:58:18
【吊打付费】26年最新LangGraph多智能体实战教程全面剖析：多智能体架构+核心组件+代码实战，存下吧！很难找全的！
李宏毅AIAgent
· 4月23日
190.1万
5798
22:30:02
黑马程序员2026最新版LangChain+LangGraph开发实战全套视频课程，从Agent开发，到LangSmith的监控、调试、评估一套搞定
黑马程序员
· 3月20日
37
0
03:51:48
2026 LangGraph 全套实战｜15 天从入门到生产级 Agent，少走 99% 弯路，面试项目两用
langgraph
· 3小时前
757
0
01:16:47
【全栈 AI 架构】Langchain、Langgraph、Deepagents、Langsmith Agent 开发全生态解析
langgraph
· 10月13日
550
0
08:02:42
【吊打付费】26年最新LangGraph多智能体实战教程全面剖析：多智能体架构+核心组件+代码实战，存下吧！很难找全的！
Agent教程
· 昨天
1.2万
103
20:28:08
【B站精选】B站最全最细的LangGraph多智能体实战教程，2026最新版，多智能体架构+核心组件+代码实战，从入门到精通，存下吧！很难


```


## 43. 验证匿名 Cookie 搜索，不输出 Cookie 内容

执行时间：2026-10-23T19:24:07.5004458+08:00

```bash
set -eu
docker exec rsshub-rsshub-1 node --input-type=module -e '
import {chromium} from "patchright";
import fs from "node:fs";
const browser=await chromium.launch({headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
try {
 const context=await browser.newContext();const page=await context.newPage();
 await page.goto("https://search.bilibili.com/all?keyword=LangGraph",{waitUntil:"domcontentloaded",timeout:20000});
 await page.waitForTimeout(2000);
 const cookies=await context.cookies();
 const r=await context.request.get("https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=LangGraph&order=pubdate",{headers:{Referer:"https://search.bilibili.com/all?keyword=LangGraph"},timeout:15000});
 const data=await r.json();console.log("HTTP",r.status(),"API_CODE",data.code,"RESULT_COUNT",data.data?.result?.length??0,"COOKIE_COUNT",cookies.length);
 if(data.code!==0||!data.data?.result?.length)process.exitCode=1;
 else{fs.writeFileSync("/tmp/rsshub-bili-anonymous.env","BILIBILI_COOKIE_0="+cookies.map(c=>c.name+"="+c.value).join("; ")+"\n",{mode:0o600});console.log("Anonymous session saved privately; cookie values omitted.");}
}finally{await browser.close();}
'

```

```text
远端退出码：0
HTTP 200 API_CODE 0 RESULT_COUNT 20 COOKIE_COUNT 8
Anonymous session saved privately; cookie values omitted.


```


## 44. 配置独立匿名会话文件并仅重建 RSSHub

执行时间：2026-10-23T19:24:55.1942112+08:00

```bash
set -eu
install -d -m 700 /opt/rsshub/private
test ! -e /opt/rsshub/private/bilibili.env
docker cp rsshub-rsshub-1:/tmp/rsshub-bili-anonymous.env /opt/rsshub/private/bilibili.env
chmod 600 /opt/rsshub/private/bilibili.env
cp -p /opt/rsshub/compose.yaml /opt/rsshub/compose.before-anonymous.yaml
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/rsshub/compose.yaml')
s=p.read_text()
assert 'env_file:' not in s
s=s.replace('    environment:\n','    env_file:\n      - path: /opt/rsshub/private/bilibili.env\n        format: raw\n    environment:\n',1)
p.write_text(s)
PY
docker compose -f /opt/rsshub/compose.yaml config --quiet
docker compose -f /opt/rsshub/compose.yaml up -d --pull never
systemctl is-active astrbot

```

```text
远端退出码：0
active

 Container rsshub-rsshub-1  Recreate
 Container rsshub-rsshub-1  Recreated
 Container rsshub-rsshub-1  Starting
 Container rsshub-rsshub-1  Started

```


## 45. 使用匿名配置验证完整搜索订阅

执行时间：2026-10-23T19:25:49.8317035+08:00

```bash
set -eu
for file in /opt/rsshub/bilibili-probe.exit /opt/rsshub/bilibili-probe.http /opt/rsshub/bilibili-probe.err /opt/rsshub/bilibili-probe.xml; do
  if [ -f "$file" ]; then mv "$file" "$file.previous"; fi
done
nohup sh -c 'curl -sS --max-time 90 -o /opt/rsshub/bilibili-probe.xml -w "%{http_code}" http://127.0.0.1:1200/bilibili/vsearch/LangGraph > /opt/rsshub/bilibili-probe.http 2>/opt/rsshub/bilibili-probe.err; echo $? > /opt/rsshub/bilibili-probe.exit' </dev/null >/opt/rsshub/probe-launch.log 2>&1 &
echo "PROBE_PID=$!"

```

```text
远端退出码：0
PROBE_PID=117245


```


## 46. 检查关键词 RSS 返回结果

执行时间：2026-10-23T19:26:00.0426995+08:00

```bash
set -eu
if [ -f /opt/rsshub/bilibili-probe.exit ]; then
  printf 'CURL_EXIT='; cat /opt/rsshub/bilibili-probe.exit
  printf 'HTTP='; cat /opt/rsshub/bilibili-probe.http; printf '\n'
  python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
p=Path('/opt/rsshub/bilibili-probe.xml')
if p.exists():
    try:
        r=ET.parse(p).getroot()
        print('ROOT:',r.tag,'ITEMS:',len(r.findall('.//item')))
        for item in r.findall('.//item')[:3]:
            print(item.findtext('title'),item.findtext('link'))
    except ET.ParseError:
        print('Response is not valid RSS XML; bytes:',p.stat().st_size)
PY
else echo 'PROBE_RUNNING'; fi
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
systemctl is-active astrbot
free -m

```

```text
远端退出码：0
CURL_EXIT=0
HTTP=200
ROOT: rss ITEMS: 20
【LangGraph】LangGraph速通教程，LangGraph+WorkFlow+Agent智能体企业级项目开发实战！ http://www.bilibili.com/video/av117489669244545
【2026最新】B站最全最细的《Agent应用开发》教程，从入门到实战！手把手教你快速打造自己的专属智能体，一次性搞懂AI大模型智能体开发，学完薪资翻倍！ http://www.bilibili.com/video/av117489367254637
【全栈 AI 架构】Langchain、Langgraph、Deepagents、Langsmith Agent 开发全生态解析 http://www.bilibili.com/video/av117489182705537
NAME              CPU %     MEM USAGE / LIMIT
rsshub-rsshub-1   0.01%     234.7MiB / 512MiB
snowluma          2.25%     376.3MiB / 1.7GiB
active
               total        used        free      shared  buff/cache   available
Mem:            1740         910          81           2         924         830
Swap:           4095        1335        2760


```


## 47. 本机端到端验证
命令：Invoke-WebRequest -Uri 'http://127.0.0.1:1200/bilibili/vsearch/Cursor/pubdate' -TimeoutSec 25，并解析 XML 条目数。
本机测试失败：An error occurred while sending the request.

## 48. 恢复过期的本机 RSSHub 转发
本机 1200 监听仍在，但连接被重置。校验该端口占用者路径为 ali-instance-cli.exe 后，仅停止 PID 44792，再运行 Start-RSSHubTunnel.ps1。其他转发和 QQ 服务未触碰。


## 49. B 站关键词搜索最终验收

本节更新第 26、41 节的采集失败结论：

- 网络诊断：服务器直接访问 B 站主页、搜索页与 nav 接口均为 HTTP 200；Chromium 能看到搜索结果。
- 对照官方 cache.ts 源码，原匿名 Cookie 初始化等待 space 页面发起 nav 请求；该等待在当前环境超时。
- 使用 RSSHub 独立 Chromium 匿名访问搜索页得到匿名会话，验证搜索接口 HTTP 200、业务 code=0、20 条结果后保存 Cookie；没有个人账号登录、扫码或复制个人 Cookie。
- 匿名会话文件：/opt/rsshub/private/bilibili.env，目录权限 700、文件权限 600。通过 Compose env_file 的 raw 格式载入；任何记录中不包含 Cookie 值。
- 服务器测试：/bilibili/vsearch/LangGraph 返回 HTTP 200、有效 RSS、20 条。
- 本机转发过期后，仅恢复对应 RSSHub 转发进程；用 curl.exe --noproxy '*' 测试 /bilibili/vsearch/Cursor/pubdate 返回 HTTP 200、有效 RSS、20 条。
- 本机测试命令：curl.exe --noproxy '*' -sS --max-time 25 -o docs/rsshub-deploy/cursor-test.xml -w 'HTTP=%{http_code}' http://127.0.0.1:1200/bilibili/vsearch/Cursor/pubdate，然后解析 XML。
- RSSHub 最新采样约 235 MiB，AstrBot 与 SnowLuma 正常。没有开启定时采集或消息推送。

### 现在可访问的订阅

- http://127.0.0.1:1200/bilibili/vsearch/LangGraph/pubdate
- http://127.0.0.1:1200/bilibili/vsearch/Cursor/pubdate

把关键词替换为所需词语，中文和特殊字符应做 URL 编码。pubdate 表示最新发布。返回视频标题、简介、作者、时间和链接，不等于视频全文或字幕；搜索结果不是全站完整导出。

匿名会话可能过期，网站接口也可能变化。本次测试通过不代表长期稳定；后续如失败，先检查健康状态、错误日志和匿名会话有效性，不能将失败当作“没有新内容”。匿名会话与机器人环境完全独立。

安全提示：不要运行并公开带展开环境变量的 docker compose config 输出，不要将 /opt/rsshub/private 下文件放入 Git 或公开部署记录。


## 项目目录迁移
2026-10-23：本机相关文档与部署辅助文件迁移至 D:/notebooks/info-agent/docs；以上历史命令保留当时路径。维护脚本已改为写入新目录。服务器 /opt/rsshub 和现有服务不变。
