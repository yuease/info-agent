# Agent 查询核心 v4 冻结验收

策略：`agent-evidence-v4-content-pack-refs`。本轮只进行隔离库离线测试，不重新采集 RSS，不调用真实模型，不修改生产配置、凭据、数据库、过滤开关或服务状态。没有新增依赖、迁移或前端变更。

## 冻结资料及真实性

`tests/fixtures/agent_v4_20261026/` 保存用户指定的五份验收资料的逐字节副本：采集快照、题目与参考、运行结果、诊断及真实验收报告。`manifest.json` 记录 SHA-256；入口先核对资料完整性。原始目录为 `C:\Users\yly\Documents\Codex\2026-10-24\zhe\outputs`。

时钟固定在 `2026-10-26T00:31:39.689826+08:00`；今天为 10 月 26 日，昨天为 10 月 25 日。十二题的问题、显式 terms、时间／时长条件、参考 ID、禁止 ID 和署名要求均直接读取冻结原题，不改写以制造通过。

`v3-input-baseline.json` 是实施前用旧 v3 代码在同一冻结条目上重新生成的离线输入包，用于真实内容覆盖对照。它不是上次真实模型收到请求的抓包，也不是历史供应商原始响应。上一轮未保存的供应商响应不补造；未知编号、非法片段和预算异常仅用明确构造的模型结果测试。

上一轮手机端 Codex／Plus 会员推广负例仍在 `tests/fixtures/agent_acceptance_20261025.json`，已有独立三题入口也继续保留。新提示保留这些相关性要求，未修改抽奖／广告规则或真实开关。

## 复验入口

```powershell
# 默认：隔离库，仅检索与实际输入包检查，零 RSS、零模型
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --output workspace/verification/agent-v4-offline.json
# 仅指定题目，仍为离线模式；重复指定同一题也只执行一次
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --question 4 --question 8 --question 9 --question 11 --question 12
# 以下仅供用户之后主动执行，本轮没有运行
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --online --question 8 --question 12 --output workspace/verification/agent-v4-online.json
```

每次运行新建并关闭临时库，不访问生产库、真实设置或来源服务。`--online` 才使用现有模型；每个所选有候选题最多一次调用，失败不重试，不进行纠错或额外语义选窗调用。每次在线运行是新的隔离验收，可能再次付费，不复用前次运行缓存。可以先检查离线输入再由用户自行执行在线命令。

安全输出包含原题、搜索结果、输入包、输入长度、截断标志，以及可选在线结果／安全预算阶段；不包含密钥、原始供应商响应、异常堆栈。离线 semantic_verdict 为 not_evaluated_offline；注入模型及真实在线模式均保留人工复核要求，不能仅凭 found 判定正确。

## 第 4、8、9、11、12 题输入变化

以下长度包含固定提示与序列化输入。旧值来自上述重新构建的 v3 基线，新值来自实际 v4 输入构建函数，不是在线模型准确率。

- 第 4 题：旧输入 2993 字符，证据是 30 字标题和若干止流标签，没有视频时长。新输入 2239 字符，保留可引用标题，加入完整 45 字正文，`duration_seconds=224`，明确传入 300 秒限制。314 秒条目仍被程序排除；标题中的“1分58秒”仍保留为原文，提示明确它不是视频时长。
- 第 8 题：旧输入 1527 字符，只有 20 字标题和“苏乙”标签。新输入 2210 字符，标题可引用，38 字完整正文进入片段：`模型：二重螺旋/徐大大哟i 动作：NatsumiSan 场景：ChyetYi`。动作及场景署名无需第二次模型调用即可被选取。
- 第 9 题：旧输入 3526 字符，第一条有标题和标签，第二条仅标签可引用，没有时长。新输入 2432 字符，两条均有可引用标题，时长分别为 33、43 秒，明确传入 60 秒限制；185 秒条目仍排除。两条均无可用正文，明确 body_missing；标签作为有界元数据保留，不能单独证明画面或问题结论，真实模型仍可少选或不选。
- 第 11 题：旧输入 2508 字符，两条的片段均只有作者名（各来源重复提供）；标题只是元数据。新输入 3396 字符，两条 33 字、29 字标题分别成为可引用片段，主题是 mod 副本改动／刷取窍门和全角色刷取／关键队伍配装。来源独立保留；相同标题在不同来源中仍分别溯源并计入预算。没有正文则明确缺失，不用作者名冒充摘要。
- 第 12 题：旧输入 1127 字符，只提供 6 字标题。新输入 2163 字符，加入完整 43 字正文：`图像素材 二重螺旋官方漫画 《流光照影》 BGM 《My Little Garden》`。标题及正文均有真实字段偏移与编号映射。

