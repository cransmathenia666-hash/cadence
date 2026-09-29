# cadence 交接文档

> 最近更新：2026-09-29（E-66：候选页 PC 目录/评审席与路径轨迹；界面走查归用户）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜分支 `feat/frontend-a-shell`（远端默认主分支）｜HEAD `f710006`，与 origin 同步；积压与本轮整改已分批入库并推送，工作区只余一份用户笔记未跟踪。origin 为公开库，历史备份 `D:/cadence-history-backup-20260923.bundle`。
> 其他工作树：`D:\cadence-flow-closure`｜`feat/flow-closure`、HEAD `d07967b`，只读保留。
> 当前唯一目标：候选页 PC 结果区已重构，静态检查通过，待用户走查；工作台、四旧页与双入口真实模型仍待走查，P4 排后。
> 下一条动作：用户在 `/candidates` 桌面端分别看“备选方向”目录/单条评审与“完整路径”轨迹，切探索对话、历史、采纳/否决；真实写入由用户决定，不清真实库。

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
| --- | --- | --- | --- |
| 后端规则、接口与闭环回归 | 通过 | 本轮 E-60；历史语义沿用 E-19/E-38/E-51 | 本轮后端验证范围见 E-60 |
| LLM provider 管理与调用记账（T10） | 通过 | E-26、E-32 | `llm.py`、或那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 统一外壳、工作台执行与报告回流 | 通过 | E-60（静态与构建）＋本轮 E-65（工作台 lint/tsc） | 工作台章节/目录切换仅静态验证，桌面操作待用户走查；`plan-tree-panel.tsx` 或 `workbench-view.tsx` 的阶段选择/操作变更后 E-65 失效 |
| 四问判断链路（T12） | 通过 | E-26、E-27（真实模型一次） | `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 变更后失效 |
| 候选清单、明确轮次追问与历史恢复 | 通过 | E-63（后端路由级）＋本轮 E-66（前端静态） | PC 目录/评审席/路径轨迹及刷新切页观感待用户走查；`candidates/page.tsx`、`candidate-card.tsx` 的展示/选择变更后 E-66 失效；`find-session.ts`、`api.ts`、`advisor.list_search_requests` 的逐轮契约变更后 E-63 失效 |
| 提案裁定与批准结果卡 | 通过 | 本轮 E-60（静态）；后端规则沿用 E-37 | 结果卡跳转与裁定视觉待走查；失效范围见 E-60 |
| 判断、档案、模型接入、报告四页新构图 | 通过 | E-64（lint/tsc） | 仅静态验证；桌面/窄屏与交互待用户走查；四页、`judgment-view.tsx`、外壳改动后失效 |
| 「找」候选清单与去重（T13） | 通过 | E-31/E-32（假上游）、E-33/E-34/E-36（真实模型两次 + 采纳落阶段） | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效 |
| 双入口对话整改（线程/追问/形态/更替/蓝图门槛/工作台建议/逐轮历史） | 通过 | E-62＋本轮 E-63（pytest 528 passed、隔离冒烟 17 步、前端 lint/tsc 通过） | 六项口径与复核缺口均落地；`advisor`/`dialogue`/`blueprint` 的门槛与历史相关函数、`main._search_round`、`db._ADDED_COLUMNS` 那 4 列、`/api/find/shape/keep`、候选页与提案页组件变更后失效；**真实模型观感与桌面走查归用户** |
| 档案录入（T22） | 通过 | E-29/E-30 | 那三条 profile 写路由与请求模型、`advisor.PROFILE_CATEGORIES` 变更后失效 |
| 采纳自动落阶段（2026-09-17 用户拍板） | 通过 | E-33/E-34/E-36 | `decide_candidate`、verdict 路由或 `VerdictResult` 变更后失效 |
| 多计划与严格分开（T24） | 通过 | E-39 | `plan.list_plans`/`close_plan`/`void_plan`、`db._ADDED_COLUMNS` 那条加列、`advisor` 的归属与过期逻辑、`find.py` 的计划上下文段、那三条计划路由、计划切换器与 `/candidates` 页面变更后失效；**走查归用户** |
| 三级结构：任务层 + 交付物验收（T23）＋节点「放回」（2026-09-28） | 通过 | E-38；历史全量 524 passed／1 例对话硬闸重试失败（与本改无关），冒烟 17 步含放回断言；本轮 E-65 前端静态 | `plan.py` 的判定与动作（`stage_completion` / `stage_finished` / `check_task` / `skip_task` / `submit_deliverable` / `reopen_node`）、`main.py` 四条动作路由（含 `/reopen`）、`deliverable_submission` 表变更后后端证据失效；计划树现为章节/目录切换，前端行为待用户走查（任务点标题展开、打勾先确认、已完成可放回） |
| 计划生命周期四态与历史计划出口（T27） | 通过 | E-42 | `plan.pause_plan` / `reopen_plan` / `list_plans` 的 `ended_*` 两字段、`proposals.decide` 的收尾判据、两条新路由、首页计划管理区与「历史计划」段、`api.ts` 的 `pausePlan` / `reopenPlan` 变更后失效；**浏览器走查归用户** |
| 计划级对话（T28：接着聊 + 档案变更提案能真写档案；**09-26 起信封可带结构化追问 → 界面问答卡（E-55）**） | 通过 | E-47、E-55 | `dialogue.py`（含 `Reply.questions`）、`plan_dialogue.questions` 列、`profile.py` 的写入规则、`proposals.decide` 的 profile_change 分支、`components/workbench/` 与 `components/agents/`、`lib/api.ts` 对话类型变更后失效；**真实模型未跑过追问，走查归用户** |
| 提案瘦身与页面分家（T29）＋节点字段写入口（T30） | 通过 | E-44、E-45 | `plan.update_node_fields` 与 `/fields` 路由、`proposals.decide`、`plan_tree` 的落后提醒两字段、`/judge` 与 `/proposals` 两类渲染、`plan-tree.tsx` 的改字段入口变更后失效；**页面走查归用户** |
| 计划对话的「一条可执行建议」（T31；2026-09-19 走查后放宽成一次可带 1–5 件任务） | 通过 | E-46 | `plan_change.py`、`dialogue.say` 的信封与提示词、`_land_suggestion`、`proposals.decide` 的 `plan_change` 分支、`plan-dialogue.tsx` 确认条与 `/proposals` 渲染变更后失效；**真实模型已跑过（他走查时用的就是它），批量那版走查归用户** |
| Agent 的资料读取：受控工具循环（P3.6，T32/T33；**T43–T45 整改加三处**：回话散文兜底、工具类别收中文别名且报错说人话、每轮开头的记忆变化摘要） | 通过 | E-47、E-51 | `backend/app/agent_runtime.py`、`agent_tools.py`、`dialogue.py` 与 `dialogue.say` 的循环、`agent_run`、`plan-dialogue.tsx` 的「本轮依据」变更后失效；**真实模型未跑，走查归用户** |
| 候选路径形状 · 阶段跳过 · 追问槽收口（P3.7，T34–T36） | 通过 | E-48 | `advisor` 的 `FoundList` / `_shape_problem` / `_shape_and_steps`、`find.py` 的 prompt 段、`plan.skip_node`、`advisor.pending_clarify` / `record_clarify`、`candidate.payload` 与 `learning_request.clarify` 两列、`/api/requests` 的 `clarify_answer`、`candidates/page.tsx` 与 `plan-tree.tsx` 变更后失效；**真实模型未跑，走查归用户** |
| 采纳落点留得住（T37，2026-09-21 走查截图触发） | 通过 | E-49 | `advisor.landing_plan` 的优先序、`decide_candidate` 写落点那一条、`candidate.landing_plan_id` 列、`blueprint.resolve_plan`、`/api/candidates` 的 `landing_plan_id`、`candidates/page.tsx` 的落点优先序变更后失效；**只验假上游** |
| 对话式规划与蓝图（T26） | 通过 | E-40/E-41 | `blueprint.py`、`proposals.decide` 的蓝图分支与 `ProposalDecideIn.selected`、`blueprint.resolve_plan` 的归属解析、`plan_chat` 表、`/candidates` 对话区与 `/proposals` 的蓝图渲染变更后失效；**真实模型未跑，走查归用户** |
| 记忆系统：三层记忆 · 候选 · 扫描 · 删除（P3.9，T38–T45） | 通过 | E-50、E-51、E-59 | `backend/app/memory.py`、`agent_tools`、`proposals.decide`、`/api/memory*`、`frontend/app/(app)/memory/page.tsx` 或 `api.ts` 变更后失效；**真实模型没跑过，走查归用户** |
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 后端部分由 E-19 与 E-38 覆盖 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义
**目标：见文件头；P4（T15–T17）排后。**

## 3. 当前开放问题

**双入口整改已收口（E-63）**：上一轮复核列出的缺口全部落地——蓝图要「最新回话就绪且不再提问」才可生成、空白目标/交付物生成期即拦；工作台以「原话里有明确修改要求」或「对紧邻的具体澄清问题回确认」为授权，只报近况时先澄清；找方向逐轮落库原话/意图/回复并进下一轮上下文，追问点名精确到轮、占用带属主（旧属主不能释放或提交接管者的占用）、只有「重新推荐」才更替同线程旧候选；候选页刷新恢复逐轮对话与「保持原形态」决定、失败保留原话、A→B→A 归一段。余下只有：真实模型措辞与桌面观感（归用户走查）。真库 4 列已幂等补上，未用真库实验。

候选队列——**一个仍有的事**：`start_reason` 未落库（加列 = Ask first），重看旧候选要依据得重问一轮。**记忆系统那侧**：周扫描的定时入口要等 P4 的 T16 周任务接上（两个开关已就绪，只差定时器）；真库记忆与收件箱为空——见下方「真实库现状」，2026-09-28 已整库清空。

- **真实库现状**：**2026-09-28 整库清空，随后据他本人的 Obsidian 知识库重建了长期档案**。清空清的是 19 张业务表（计划/节点/候选/提案/两种对话/运行记录/交付/记忆与扫描/台账流水/调用记账，自增主键归零），只留 `llm_provider`（2 个，密钥没动）——他对「档案与配置是否一起清」答的是「模型提供商留着，其他人全删了」。**现在**：`profile_item` 22 条五类齐全（`source_kind` = 20 条用户陈述 + 2 条待验证，15 条带事实时间、3 条带复核日期），计划类与记忆仍为空；回退点 `.bak-20260928-014049`（2026-09-17 那次只清计划类、留了档案，口径不同别混着对账）。
- **档案来源与知识库里的方向性事实**：22 条提炼自库外两份 vault——`C:\Users\123\Documents\Obsidian Vault\个人知识库`（93 篇，正式结论在 `10-生活/01-个人基础` 与三个领域总览）和 `D:\学习`（课程资产与进度）。**vault 不进本仓库**：个人内容只写本地库，不写进提交或本文件；取料按 SPEC「接本地库」的点名式两层，一次性脚本用完即删。清库前那些具体数字（候选 #22 落点、P3.6 真库未跑）**全部失效**，`/judge` 不必再为缺判据底座重新录料。那份库另记着一条未经他口头确认的方向：cadence 暂停新功能开发、留作以后的工程样本，当前学习主线是 Python 命令行小项目，实习投递 2027-03 才开始——**别主动推 cadence 新功能**。
- **两个已知边界**：① 去重只做「去空白 + 转小写」——换个说法的同一件事仍可能被当新候选；② 长输出慢（E-31/E-32/E-34）：最坏一次 `find` 约 6 分钟；提速得压 prompt，未做。
- **口径与约定**：候选在 `/candidates`，提案在 `/proposals`，判资料在 `/judge`，记忆在 `/memory`；`/` 跳转 `/workbench`，`/new` 为高级手工入口。报告按 URL 的计划/周检查点进入，侧栏携带所选计划；直接无参数访问仍取最新 active 计划。候选规划对话与计划长对话不混用；记忆批量批准仅「新增 + 用户明说」，其余逐条裁定；彻底删除仍需预览与确认。其余规则见 SPEC。
- **样式与零散口径**：`POST /api/report` 无防重复（正解是前端禁用）；框架生成的 `404`/`405` 文案仍是英文；provider 的「地址/模型」清不成空（留空 = 不改）；`ledger.fetch_active` 取的是「初始业务状态」（名字误导、行为没错）；前端 effect 里同步 setState 会被 lint 拦（取数写成 `.then` 回调）；**数据库连接关掉了 sqlite3 的跨线程检查**（不关会随机 500）。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——节点 / 候选 / 提案都有，`plan` 没有（`closed` 是完成，不是否决），所以 `plan` 的 `void` 由台账写（T27 起）。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、各种判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422` / 与现状冲突 `409` / 业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- **P2 取数架构：浏览器直连**（决策 26）。前端用客户端组件，`lib/api.ts` 在浏览器里 fetch 后端，因此受 CORS 名单约束。不采用服务端取数路线（理由见 SPEC 第 10 节）。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端；LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 44 条见 `docs/SPEC.md` 第 18 节（双入口对话修订为第 44 条），除用户明确要求不重新讨论；台账拆列（方案 A）只在 SPEC 第 17 节第 2 条那三个条件满足时才重开。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，**只留本机、已不入库**，不得删除或改写。
- 本地库 `data/cadence.db` 存着用户真实档案与手工验收痕迹（现状见第 3 节）——**不得清库**。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
| --- | --- | --- | --- | --- |
| E-19 / E-26 / E-27 / E-29 / E-30 / E-31 / E-32 / E-33 / E-34 / E-36（2026-09-15–17） | P0–P3 早期：CORS 与错误形状、四问（真机一次）、档案录入与防重复、候选链路与两次真机「找」＋采纳落阶段 | `test_advisor.py` / `test_profile_write.py` / `test_candidates.py` / `test_llm.py` 可复跑 | 通过：错误键集恒为 `{detail, errors}`；四问没给 id 又不说「依据不足」判不合格；档案五令牌外 422、同文本 409；候选越界与禁区命中判不合格、两次不落一条；采纳落同名阶段、撞名 409 | 各自失效条件见第 1 节对应行。**真实模型只跑过 1 次（四问）与 2 轮（「找」）** |
| E-37 / 2026-09-17 | **T14：提案两条后端路由 + 两个正式页**。`pytest -q` 216 passed、冒烟 9 步；lint/tsc exit=0 | `test_proposals.py` 可复跑 | 通过：只列 pending；裁定落业务终态并补 `decided_at`；缺理由 400、已裁定 409、不存在 404 | `proposals.py`、那两条路由与请求模型、两个页面、`api.ts` 两个函数变更后失效。**只验假上游**；**走查归用户** |
| E-38 / 2026-09-17 | **T23 三级结构与交付物验收**（决策 30–32）：`plan_node.level` 加 `task`、新表 `deliverable_submission`、打勾 / 跳过 / 交交付物三条路由。`pytest -q` 229 passed、lint/tsc exit=0 | `test_task_layer.py` 可复跑 | 通过：四组合判定符合规则；跳过算完成且理由进台账；交付物只对阶段、重提交留痕；打勾/跳过只对任务 | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-39 / 2026-09-18 | **T24 多计划与严格分开**（决策 33–34）：`learning_request.plan_id` 加列、`list_plans` / `close_plan` / `void_plan` + 三条路由。`pytest -q` 244 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：默认列表只给进行中；采纳落归属计划、无归属不指明 409；同计划上一轮未裁定的候选过期、已裁定的不动 | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-40 / E-41 / E-52（2026-09-18 / 09-26） | **T25「找」+ T26 对话式规划与蓝图**：`pytest -q` 274→279、E-52 447 passed；冒烟 10 步、lint/tsc 通过 | `test_candidates.py`、`test_blueprint.py` 可复跑 | 通过：已裁定反馈进「找」，未采纳不能规划；蓝图版本与勾选生效、同名阶段复用、冲突预拦；E-52 真机不重问已答、候选贴原话 | 失效条件见第 1 节 T25/T26；T26 只验假上游 |
| E-42 / 2026-09-18 | **T27 计划生命周期四态**（决策 33）：`pytest -q` 294 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：暂停幂等、收尾/作废不可暂停、作废是单向门；重开放回进行中 | 失效条件见第 1 节同名行。**走查归用户** |
| E-44 / 2026-09-18 | **T29 提案瘦身与页面分家**（决策 28/29/30 修订）：`pytest -q` 301 passed、冒烟 10 步、lint/tsc exit=0 | `test_proposals.py` / `test_progress.py` / `test_plan.py` 可复跑 | 通过：阶段收尾不再产提案（判定仍在）、落后只出提醒、老类型批准被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-45 / 2026-09-18 | **T30 节点字段写入口**（决策 38）：`pytest -q` 312 passed、冒烟 11 步全绿、lint/tsc exit=0 | `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：id 与引用不断、流水留改前改后、挪截止日带动落后量、清日期、缺理由 / 非法日期 / 给任务设交付物一律拒、撞同名被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-46 / 2026-09-18 | **T31 计划对话的「一条可执行建议」**（决策 39）：`pytest -q` 332 passed、冒烟 11 步、lint/tsc exit=0 | `test_dialogue.py` 可复跑 | 通过：建议一次最多一条、坏建议一条都不落、批准后 **id 不变**且留流水、加东西按序建节点、超 5 件与批内重名都拒、计划离开进行中时批准被拒 | 失效条件见第 1 节同名行。**真实模型只被他走查跑过** |
| E-47 / 2026-09-20 | **P3.6 Agent 核心（T32/T33）**（决策 40）：`pytest -q` 354 passed、冒烟 11 步、lint/tsc exit=0 | `test_agent.py` / `test_dialogue.py` 可复跑 | 通过：目录=注册表、四个工具只读、跨计划参数被拒、两条上限闸、撞上限如实交代且**一条提案都不落**、三种结局都留一行 `agent_run` | 失效条件见第 1 节 Agent 那行。**只验假上游** |
| E-48 / 2026-09-20 | **P3.7：T34 路径形状 + T35 阶段跳过 + T36 追问槽收口**（决策 41）。`pytest -q` 385 passed（+31）、冒烟 11 步、lint/tsc exit=0。真库已补两列 | `test_candidates.py` / `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：`path` 只落一行伞候选、形状与条数对不上判不合格重试、**步骤名不进禁区**、采纳回执带 steps；阶段跳过留痕 + 视同完成 + 落后不算 + 周打卡不给跳；追问只问事实、已答的不再问（回答拼原话见 E-63） | 失效条件见第 1 节 T34–T36 那行。**只验假上游** |
| E-49 / 2026-09-21 | **T37 采纳落点留得住**（决策 33 ② 补的洞）：`pytest -q` **392 passed**（+7）、冒烟 11 步、lint/tsc exit=0；真库已补列 | `test_candidates.py` / `test_blueprint.py` 可复跑 | 通过：采纳后重新取行仍定得下来、换计划要拒、落点计划被收尾后报得清楚、否决不留落点 | 失效条件见第 1 节 T37 那行。**只验假上游**；真库里候选 #22 是修之前的，不受益 |
| E-53 / 2026-09-26 | **/proposals 迁壳重做**：lint/tsc/build 全 0；浏览器实测勾选级联、全不勾禁批准、驳回两段式 | `design-samples/real-round5/` 截图（本机） | 通过：勾选逻辑原样复用、`selected` 仅蓝图传、回执语义保留并补记忆 effect 分支、未知 kind 兜底 | 失效条件见第 1 节提案裁定行。**真库只走展示与取消，批准/驳回未真点** |
| E-55 / 2026-09-26 | **工作台四件套**：历史导航条、追问渲染成问答卡（信封加 `questions`）、流式展开、思考态。452 passed、冒烟 17 步、lint/tsc 0；真库补列 | `test_dialogue.py` 末段 4 例可复跑 | 通过：追问落库且 view/turn 带出、自由题归一化、空标题/单选项带原因重说 | 失效条件见第 1 节计划级对话行。**真实模型未跑，走查归用户** |
| E-50/E-51 / 2026-09-21 | 记忆新增/扫描/裁定/彻底删除与走查整改；历史 pytest 429→443、冒烟17步 | test_memory/test_agent/test_dialogue 可复跑 | 通过：日期优先、续期不增行、散文重试后兜底、中文工具别名、记忆变化摘要；只验假上游 | memory/agent_tools/dialogue 的这些行为变化需复验，真实模型扫描待用户 |
| E-60 / 2026-09-27 | 主树整合：四项前端命令 0、pytest 460 passed、隔离冒烟 17 步、diff-check 过（备份在 `%TEMP%` cadence-merge-20260927） | 同轮 patch 与日志 | 通过：三方合并源代码哈希未变；新接口 GET /api/learning-requests 与 clarify_request_id 纳入 | 相关路由/组件被 E-61～E-63 重写后失效；不覆盖真实模型/浏览器 |
| E-63 / 2026-09-28 | 双入口整改：蓝图就绪、工作台意图闸、找方向逐轮历史/形态裁定/占用与 redo；`pytest -q` 528 passed、隔离冒烟 17 步、前端 lint/tsc 通过；真库幂等补 4 列 | `test_candidates` / `test_dialogue` / `test_blueprint` / `test_agent` 可复跑 | 通过：只报近况先澄清、蓝图未就绪拦截、找方向刷新恢复轮次与形态决定、同线程历史进模型，跨线程与旧占用不串 | 后端契约变更后失效；真实模型措辞与桌面观感待用户 |
| E-64 / 2026-09-29 | 四旧页重做：评判纵向展开/历史回看、档案分类/侧边编辑、服务商列表/详情/流水、报告检查点/回执。前端 `npm run lint`（0 error，9 warning）、`npx tsc --noEmit`、`git diff --check` 通过；`impeccable detect --json` 为 `[]` | 本轮源码；未跑浏览器、未写真库 | 仅静态验证；桌面/窄屏、键盘与真实操作待用户走查；四页、`judgment-view.tsx`、外壳改动后失效 |
| E-65 / 2026-09-29 | 工作台 PC 右栏：默认当前阶段章节，目录替换正文，阶段选择/前后翻页、交付物与执行清单重排；`npm run lint`（0 error，9 条既有 warning）、`npx tsc --noEmit`、`git diff --check -- frontend/components/workbench/plan-tree-panel.tsx frontend/components/workbench/workbench-view.tsx` 通过 | 本轮源码；未跑浏览器、未写真库 | 仅静态验证；阶段切换、任务/交付物/报告与计划管理真实操作待用户桌面走查；两个工作台组件的相关行为变更后失效 |
| E-66 / 2026-09-29 | 候选页 PC：左侧目录替换折叠清单、右侧单条评审；路径为横向步骤轨迹，保留对话/历史/推荐依据/裁定。`npm run lint`（0 error，9 既有 warning）、`npx tsc --noEmit`、`git diff --check` 通过 | 当前源码；未跑浏览器、未写真库 | 仅静态验证；两种形态、历史切换与裁定待用户走查；候选页及候选卡展示变更后失效 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库；加表/加列都走它
.\.venv\Scripts\python.exe -m pytest -q           # 全绿
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe -c "import pathlib,tempfile; from tools import smoke_p1; smoke_p1.TEMP_DB=pathlib.Path(tempfile.mkdtemp(prefix='cadence-merge-smoke-'))/'smoke.db'; raise SystemExit(smoke_p1.main())"
.\.venv\Scripts\python.exe tools\dev_server.py    # 开发热重启（**勿用 `uvicorn --reload`**，本机必有日志 Reloading 后卡死，原因见下）

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（建议使用 localhost；已纳入源 next.config 的 allowedDevOrigins，其他来源未复验）
                   # 现有页面：/workbench · / · /new · /report · /candidates · /judge · /proposals · /memory · /providers · /profile
                   # 外壳唯一挂在 app/layout.tsx；(app)/layout.tsx 仅路由分组；/memory 只在 (app)/memory/page.tsx
