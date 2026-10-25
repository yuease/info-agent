# Agent 查询核心（本地 Python，v4 查询策略）

2026-10-26 v4：基础正文证据包、短编号选择、真实时长传递与确定性输出装箱已实施；详见下文。保留前轮显式 terms 行为：query_updates 必须同时提供完整 question 和至少一个有效 terms，不再从 question 提取词或日期。宿主根据用户问题提供核心关键词、显式时间与来源；缺省时间不额外限制，缺省来源为当前允许来源。缺词返回 QUERY_TERMS_REQUIRED，发生在采集、模型和付费请求占位之前。search_items 仍可省略 terms 浏览。这是明确的接口行为变化，调用方必须调整；方法入口不变，不新增迁移。

本轮提供 `info_agent.agent.AgentCore` 和 `python -m info_agent.agent_cli`，无需网页或交互终端。没有 DSH／AstrBot 插件、MCP、HTTP Agent 路由或新的部署服务。个人简报、反馈、采集、候选池及本地定时器继续使用原入口。

## 宿主绑定与调用

宿主使用可信项目目录和配置路径构造实例，再把下面七个方法包装成工具。不要向模型开放构造函数、任意文件路径或数据库路径。请求中的 profile_id 必须等于实例绑定的真实配置 ID；填写其他 ID 不能选择其他档案。相同 profile_id 与 RSSHub 地址属于同一内容命名空间，宿主若需要隔离数据，必须分配不同档案。此机制是本地宿主授权边界，不是多用户认证服务。

```python
from info_agent.agent import AgentCore, CoreError

core = AgentCore(r"D:\notebooks\info-agent", expected_profile_id="default")
scope = {"profile_id": core.profile_id}
try:
    result = core.search_items({
        **scope, "terms": ["Codex", "重置"],
        "time_range": {"period": "today", "timezone": "Asia/Shanghai"},
        "limit": 10,
    })
    print(result.model_dump_json(indent=2))
except CoreError as error:
    print(error.as_dict())  # 稳定错误码、安全说明、可选安全详情
```

输入和输出都是显式 Pydantic 模型；方法同时接受相应模型和字典，未知输入字段拒绝。模型定义在 `info_agent/agent_models.py`，公开导出在 `info_agent/agent.py`。构造可注入 clock、model、fetcher 和受限 QueryPolicy，以便离线测试。示例 `examples/agent_core_example.py` 默认仅检索，不联网、不调用模型。

## 两条流程

个人简报：当前长期来源、兴趣、学习、视频时长、动态推荐有效期、来源公平预算、推荐冷却和探索配额保持原策略。

问题查询：从允许访问的保留内容中先执行文字检索，再对相关结果截取。忽略个人兴趣限制、自动学习排序、推荐冷却、探索、动态推荐 TTL 和个人默认时长；显式 max_duration_seconds 仅约束视频，未知视频时长被排除，图文不受该项限制。查询不写 runs/recommendations/feedback，不更新学习头或权重。直接复用解析、身份去重、规则、模型工厂和安全诊断，不调用个人简报生成函数。

查询不是全网搜索，RSS 有限采样也不能证明一个时间段没有任何更新。`coverage.scope=retained_allowed_data`、`limited_sampling=true` 始终明确这一点。结果和覆盖不足可以同时出现。

## search_items

必需 profile_id；可选字段：

