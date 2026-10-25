# Agent v4：宿主调用与冻结复验

策略版本：`agent-evidence-v4-content-pack-refs`。本轮保持原依赖、前端、入口、数据库结构、个人简报、反馈、学习和过滤规则；不开发插件或 MCP，不加入向量检索、自动分词、额外模型调用或纠错重试。

## 工具选择

- 多条内容的更新筛选：`query_updates`，有候选且未缓存时一次模型调用。
- 明确找某条内容：`search_items`，只检索已保留资料。
- 已定位具体作品，询问署名、素材或正文细节：`get_item`，不联网、不增加筛选模型调用。
- `query_updates` 返回 `insufficient_evidence`，而 search 已定位具体条目时，宿主可以按需读取 `get_item`，不能立即断言数据库没有答案。

工具选择不能替代证据包修复：`query_updates` 本身也必须提供基础标题与正文。来源失败、范围内无匹配、找到候选但证据不足、模型失败要分别表述；同时检查 coverage 与 collection_failures。有限 RSS 采样不能证明全站没有更新。

## 宿主提供显式条件

保留完整用户问题 question；由宿主给出 terms，不从 question 自动分词或推断日期／来源／类型。terms 规范化去重后最多 8 个，每个最多 100 字符，组间 AND、组内已有别名 OR；跨允许贡献联合满足 AND 的逻辑保留。空 terms 在采集、模型及请求占位前返回 QUERY_TERMS_REQUIRED；search 仍可无词浏览。

“今天”显式传 time_range.period=today；其他日期传绝对 start/end，起点包含、终点不包含，默认 Asia/Shanghai。来源 UID 必须来自已知来源身份；不猜作者 UID、作品归属或角色别名。显式 max_duration_seconds 仅约束视频，视频时长未知时继续排除，非视频不受此项限制。

```python
from info_agent.agent import AgentCore

core = AgentCore(r"D:\notebooks\info-agent")
scope = {"profile_id": core.profile_id}
# 以下 query 示例有候选且未缓存时会调用模型，本轮没有执行。
result = core.query_updates({
    **scope, "question": "今天有没有 Codex 重置的消息？",
    "terms": ["Codex", "重置"],
    "time_range": {"period": "today", "timezone": "Asia/Shanghai"},
    "request_id": "host-v4-reset-001",
})
# 具体作品的细节：先定位，再读取；两步均无模型调用、无联网。
found = core.search_items({**scope, "terms": ["你留下的春天"]})
if found.items:
    detail = core.get_item({**scope, "item_id": found.items[0].id})
```

## 证据、时长与模型协议

模型输入来源仅限当前允许贡献。主要贡献按覆盖检索词组数降序，再按 kind/key/entry_key 稳定排序；不使用采集时间。先提供主要标题（最多 300 字符）、短正文（不超过 600 字符时完整提供）或长正文开头（最多 200），再补命中窗口、其他来源标题／正文，最后动态转发上下文。补充来源优先覆盖未覆盖的词组。body_text 为空或缺失时回退 excerpt；无正文标记 body_missing，不生成占位。作者与最多 12 个、每个最多 100 字符的标签仅作元数据，不能选作原文片段。

单条片段实际字符总量默认 1200，包括拆片重叠；相交窗口只在同来源同字段内合并。最终片段不超过 max_snippet_chars（默认 400），尽量按换行／句末拆分，交界重叠最多 40 字符，偏移始终对应连续原文。默认预算可容纳 300 字标题与 600 字短正文。总输入按最终完整提示和 JSON 字符数计量，含问题、条件、元数据和编号；按检索顺序装入完整有界包，下一个包放不下就停止，不缩小已装入基础正文。首包也装不下是 input_pack 预算错误。

每个实际发送条目使用 I1/I2…，条目内片段使用 E1/E2…；服务器持有真实 ID、原文、来源、字段和偏移映射。模型只返回：

```json
{"evidence":[{"item_ref":"I1","evidence_ref":"E2","reason":"该正文给出了用户询问的署名"}]}
```

reason 最多 200 字符；不让模型复制真实 ID、quote、标题、链接或时间。旧 id/quote/span_id 内部格式严格失败，不双协议兼容或重试。按结构与字段、原始选择列表 JSON 字符预算、全部 item_ref、全部 evidence_ref 与实际原文映射校验；全部原始项合法后按真实 ID 保留第一条，再检查不同 ID 数量上限。重复里的非法片段也整次失败；duplicates_removed 首次／缓存／重放保持一致。

