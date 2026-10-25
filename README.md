# 个人信息筛选助手

Agent 查询核心 v4 使用有界基础正文证据包和短编号选择，程序回填真实 ID／原文，传递真实视频时长与显式条件；响应超预算时保留完整证据并明确部分返回。query 仍需宿主显式提供 terms／时间条件，search 可无词浏览。旧策略缓存不复用；见[宿主约定](docs/Agent宿主调用与复验.md)和[十二题冻结复验](docs/Agent-v4冻结验收.md)。默认复验零 RSS、零模型，真实语义效果待用户在线验收。

现已提供面向 Agent 的本地 Python 查询核心和非交互 JSON CLI：先检索保留内容，再按需用一次模型选择证据；支持有界临时视频搜索、长期关键词增删、同目标兴趣修订和可撤销设置审计。没有新增插件、MCP 或部署服务。完整请求、错误与费用语义见 [Agent 核心接口](docs/Agent核心接口.md)。

Agent 漏报修复：按命中位置提取有界原文窗口，各来源文字独立保留，搜索／引用／详情均携带对应出处。旧正文前缀策略缓存已失效，旧请求重放不会隐式重复付费；不涉及新数据库迁移或个人简报策略调整。

```powershell
# 默认只查询已保留数据，不联网、不调用模型
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli search --term Codex --term 重置 --today
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli preferences
```

本机 Python / LangGraph 工作流：独立采集关键词视频、指定 UP 投稿／动态及关注视频／综合动态 RSS 到 SQLite 候选池，按需用一次逻辑模型筛选产生短名单，再由程序完成校验、有限偏好排序和约 80/20 常规／探索推荐。终端反馈自动触发本地学习，不增加模型调用或常驻进程。

## 运行

### 可视化界面

在编辑器运行 `main_gui.py`，会启动本地网页并打开浏览器；也可以执行：

```powershell
& D:\Anaconda3\envs\agent\python.exe -m info_agent.web
```

访问 `http://127.0.0.1:8765`，在界面中生成简报、仅采集预览、查看历史、提交／撤销反馈、查看偏好、修改兴趣和订阅来源设置。保持启动进程运行，按 Ctrl+C 关闭。端口占用时使用 `--port 8766`；不自动打开浏览器时使用 `--no-browser`。服务只监听本机，不新增依赖，不修改现有 CLI 的行为。

生成按钮会调用现有模型；“仅采集预览”不调用模型。切换关键词、来源或临时时长不重置偏好，旧推荐仍可评价；兴趣修改可明确选择“调整同一目标”延续学习，或“切换目标”隔离旧学习。升级自动备份旧 SQLite，不混合无法确认归属的目标。详细说明见 `docs/可视化界面.md`。

继续使用现有环境，无需重装依赖。在编辑器直接运行 `main.py` 会生成并显示简报；命令行：

```powershell
cd D:\notebooks\info-agent
& D:\Anaconda3\envs\agent\python.exe -m info_agent --preview
& D:\Anaconda3\envs\agent\python.exe -m info_agent --show
& D:\Anaconda3\envs\agent\python.exe -m info_agent --profile briefing_profile.json --show
```

RSSHub 本机转发需可用。`--preview` 或 `python -m info_agent collect` 只采集入池，零模型调用、不登记推荐历史。main.py／网页生成只读候选池，不隐式请求 RSS，每轮最多一次逻辑筛选，SDK 网络重试单独统计。候选不足可少推荐或零推荐。

`briefing_profile.json` 保留原有配置，旧格式兼容；完整可选项见 `briefing_profile.example.json`。模型工厂与 `.env` 凭据沿用现状。SQLite 数据保存在 `workspace/info_agent.sqlite3`；候选、运行记录、简报导出到 `workspace/briefings/<run_id>/`。

## 反馈和撤销

```powershell
python -m info_agent feedback
python -m info_agent feedback --run RUN_ID --item 2 --value useful
python -m info_agent feedback --run RUN_ID --item 3 --value not_useful --reason 太长
python -m info_agent feedback --run RUN_ID --item 1 --value later
python -m info_agent feedback --undo EVENT_ID
```