- terms：词之间 AND，每词与同组配置别名 OR。逐项去首尾空白、忽略空项，按 NFKC／大小写规范化去重并保留首次顺序；清理后最多 8 个词、每词最多 100 个规范化字符，超限 INVALID_ARGUMENT。两接口共用同一逻辑。search 不传词／清理后为空可浏览；query 则返回 QUERY_TERMS_REQUIRED。
- time_range：仅显式参数决定。start/end ISO 日期或时间，起点包含、终点不包含；无偏移时间按 timezone 解释，默认 Asia/Shanghai。`period=today` 与 start/end 互斥，返回绝对区间；缺省不额外限制时间，问题文字中的“今天／today”不会触发过滤。
- sources：`[{kind,key?}]`，可进一步缩小当前允许来源，不能启用停用来源。kind 支持 keyword_search/up_uploads/up_dynamics/following_videos/following_dynamics/temporary_keyword。
- content_types：video/text/image/article/audio/live/unknown 的子集。
- limit/page：默认 20／1；宿主策略默认最多 50 条一页。返回 has_more、next_page、snapshot_version、truncated。下一页传相同 limit 和 snapshot_version，数据或覆盖状态变化返回 PAGINATION_CHANGED，避免错页。
- collection_mode：默认 existing_only；temporary_keywords 才执行一次有界临时视频采集。temporary_keywords 指定采集词，省略时只可使用显式 terms，不从问题猜词。
- request_id：临时采集应明确传入，并在超时重试时保持相同。默认仅检索没有网络、模型或付费请求日志。
- max_duration_seconds、timeout_seconds：可选显式约束，不能超过宿主总预算。

返回原始 ID、标题、连续原文 snippet、作者、发布时间、链接、内容类型、真实 duration_seconds（未知 null）、来源证据（含 collected_at、temporary、query_id、url、author）及截断标志。snippet_evidence 标明该片段的来源、字段及原文 start/end。每条片段默认最多 400 字符，整个输出默认 10000 字符，超限分到下一页；单条元数据已超限则明确 OUTPUT_BUDGET，不悄悄丢失条目。分页排序为词匹配强度、实际命中文字贡献的发布时间倒序、稳定 ID。

按发布时间过滤。缺失时间标为 time_basis=missing；有日期范围时排除并在覆盖情况计数，无范围时可返回。首次采集时间只用来说明采集覆盖，绝不冒充发布时间。72 小时只影响个人推荐，显式查询过去日期可读仍保留的旧动态。先逐条应用来源／时间／类型／规则，再按视频身份合并展示；各允许贡献的文字始终独立保存于本次读快照，不以较长正文覆盖较短正文。item 的标题、作者、链接、published_at 和 matched_published_at 对应该 snippet／quote 实际使用的同一来源；sources 仍保留全部允许贡献。没有时间的证据不会借用另一来源的时间。转发上下文仍属于其 RSS 贡献，字段路径另行标识，不宣称它是原作者的新发布。

## query_updates

在 search 参数基础上，question 保留完整原问题，terms 必需至少一个有效词。核心不再拆分自然问句，也不增加关键词生成模型。“有没有、找一下、订阅里”等通常是表达意图，不是默认检索词；“订阅里”通常指已配置来源，不等于付费会员。用户明确说“今天”时由宿主传 time_range.period=today；指定作者／来源由宿主给 sources，不能让核心从文字猜 UID 或内容类型。不确定角色别名、作品归属或作者身份时不凭空编造映射。`agent_query.aliases` 继续仅使用已有受控配置，例如 `"重置":["reset"]`、`"二创":["同人","手书"]`，不自动生成。

```python
evidence = core.query_updates({
    **scope, "question": "今天有没有 Codex 重置相关消息",
    "terms": ["Codex", "重置"], "time_range": {"period": "today"},
    "request_id": "host-query-v4-001", "limit": 4,
})
```

默认最多 30 个候选，单条所有实际原文片段默认最多 1200 字符（含拆片重叠），全局输入默认 18000 字符，最多 8 条证据，完整响应默认最多 10000 字符，模型 max_tokens 默认 2500。全局输入按真正发送的固定提示、问题、显式条件、元数据、编号与片段序列化长度计算。按检索顺序装入完整有界包，下一个放不下就停止并标明 input_truncated；首包无法容纳则 OUTPUT_BUDGET / input_pack，绝不反复减半基础正文。

主要来源按覆盖检索词组数量降序，再按 kind/key/entry_key 稳定排序，不按采集时间。依次提供主要贡献可引用标题（前 300 字）、短正文（不超过 600 字完整提供）或长正文前 200 字、其余命中窗口；补充来源优先覆盖未覆盖词组，再提供标题／正文；最后用剩余预算提供稳定字段顺序的转发上下文。body_text 为空或缺失回退 excerpt，没有正文则 body_missing=true。作者正常提供，标签最多 12 个、每个最多 100 字且 tags_truncated 明示；两者仅为元数据，不可选作证据。基础正文与窗口共享单条预算；被排除贡献的标题和正文绝不进入包，跨来源联合 AND 召回不变。