程序直接取所选片段完整原文回填 quote 与 quote_evidence；item 标题、链接、作者、发布时间、duration_seconds 均对应实际引用贡献。视频时长只能依据 duration_seconds，标题里的实战／通关时间不等于视频时长。模型还会收到程序实际执行的 max_duration_seconds、绝对时间范围和内容类型限制；这些条件不代表内容真假已经核实。

仅共享活动标签、同名角色或品牌不足以入选；篮球希罗、明显影视剪辑和其他游戏不能仅因二重螺旋活动标签入选。没有图片／视频资料就不描述画面。作者主张、预告、条件承诺不升级为已核实事实。保留手机端 Codex 创建／接管与 Plus 会员推广的相关性正反例。仅检查提示存在、使用模拟选择不能证明真实语义效果。

## 输出装箱与安全错误

模型前先检查覆盖摘要、基础结构、单条必要证据与 200 字 reason 是否至少容纳一个结果；按请求最大 evidence 数预留省略计数。明显不可容纳时零模型调用。模型后保留完整已验证、已去重选择，按模型顺序装箱。每步测量完整响应 JSON；需要压缩当前条目展示信息时，先删除重复 dynamic_contexts 并设 contexts_truncated，再将 item.sources 缩到实际引用来源并设 sources_truncated。quote 和 quote_evidence 来源／偏移不删除、不截断。

当前条目仍放不下，已有证据则停止后续条目，返回 output_truncated=true 与 evidence_omitted_count（已验证但因装箱省略的条目数）。首条必要证据都放不下明确预算错误。不同 ID 超过请求 limit 仍 selection_count 失败，不作为正常部分返回。缓存与请求记录都保存完整选择，再用同一装箱函数生成首次、缓存、重放响应。

为保持三条路径在边界上的省略计数一致，装箱同时序列化首次、缓存、重放三种完整指标结构，预留受限请求编号、查询归属编号和 ISO 时间的实际字段宽度；不是固定任意字符扣减。最终实际响应也参与长度比较。elapsed_seconds 输出保留微秒精度。该保留可能使响应略小于 max_output_chars，但不会删减证据来凑条数。

保留 OUTPUT_BUDGET／MODEL_OUTPUT_BUDGET；error_details.phase 明确 input_pack、selection_chars、selection_count 或 response_pack，附 actual_chars/allowed_chars 或 actual_count/allowed_count。response_pack 的字符数可为装箱使用的完整缓存／重放预算信封长度。未知编号使用 unknown_item_ref_count、unknown_evidence_ref_count、cross_item_ref_count 区分；不回传未知编号内容、原始提示、供应商响应、凭据或堆栈。

新公开字段默认兼容：Item.duration_seconds=null；QueryResult.output_truncated=false、evidence_omitted_count=0；已有 duplicates_removed 默认 0。旧策略缓存不复用；旧完成请求重放 QUERY_CONTEXT_CHANGED，不自动再付费。新策略超时重试继续复用原 request_id。

## 冻结复验

本轮十二题入口、资料校验值、输入变化和人工判定说明见 [Agent-v4冻结验收.md](Agent-v4冻结验收.md)。默认仅在隔离库检索与检查输入包，零 RSS、零模型；--online 才允许真实模型。每题最多一次，失败不重试，不以修改原题／关键词／参考答案制造通过。

```powershell
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --output workspace/verification/agent-v4-offline.json
# 指定题号，可多次给 --question；默认仍零模型
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --question 4 --question 8 --question 9 --question 11 --question 12
# 仅由用户之后显式验收；本轮未运行，每个所选有候选题最多一次真实调用
& D:\Anaconda3\envs\agent\python.exe examples/agent_acceptance_v4.py --online --question 8 --question 12 --output workspace/verification/agent-v4-online.json
```

旧手机端 Codex／会员推广负例仍位于 tests/fixtures/agent_acceptance_20261025.json，旧三题入口 examples/agent_acceptance.py 保留。它的 model_request 宽词探针是原已公开的冻结设定，不冒充原题；本轮十二题从不改写问题或显式条件。旧报告未保存的原始供应商输出始终不补造。

本轮不修改生产配置、密钥、过滤开关或服务状态。加载新策略需由宿主之后重新加载 Python 进程，本轮未重启生产服务；不需要数据库迁移。
