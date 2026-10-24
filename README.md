# 个人信息筛选助手

本机 Python / LangGraph 工作流：仅采集配置关键词的 RSS，用一次逻辑模型筛选产生短名单，再由程序完成校验、有限偏好排序和约 80/20 常规／探索推荐。终端反馈自动触发本地学习，不增加模型调用或常驻进程。

## 运行

### 可视化界面

在编辑器运行 `main_gui.py`，会启动本地网页并打开浏览器；也可以执行：

```powershell
& D:\Anaconda3\envs\agent\python.exe -m info_agent.web
```

访问 `http://127.0.0.1:8765`，在界面中生成简报、仅采集预览、查看历史、提交／撤销反馈、查看偏好和修改兴趣设置。保持启动进程运行，按 Ctrl+C 关闭。端口占用时使用 `--port 8766`；不自动打开浏览器时使用 `--no-browser`。服务只监听本机，不新增依赖，不修改现有 CLI 的行为。

生成按钮会调用现有模型；“仅采集预览”不调用模型。修改兴趣设置可能切换学习上下文，历史简报仍可阅读，旧上下文的反馈入口在网页中禁用。详细说明见 `docs/可视化界面.md`。

继续使用现有环境，无需重装依赖。在编辑器直接运行 `main.py` 会生成并显示简报；命令行：

```powershell
cd D:\notebooks\info-agent
& D:\Anaconda3\envs\agent\python.exe -m info_agent --preview
& D:\Anaconda3\envs\agent\python.exe -m info_agent --show
& D:\Anaconda3\envs\agent\python.exe -m info_agent --profile briefing_profile.json --show
```

RSSHub 本机转发需可用。`--preview` 只采集保存候选，零模型调用，不登记推荐历史。正式运行每轮一次逻辑筛选，SDK 网络重试单独统计。候选不足可少推荐或零推荐。

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

当前共 65 项测试通过（原后端 50 项及网页 API 测试）；覆盖反馈撤销、幂等、上下文隔离、配置冲突、异步任务、候选过滤及访问边界。实际浏览器预览取得 40 条候选、0 来源失败、0 次模型调用。标题、简介和元数据不能证明视频真实质量。未实现字幕、下载、转写、热度推荐、关键词扩展、主题／作者黑名单、外网部署和定时推送。

完整用法、恢复及学习限制见 `docs/使用说明.md`；实现架构与调整原因见 `docs/项目架构书.md`。