相交的开头与命中窗口在同一来源、同一字段内合并；最终可选择片段不超过 max_snippet_chars（默认 400），尽量在换行／句末拆分，交界最多重叠 40 字，重复字符计入预算。每个片段保留原始连续文字及准确偏移，不拼字段或来源。默认 1200 足以保留 300 字标题与不超过 600 字的短正文；自定义小预算按优先顺序截取并明确 text_truncated/title_truncated。

内部模型输入按实际发送顺序编号 item_ref=I1/I2…，每条独立 evidence_ref=E1/E2…；片段仅提供编号、field、text 与必要来源标识，真实 ID、片段哈希和偏移映射由服务器保管。模型输入还包括 duration_seconds（未知 null）、published_at、content_type、title、author、有界 tags，及 applied_conditions 中程序实际执行的显式时长限制、绝对时间范围、类型限制。视频时长只能依据 duration_seconds，标题的通关／比赛／实战用时不等于视频时长；条件已通过不应无故否认，但通过过滤不证明内容真假。

内部返回仅为 `{"evidence":[{"item_ref":"I1","evidence_ref":"E2","reason":"如何帮助回答问题"}]}`，reason 最多 200 字。模型不复制真实 ID、原文 quote、标题、链接或时间；旧 id/quote/span_id 格式不兼容、不重试。固定校验顺序：结构／字段长度；原始完整选择 JSON 字符预算；全部 item_ref；全部 evidence_ref 所属关系与允许来源真实原文映射；全部合法后按真实 ID 保留第一条；再检查不同 ID 数不超过 limit/max_evidence。任何未知编号或重复项里的非法片段整次失败，不猜测修正。duplicates_removed 默认为 0，首次／缓存／重放保持一致。

程序直接将完整所选片段回填 quote 与 quote_evidence，item 元数据来自该片段实际贡献。模型前检查是否至少能容纳基础结构、coverage、必要证据和最多 200 字 reason，明显无法容纳时不调用模型。模型后按已验证选择的顺序装箱，每次计算完整响应 JSON。展示字段压缩仅依次删除当前条目重复 dynamic_contexts、缩减 item.sources 至引用来源，并设置对应已有截断标志；quote、quote_evidence 来源与偏移不可删除、截短。仍放不下则停止后续选择；已有结果返回 output_truncated=true、evidence_omitted_count 为因装箱省略的已验证条目数；首条也无法容纳明确 response_pack 预算错误，不冒充无证据。模型不同 ID 超请求 limit 仍严格 selection_count 失败。

缓存及请求记录保留完整已验证选择，不只保留装箱后可见条目；首次、缓存、重放用同一函数装箱。为稳定边界上的截断计数，完整响应计量同时考虑三种真实指标结构，并按受限编号长度和运行时间字段宽度预留缓存／重放元数据；实际最终 JSON 也始终受硬上限约束。elapsed_seconds 序列化至微秒精度，详见[宿主约定](Agent宿主调用与复验.md)。

新增 QueryResult.output_truncated=false、evidence_omitted_count=0，兼容旧 JSON 默认值；公开 Item.duration_seconds=null，search_items/query_updates/get_item 均使用实际展示来源的真实值，不从标题推断。error_details.phase 区分 input_pack、selection_chars、selection_count、response_pack，附实际字符数／上限或数量／上限；未知 item_ref、无此 evidence_ref、片段不属于该条目分别有安全计数，不返回原始错误内容。

相关性提示保留手机创建／接管 Codex 与 Plus 会员推广负例，补充仅共享二重螺旋活动标签、篮球希罗、影视剪辑与其他游戏不得据此入选。作者和标签不可单独证明正文或具体画面，模型只列最终入选条目，reason 说明如何帮助回答问题；不根据 reason 措辞做硬删除，不针对作者或测试条目编码。可少选或不选；作者主张、预告、条件承诺不升级为事实，不根据未提供的图片／视频生成内容。离线模拟不能证明真实语义质量。