npm run lint; npx next typegen; npx tsc --noEmit; npm run build  # 本轮依次执行，均 exit=0
```

**端口与服务**：后端 8000、前端 3000。停服务按端口用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端改 3000）找 PID 再 `Stop-Process`；后端 worker 名字不含 uvicorn，别只杀父进程。**热重启只用 `tools\dev_server.py`**：本机 Python 3.14 下 `uvicorn --reload` 首次保存必卡在 `Reloading...`；替代脚本 1–2 秒重启并写 `%TEMP%\cadence-uvicorn.log`。全新克隆先装后端依赖；前端 `npm ci` + `build` 后再 `tsc`。

| 任务类型 | 必读文件 |
| --- | --- |
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **前端（写代码前必读）** | `frontend/AGENTS.md`（`next dev` 自动生成，提交它保持工作区干净）与 `frontend/node_modules/next/dist/docs/` 的 `upgrading/version-16.md`、`01-getting-started/06-fetching-data.md`——Next 16 有破坏性变更；再叠 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更与需求背景 | `docs/SPEC.md` 第 10、11、17、18 节（产品/架构）与第 1–9 节（需求背景）、`docs/U2-触达详解.md`（`docs/未决项讨论.md` 已被 SPEC 取代） |

## 7. 给下一个 Agent 的启动提示
1. 开工先完整读本文件并核对 Git 与未提交改动；只读当前目标相关源码与规则。P4 验收标准在 `tasks/todo.md` P4 一节，SMTP 授权码不是开工前提。
2. **探测一律指向临时库**，绝不拿 `data/cadence.db` 做实验（曾误建节点）；细则照 `tools/smoke_p1.py` 与项目 `AGENTS.md`。