`useful / not_useful / later` 对应“有用／没用／暂时不想看”。原因可选，每次返回可撤销的事件 ID。同一条目再次反馈替换有效评价；撤销恢复先前未撤销评价。可加 `--key REQUEST_ID` 防止网络／脚本重试重复提交。没有反馈是未知；不记录或推断是否观看过。

## 验证和边界

```powershell
& D:\Anaconda3\envs\agent\python.exe -m pytest -q
```

Agent 核心本轮仅使用临时数据库、固定时钟、注入来源／模型和独立进程离线验证，没有真实采集、真实模型、正式推荐／反馈或真实设置修改。上一阶段只有限验证一个公开 UP 动态的 1 批 12 条，不代表查询准确率、全网覆盖或关注综合动态在线可用。未实现字幕、下载、转写、视觉识别、热度推荐、自动扩词、自动作者黑名单、外网部署或定时推送；支持用户明确 UP 屏蔽及仅服务存活时的定时采集。

完整用法、恢复及学习限制见 `docs/使用说明.md`；实现架构与调整原因见 `docs/项目架构书.md`。

网页接口包括 sources_v1、cover_images_v1、stable_preferences_v1；具体字段与状态见 `docs/前端对接约定-多来源与封面.md`。Python 接口独立于网页。升级后重启正在运行的本地 Python 网页进程并刷新原端口（默认 `http://127.0.0.1:8765`），无需重启 RSSHub／转发。


错误诊断现区分来源故障、模型请求、结构化解析、字段和 ID 校验、存储与导出故障；部分来源失败单独说明。失败任务按钮定位本次运行，旧记录不猜测具体原因。简报已提交但导出失败会明确提示“已保存，无需重新生成”，可用 `python -m info_agent recover` 恢复。诊断字段与排查步骤见 [使用说明](docs/使用说明.md#错误排查2026-10-25)。更新后重启原本地网页 Python 服务并刷新页面，RSSHub 与转发不需重启。


动态候选池已接通：每位 UP 独立开关／范围、动态类型、可撤销 UP 屏蔽、保守抽奖规则、动态 24/72/168 小时时效、独立任务与本地服务定时采集（默认关闭）。过滤命中不删除原始条目，图片仅展示，不识图。配置示例以 source_plan 为权威；旧配置兼容、学习不因来源或规则变化重置。

先刷新来源，再按需生成；生成失败不消耗池，成功提交才进入冷却。真实库 v3 迁移记录保留在架构书；v4 在首次新版打开旧库前另做 pre-agent 备份，保留旧运行、反馈、偏好原义。本轮没有通过网页修改真实设置，原服务需要重启加载更新。

动态候选池阶段原回归 225 项继续保留；Agent 核心最终回归数量见本文末尾和使用说明。广告规则 advertisement v1 与抽奖规则独立，可在原过滤页面开关；旧配置未显式包含新规则时维持原选择，不自动修改真实配置。


Agent 核心本轮最终回归：292 项通过（原 225 项＋新增 67 项），前端 JS 语法及 Python 编译检查通过。隔离浏览器验证同目标修订、切换目标、撤销后刷新及广告独立开关保存；未调用真实 RSS／付费模型、改真实订阅兴趣或新增正式推荐反馈。使用与限制见 docs/Agent核心接口.md；真实库 v4 备份迁移在首次新版打开时执行，原网页进程需重启。

查询漏报修复最终回归 **317 项通过**（保留原 292 项＋新增 25 项）。验证了后半段证据、跨来源短正文、预算与原文偏移、排除来源不可借用、缓存／重放和业务状态不变。本次无新增数据库迁移，未启动真实服务、改真实订阅或调用真实 RSS／模型；详细行为见 [Agent 核心接口](docs/Agent核心接口.md)。


2026-10-26 Agent v3 最终验证：370 项通过（原 317 项＋新增 53 项），Python 编译、前端 JS 语法与改动空白检查通过。冻结第 4／5／6 题默认隔离验收均找到参考条目，RSS 请求和模型调用均为零，semantic_verdict 保持 not_evaluated_offline。四条规则漏网样本启用时拦截、关闭时可见；新闻、骗局提醒、教程和结果公告反例保留。未修改真实配置／数据库／开关、未调用付费模型或重启生产服务；真实相关性与原第 5／6 题模型输出仍需用户显式在线复验。