返回 evidence.quote_evidence，含窗口 ID、实际 quote 的字段 start/end、原文 text 和 source（实际链接、作者、发布时间、采集时间等）；item 元数据由该来源回填。所有 start/end 使用 Python Unicode 码点，起点包含、终点不包含；NFKC／大小写转换仅用于定位，返回的片段不规范化、不改写，覆盖全半角、字符展开和组合字符的偏移映射。

不生成最终答案、不通用事实核查。作者观点仍是作者主张；无文字图片不识别、不编图片摘要，结果会提示文字证据不足。由宿主 Agent 组织最终回答，并引用原文。

outcome 分为 found、no_match、insufficient_evidence（检索到内容但模型未选到证据）、collection_failed、model_failed。已有内容与临时采集部分失败可并存于 found；请同时检查 coverage.insufficient/reasons、collection_failures 和 error_code，不把它们概括为“没有更新”。

## 临时来源与长期关键词

长期关键词增量保存在原订阅配置，延续原最多 5 词、单词最长 100 字符及至少启用一个有效来源的约束；不会因为添加一个词覆盖其他词。关键词来源停用时添加关键词不自动启用来源。返回 data_ready、needs_collection、next_action，添加订阅并不等于已采集到数据。

临时采集只覆盖 B 站关键词视频搜索，不搜索全站图文动态。默认最多 3 词／3 请求／20 秒／60 条，硬上限集中在 QueryPolicy；不重试、不无限扩词，不调用模型。请求失败按来源返回安全错误信息。成功批次进入 temporary_items，temporary_origins 记录查询归属；保留原文，当前规则在读取时应用。默认不会进入个人候选；后续同档案查询可复用，其他档案不可访问。

把同一临时词添加为长期订阅时，仅提升确有该词搜索来源证据的数据，并保留实际采集时间；其他临时词结果不提升。仅凭正文碰巧出现新关键词不能提升。长期来源停用后其独有内容不参与默认查询，但同视频如有独立可访问临时来源可继续按该临时证据读取。

## get_item / get_source_status

`get_item({profile_id,item_id,max_body_chars?,sources?})` 读取保留文字，默认 4000 字符、宿主默认上限 8000，返回 body_chars/truncated/source_text_limited。复用当前档案、当前允许来源、规则和人工屏蔽检查，不能通过 ID 绕过。只读已存文字，不访问原链接、字幕或图片；images_analyzed 始终 false。旧记录仅有简介时明确标记文字有限，不联网补全文。

详情按允许来源公平分配正文预算，不再只读取最长正文。body 中不同来源／字段以 `[独立来源片段]` 分隔，分隔标记不是原文；body_spans 给出每段的 body_start/body_end、原字段 start/end 与来源。可用这两对偏移验证 `body[body_start:body_end]` 对应原字段切片，不把来源 A 的文字与来源 B 的时间拼在一起。详情没有新增时间参数，读取当前允许来源的保留历史；若只需要查询中某个来源，用 sources 缩小范围。长度不足会标明 truncated，不能保证一次读尽全部来源。

`get_source_status({profile_id,include_disabled?})` 分来源返回 last_checked_at、last_success_at、retained_count、availability、安全错误码／说明。失败不覆盖最后成功时间；SOURCE_BUDGET 未请求不伪装成检查。ready 仅表示最后一次检查成功，不保证采样完整；partial 表示保留旧成功但当前失败；unknown 不伪造登录状态。最多返回 50 个状态并标明截断；查询覆盖摘要最多列 8 个来源，保留 sources_truncated。状态读取不请求 RSSHub。

## get_preferences / update_preferences / undo_preference_change

Agent 仅能读取和增量修改 keywords 与 interests，不能改凭据、模型、地址、来源计划、定时器或其他 Profile 字段。普通查询从不隐式修改。宿主传入明确修改请求即执行，不增加逐条确认。