第 8、12 题验证的是数据库已保存署名能进入输入，并不独立核实作品署名真假。第 4、9 题验证的是真实存储时长和程序实际约束能被模型看到，不保证模型之后一定正确解释。

## 测试覆盖与结果边界

原有完整离线基线为 370 项。新增 `tests/test_agent_v4.py` 覆盖真实署名、作者命中标题、长正文开头与后半段窗口、主要贡献与补充来源优先级、来源排除、空正文回退、标签限额、拆片／重叠／偏移、单条及总输入预算、短编号回填、非法编号与去重前严格校验、真实时长、响应预检／展示压缩／部分装箱／首条失败、安全阶段、完整选择缓存与重放，以及精确预算边界上的计数一致性。

原有测试仅将内部模拟模型协议迁移为短编号；显式 terms、显式时间、设置保护、业务只读、来源独立、旧策略缓存隔离、跨进程幂等、抽奖／广告反例等回归保留。旧 id/quote/span_id 输出严格拒绝另有专项测试；没有给生产实现添加双协议适配。

本轮完整离线测试 **405 项通过**（原 370 项保留并适配内部协议，新增 v4 专项 35 项）。Python 全量编译检查通过；原前端 JS 语法检查通过。详细测试日志在 `workspace/verification/agent-v4-full-tests.txt`，离线十二题产物在 `workspace/verification/agent-v4-offline.json`，旧手机端负例产物在 `workspace/verification/agent-v4-previous-negatives.json`。Python 编译及现有前端 JS 语法检查不需要联网或安装依赖。全仓 `git diff --check` 中，既有 `docs/RSSHub部署记录.md` 第 1597、1603 行有两处原有尾随空格，本轮未修改该文件；本轮文件单独检查。

尚未执行任何真实模型复验。提示词存在、模拟模型通过、证据映射正确都不能证明全部相关性或模型稳定性问题已经解决；尤其是仅活动标签关联、同名角色、影视／其他游戏干扰、作者主张与条件承诺，仍需用户在线人工判断。

## 兼容性

公开 Python／JSON 方法及真实 ID、evidence 基本结构保留。Item 新增可选 duration_seconds（默认 null）；QueryResult 新增 output_truncated（默认 false）、evidence_omitted_count（默认 0），原 duplicates_removed 默认 0。quote 为程序回填完整原片段，来源与偏移不压缩；返回可省略部分条目但不会把它们算成无匹配。

内部模型协议切换为 item_ref/evidence_ref/reason，不兼容旧格式且不自动重试。缓存和请求保存完整已验证选择；新策略不复用旧缓存，旧请求重放保持 QUERY_CONTEXT_CHANGED，不会自动再次付费。数据库结构与生产运行状态未改变。

## 本轮修改文件

- 核心：`info_agent/agent_core.py`、`info_agent/agent_models.py`、`info_agent/text_evidence.py`、`info_agent/retrieval.py`、`info_agent/errors.py`、`info_agent/core_errors.py`。
- 测试：`tests/test_agent_core.py`、`tests/test_agent_evidence.py`、`tests/test_agent_v3.py`、新增 `tests/test_agent_v4.py`。
- 冻结资料：新增 `tests/fixtures/agent_v4_20261026/` 下五份原始资料副本、`manifest.json` 与 `v3-input-baseline.json`；旧 `agent_acceptance_20261025.json` 保持原样。
- 示例：更新 `examples/agent_core_example.py`，新增 `examples/agent_acceptance_v4.py`；原三题入口保持原样。
- 文档：`README.md`、`docs/Agent核心接口.md`、`docs/Agent宿主调用与复验.md`、新增本文。

开始实施时工作区已有大量未提交改动；本轮保留这些内容，不覆盖、不提交或撤销用户既有工作。没有修改前端文件、生产配置、依赖声明、过滤规则或迁移文件。