```python
old = core.get_preferences(scope)
change = core.update_preferences({
    **scope, "expected_version": old.version, "request_id": "settings-001",
    "add_keywords": ["Codex"], "remove_keywords": [],
    "interests": "AI 编程工具的更新与实践", "interest_mode": "same_goal",
})
restored = core.undo_preference_change({**scope, "change_id": change.change_id})
```

version 是当前完整规范化配置的摘要，配置已变化返回 SETTINGS_CONFLICT，先重新读取。相同 request_id／相同参数返回原结果并标记 replayed；同编号不同参数返回 IDEMPOTENCY_CONFLICT。返回 change_id、before/after、changed_fields、采集准备状态。网页也走 SettingsService 相同事务、身份与审计逻辑，保留网页 expected 快照冲突及活动任务保护。

兴趣必须明确 interest_mode：same_goal 是同目标文字修订，保留稳定 goal_id 和学习 context；switch_goal 创建新 goal_id，隔离旧权重。旧配置未带 identity 时，首次同目标修订以旧兴趣为 legacy_anchor，精确保留原 context；已有不同目标不合并。关键词、来源、规则和临时时长继续不改变学习身份。特征解释规则（duration_buckets/max_tags）仍参与 context，不能混用不同含义权重。

撤销按变更字段和字段审计头检查，只回退该次 keywords／interest，不覆盖后来无关字段；同字段后续更改，即使又改回同值，也明确 PREFERENCE_UNDO_CONFLICT。重复撤销返回原撤销结果；依次撤销最新同字段修改后可再撤销更早修改。兴趣撤销恢复原身份和已有学习版本，不删除反馈、学习或撤销事件。此接口不回退网页修改的任意高级字段，不支持再“撤销撤销”事件。

设置保存采用 durable prepared 审计意图 → 原子替换文件 → 提交数据库。磁盘失败不假报成功；重开 Store 或同请求重试完成同一意图。若外部文件已发生其他修改，标记冲突，不自动覆盖。新采集／生成在设置意图待提交时拒绝启动；已有活动任务阻止设置提交。私有审计库包含配置前后快照，不作为公开日志或 Agent 返回。

## 缓存、超时与隐私

缓存键包含档案和允许来源、规范化问题／词、绝对时间范围、相关候选文字与来源版本、规则／屏蔽／查询预算、模型身份与 model_version、提示版本。新相关内容、内容修订、日期、别名、规则、模型或来源变化不会误用旧结果。默认有效 900 秒；cache_ttl_seconds=0 关闭跨请求缓存。命中或无候选零模型调用；命中仍重验当前访问范围并回填当前采集时间。

当前提示／结果处理策略版本为 `agent-evidence-v4-content-pack-refs`。内容版本包含各允许来源的独立记录，较短正文修订也会使缓存失效。模型输入不含会随重复采集变化的 collected_at/query_id，避免它们影响窗口装箱；返回证据仍回填最新值。旧策略缓存自然失效但不删除；旧策略的已完成请求（包括没有 strategy_version 的旧记录）重放返回 QUERY_CONTEXT_CHANGED，不自动再付费。若宿主明确需要按新策略重查，须使用新请求编号。

每次返回 metrics：cache_hit、replayed、logical_model_calls、http_attempts、rss_requests、actual_requests、elapsed_seconds、tokens。缓存／幂等重放本次费用为零，原调用指标放 origin_metrics。供应商不提供用量时为 null，不编造 token。生产查询克隆现有客户端设置剩余超时、max_tokens，并关闭 SDK 重试，个人简报的模型工厂策略保持原状。

同步等待默认最多 45 秒，宿主策略最多 120 秒。SQLite 持久请求占位和进程身份保证跨进程同请求编号只领取一次。等待超时可能早于底层请求结束：宿主保持进程运行并用相同编号／参数重试，可取得同一结果；绝不能自动换编号。CLI 退出或原进程崩溃后在途结果无法确认，返回 QUERY_OUTCOME_UNKNOWN，不自动再次付费。活进程的在途请求不按短租约超时抢占；无需进度接口或新服务。

重放时相关内容／权限变化返回 QUERY_CONTEXT_CHANGED，不自动追加模型；同编号跨日的相对日期已变返回 IDEMPOTENCY_CONFLICT。宿主若确实需要重新查询，应明确使用新编号。记录为私有 SQLite；不保存完整问题、完整提示、原始模型输出／供应商异常，不写公开查询日志。片段和检索词是结果与审计所需私有数据。错误保留稳定错误码和安全计数／字段路径，不能确定的类型仍保守归类。

## CLI

在项目目录使用现有 Python；下列 search/preferences/sources/item 不调用模型：

```powershell
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli search --term Codex --term 重置 --today
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli sources
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli item ITEM_ID --max-body-chars 2000
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli preferences
# 只有显式增加采集模式才联网；仍不调用模型
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli search --term Codex --collect-temporary --temporary-keyword Codex --request-id temp-001
# query 有候选且未缓存时才调用现有付费模型，保留同一编号重试
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli query '今天有没有 Codex 重置相关消息' --term Codex --term 重置 --today --request-id query-v4-001
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli update --expected-version VERSION --request-id settings-001 --add-keyword Codex
& D:\Anaconda3\envs\agent\python.exe -m info_agent.agent_cli undo CHANGE_ID
```

全局 `--root`／`--profile` 放在子命令之前，profile 必须在 root 内。每个子命令都可用 `--request-file request.json` 读取完整 typed 请求，避免问题出现在进程命令行；输出 JSON，错误固定安全码。退出码 0=成功（含未匹配或证据不足），1=模型／采集失败，2=输入／范围／存储／版本等接口错误。终端不会追问确认。

## 迁移与当前验证边界

既有 SQLite schema v4（早前阶段已引入，本轮结构不变）在旧库任何变更前在线备份 `*.pre-agent-*.sqlite3`（更旧库仍按 pre-stable/pre-pool），事务创建查询、临时来源、逐源观察、设置审计和兴趣归属表。旧 runs JSON/context、候选、推荐、反馈、学习权重及撤销均不改写。仅精确对应旧 context 的目标归属可审计延续，无法确认的归属隔离记录，不合并不同目标、不相加权重。迁移幂等，重开不重复备份或重计权；恢复到旧备份后会再安全迁移。首次新版打开自动执行。

此前查询核心阶段使用过隔离浏览器验证设置流程；本轮只做临时数据库、固定时钟、冻结输入、模型注入和独立进程离线验收，没有操作生产浏览器或服务。未运行真实 RSS 采集、真实模型或真实设置修改，也未新增正式推荐／反馈。上一阶段有限真实 UP 动态验证不能视为本轮查询准确率或全网覆盖证明。

限制：确定性文字检索不保证复杂中文分词／语义召回；无向量库、不识图、不自动判断作品归属；旧简介无法还原被旧版截断正文。池、临时内容、请求与审计目前无自动清理；缓存过期后不再命中，但行仍保留。当前扫描保留数据，规模增大可另加索引，输入／输出与网络／模型预算始终受限。网页无 Agent 查询面板或设置审计列表，撤销长期设置用 Python／CLI。源码更新后重启原 main_gui.py／info_agent.web 进程并刷新浏览器；无需重启 RSSHub 或转发。

历史 v3 完整离线基线为 370 项通过。本轮不新增数据库迁移，使用隔离库、固定时钟、真实冻结条目与明确构造的模拟模型结果验证 v4；完整结果与十二题输入包对照见 [Agent-v4冻结验收.md](Agent-v4冻结验收.md)。未运行真实 RSS 或付费模型复验；输入内容齐全、引用校验正确不等于全部相关性或模型稳定性问题已经解决。

2026-10-26 v4 完整离线回归：405 项通过（保留原 370 项，新增专项 35 项）；Python 全量编译、既有前端 JS 语法和本轮文件空白检查通过。十二题默认复验与旧手机端负例入口均零 RSS、零模型。尚未进行真实模型验收。
