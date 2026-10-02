# cadence 交接文档

> 最近更新：2026-10-02（E-76：提案页补回规划对话入口）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`feat/frontend-a-shell`｜本轮分三批提交（后端 3668ff9→前端 759cb7f→文档批收尾），领先公开 origin 6 个提交、未推送；工作区只余未跟踪的用户笔记，备份 `D:/cadence-history-backup-20260923.bundle`。
> 其他工作树：`D:\cadence-flow-closure`｜`feat/flow-closure`、`d07967b`，只读保留。
> 当前唯一目标：成果闭环 P2 前后端已落地（E-74 后端、E-75 前端接线，复核问题均已修复）；提案页已能把蓝图退回同一条规划对话（E-76），待用户桌面走查。蓝图增强对照与 P4 走查未完成。
> 下一条动作：重启后端与前端 → 在 proposals 点「补充信息，回规划对话」验证回到原候选会话，再走查 P1（/new→工作台证据/验收/收尾）与 P2（采纳→规划对话→蓝图→proposals 确认契约批准/退回）；随后进 P3。

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
| --- | --- | --- | --- |
| 后端规则、接口与闭环回归（含成果闭环 P1＋P2 后端） | 通过 | E-74（679 passed、隔离冒烟 29 步、复核 6 项修复）；历史语义沿用 E-19/E-38/E-51 | 采纳/会话/蓝图/批准/契约/验收/升级函数或新表变更后失效 |
| LLM provider 管理与调用记账（T10） | 通过 | E-26、E-32 | `llm.py`、或那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 统一外壳、工作台执行与报告回流（含成果接线） | 通过 | E-64＋E-65＋E-73（静态） | 桌面操作待用户走查；`plan-tree-panel.tsx`、`stage-review-section.tsx`、`workbench-view.tsx` 或 `/new` 变更后 E-73 前端证据失效 |
| 四问判断链路（T12） | 通过 | E-26、E-27（真实模型一次） | `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 变更后失效 |
| 候选清单、明确轮次追问与历史恢复（采纳进规划会话） | 通过 | E-63＋E-75（候选页会话化静态） | PC 目录/评审席/刷新切页观感待用户走查；`candidates/page.tsx`、`candidate-card.tsx` 的展示/选择变更后 E-75 失效；`find-session.ts`、`api.ts`、`advisor.list_search_requests` 的逐轮契约变更后 E-63 失效 |
| 提案裁定与批准结果卡（含 v2 蓝图契约确认、退回规划入口） | 通过 | E-75＋E-76（前端静态）＋E-37（后端） | PC 待用户走查；裁定接口或提案组件变更后失效 |
| 判断、档案、模型接入、报告四页新构图 | 通过 | E-64（lint/tsc） | 仅静态验证；桌面/窄屏与交互待用户走查；四页、`judgment-view.tsx`、外壳改动后失效 |
| 「找」候选清单与去重（T13） | 通过 | E-31/E-32（假上游）、E-33/E-34/E-36（真实模型两次 + 采纳落阶段） | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效 |
| 双入口对话整改（线程/追问/形态/更替/蓝图门槛/工作台建议/逐轮历史） | 通过 | E-62＋E-63（528 passed） | 六项口径落地；`advisor`/`dialogue`/`blueprint` 的门槛与历史相关函数、`main._search_round`、`db._ADDED_COLUMNS` 那 4 列、`/api/find/shape/keep`、候选页与提案页组件变更后失效；**真实模型观感与桌面走查归用户** |
| 档案录入（T22） | 通过 | E-29/E-30 | 那三条 profile 写路由与请求模型、`advisor.PROFILE_CATEGORIES` 变更后失效 |
| 采纳进规划会话（P2 起取代「采纳自动落阶段」） | 通过 | E-74 | `decide_candidate`、会话助手或 verdict 路由变更后失效 |
| 多计划与严格分开（T24） | 通过 | E-39 | `plan.list_plans`/`close_plan`/`void_plan`、`db._ADDED_COLUMNS` 那条加列、`advisor` 的归属与过期逻辑、`find.py` 的计划上下文段、那三条计划路由、计划切换器与 `/candidates` 页面变更后失效；**走查归用户** |
| 三级结构：任务层 + 交付物验收（T23）＋节点「放回」（2026-09-28） | 通过 | E-38；本轮 E-65 前端静态 | `plan.py` 的判定与动作（`stage_completion` / `stage_finished` / `check_task` / `skip_task` / `submit_deliverable` / `reopen_node`）、`main.py` 那几条动作路由、`deliverable_submission` 表变更后后端证据失效；计划树前端（章节切换、打勾先确认、已完成可放回）待用户走查 |
| 计划生命周期四态与历史计划出口（T27） | 通过 | E-42 | `plan.pause_plan` / `reopen_plan` / `list_plans` 的 `ended_*` 两字段、`proposals.decide` 的收尾判据、两条新路由、首页计划管理区与「历史计划」段、`api.ts` 的 `pausePlan` / `reopenPlan` 变更后失效；**浏览器走查归用户** |
| 计划级对话（T28：接着聊 + 档案变更提案能真写档案；09-26 起信封可带结构化追问 → 问答卡） | 通过 | E-47、E-55 | `dialogue.py`（含 `Reply.questions`）、`plan_dialogue.questions` 列、`profile.py` 的写入规则、`proposals.decide` 的 profile_change 分支、工作台与 agents 组件、`lib/api.ts` 对话类型变更后失效；**真实模型未跑过追问，走查归用户** |
| 提案瘦身与页面分家（T29）＋节点字段写入口（T30） | 通过 | E-44、E-45 | `plan.update_node_fields` 与 `/fields` 路由、`proposals.decide`、`plan_tree` 的落后提醒两字段、`/judge` 与 `/proposals` 两类渲染、`plan-tree.tsx` 的改字段入口变更后失效；**页面走查归用户** |
| 计划对话的「一条可执行建议」（T31；2026-09-19 走查后放宽成一次可带 1–5 件任务） | 通过 | E-46 | `plan_change.py`、`dialogue.say` 的信封与提示词、`_land_suggestion`、`proposals.decide` 的 `plan_change` 分支、`plan-dialogue.tsx` 确认条与 `/proposals` 渲染变更后失效；**真实模型已跑过（他走查时用的就是它），批量那版走查归用户** |
| Agent 的资料读取：受控工具循环（P3.6，T32/T33；**T43–T45 整改加三处**：回话散文兜底、工具类别收中文别名且报错说人话、每轮开头的记忆变化摘要） | 通过 | E-47、E-51 | `backend/app/agent_runtime.py`、`agent_tools.py`、`dialogue.py` 与 `dialogue.say` 的循环、`agent_run`、`plan-dialogue.tsx` 的「本轮依据」变更后失效；**真实模型未跑，走查归用户** |
| 候选路径形状 · 阶段跳过 · 追问槽收口（P3.7，T34–T36） | 通过 | E-48 | `advisor` 的形状校验与追问槽、`find.py` 的 prompt 段、`plan.skip_node`、`candidate.payload` 与 `learning_request.clarify` 两列、`/api/requests` 的 `clarify_answer`、候选页与计划树变更后失效；**真实模型未跑，走查归用户** |
| 采纳落点留得住（T37，2026-09-21 走查截图触发） | 通过 | E-49 | `advisor.landing_plan` 的优先序、`decide_candidate` 写落点那一条、`candidate.landing_plan_id` 列、`blueprint.resolve_plan`、`/api/candidates` 的 `landing_plan_id`、`candidates/page.tsx` 的落点优先序变更后失效；**只验假上游** |
| 对话式规划与蓝图（T26；增强模式） | 通过 | E-69＋E-71＋E-74 复验 | 标准兼容、增强两位审查员独立表态/一次修订/失败不落提案均有假上游覆盖；`blueprint.generate_blueprint`、审查 payload 契约（reviewers / revision_resolution）、蓝图请求契约、候选模式选择、审查席与模式切换组件变更后需复验；**真实模型效果与页面交互归用户走查** |
| 记忆系统：三层记忆 · 候选 · 扫描 · 删除（P3.9，T38–T45） | 通过 | E-50、E-51、E-59 | `backend/app/memory.py`、`agent_tools`、`proposals.decide`、`/api/memory*`、`frontend/app/(app)/memory/page.tsx` 或 `api.ts` 变更后失效；**真实模型没跑过，走查归用户** |
| 每周触达与导出（P4，T15–T17） | 通过 | E-67（566 passed、冒烟 20 步、lint/tsc 0、detect `[]`） | `notify.py` / `export.py` / `jobs/weekly_checkpoint.py`、`/api/notify` 三条路由与 `app_setting` 表、`components/settings/weekly-reminder.tsx` 变更后失效；**真实邮件没发过（空实现）、桌面观感归用户** |
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 后端部分由 E-19 与 E-38 覆盖；标准 4（周检查点三问）的代码路径由 E-67 覆盖 | 需 T18 两周试用（真邮件与 Windows 任务计划还没接） |

## 2. 当前目标与完成定义
**目标：见文件头；成果闭环 P1＋P2 前后端已落地（E-74/E-75），待用户桌面走查；蓝图增强对照与 P4 走查未完成；P3/P5 未开工，T18 未开始。**

## 3. 当前开放问题

双入口整改已收口（E-63；措辞与观感归用户走查）。候选队列那边**还有一件事**：`start_reason` 未落库（加列 = Ask first），重看旧候选要依据得重问一轮。**触达那侧（E-67）还差三步**：真邮件要 SMTP 授权码（环境变量 `CADENCE_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD`，只填本机 `.env`，空实现）、Windows 任务计划每周一 09:00 调一次（用户自己配）、开关与收件邮箱在设置页拧。

**蓝图增强模式已实现（E-69；09-30 E-71 改多审查员）**：标准模式保留原行为；增强模式由两位职责不同、互不通气的审查员（水平核对员／结构审查员）对初稿**各自独立表态**——赞同写认可哪点与依据，反对写反对哪点、理由与建议调整；与现有计划的硬冲突由「系统防冲突检查」补位第三个座位。有「必须修改」的反对才自动修订一次并逐条交代，最多 6 次调用（每位审查员含一次结构重试），失败不静默降级、不落提案。模式与各审查员结论随提案保存，提案页审查席默认展开、初稿与修订对照折叠；具体验收口径见 `docs/ideas/蓝图多Agent审查增强模式.md` 与 SPEC 决策 45。审查语义效果尚未由程序证明，需用户用真实模型同题对照。**P2 后端已落地（E-74）**：采纳进规划会话、蓝图 v2 带契约、批准原子建树；复核确认 6 项（嵌套事务、双会话互卡、终态再规划、落点回填、legacy 绕开、legacy 计划绕过升级闸）均已修复回归。expired 定时未接（懒过期）；P2 前端已接线（E-75），SPEC 决策 27/33/36 与 OC-05～07 已同步。

- **真实库现状**：**2026-09-28 整库清空，随后据他本人的 Obsidian 知识库重建了长期档案**（清空清的是 19 张业务表，只留 `llm_provider`：2 个、密钥没动）。**2026-09-29 复查**：`profile_item` 23 条五类齐全（21 条用户陈述 + 2 条待验证；15 条带事实时间、3 条带复核日期），另有计划 2 个、候选 36 条（09-29 仍有新请求），记忆 / 收件箱 / 通知记录仍为空。回退点 `.bak-20260928-014049`（2026-09-17 那次只清计划类、留了档案，口径不同别混着对账）。
- **档案来源与知识库里的方向性事实**：那批档案提炼自库外两份 vault——`C:\Users\123\Documents\Obsidian Vault\个人知识库`（93 篇，结论在 `10-生活/01-个人基础` 与三个领域总览）与 `D:\学习`（课程与进度）。**vault 不进本仓库**：个人内容只写本地库，不写进提交或本文件；取料按 SPEC「接本地库」的点名式两层，脚本用完即删。那份库另记着一条未经他口头确认的方向：cadence 暂停新功能开发、留作工程样本，当前学习主线是 Python 命令行小项目，实习投递 2027-03 才开始——**别主动推 cadence 新功能**。
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
- PC 设计准则见 `docs/前端设计方向.md`、`docs/前端页面重做模板.md`；旧样式禁令已废，`design-samples/` 仅存档。移动端暂缓优化，保留可用性。
- 已定决策共 44 条见 `docs/SPEC.md` 第 18 节（双入口对话修订为第 44 条），除用户明确要求不重新讨论；台账拆列（方案 A）只在 SPEC 第 17 节第 2 条那三个条件满足时才重开。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，**只留本机、已不入库**，不得删除或改写。
- 本地库 `data/cadence.db` 存着用户真实档案与手工验收痕迹（现状见第 3 节）——**不得清库**。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
| --- | --- | --- | --- | --- |
| E-19 / E-26 / E-27 / E-29–E-36（2026-09-15–17） | P0–P3 早期：CORS 与错误形状、四问（真机一次）、档案录入与防重复、候选链路与两次真机「找」＋采纳落阶段 | `test_advisor.py` / `test_profile_write.py` / `test_candidates.py` / `test_llm.py` 可复跑 | 通过：错误键集恒为 `{detail, errors}`；四问没给 id 又不说「依据不足」判不合格；档案五令牌外 422、同文本 409；候选越界与禁区命中判不合格、两次不落一条（采纳副作用已由 E-74 改进规划会话）。**真实模型只跑过 1 次（四问）与 2 轮（「找」）** |
| E-37 / 2026-09-17 | **T14：提案两条后端路由 + 两个正式页**。`pytest -q` 216 passed、冒烟 9 步；lint/tsc exit=0 | `test_proposals.py` 可复跑 | 通过：只列 pending；裁定落业务终态并补 `decided_at`；缺理由 400、已裁定 409、不存在 404 | `proposals.py`、那两条路由与请求模型、两个页面、`api.ts` 两个函数变更后失效。**只验假上游**；**走查归用户** |
| E-38 / 2026-09-17 | **T23 三级结构与交付物验收**（决策 30–32）：`plan_node.level` 加 `task`、新表 `deliverable_submission`、打勾 / 跳过 / 交交付物三条路由。`pytest -q` 229 passed、lint/tsc exit=0 | `test_task_layer.py` 可复跑 | 通过：四组合判定符合规则；跳过算完成且理由进台账；交付物只对阶段、重提交留痕；打勾/跳过只对任务 | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-39 / 2026-09-18 | **T24 多计划与严格分开**（决策 33–34）：`plan_id` 加列、三条计划路由。`pytest -q` 244 passed、冒烟 10 步、lint/tsc 0 | `test_plans.py` 可复跑 | 通过：默认列表只给进行中；采纳落归属计划、无归属不指明 409；同计划上一轮未裁定的候选过期、已裁定的不动。**浏览器走查归用户** |
| E-40 / E-41 / E-52（2026-09-18 / 09-26） | **T25「找」+ T26 对话式规划与蓝图**：`pytest -q` 274→279、E-52 447 passed；冒烟 10 步、lint/tsc 通过 | `test_candidates.py`、`test_blueprint.py` 可复跑 | 通过：已裁定反馈进「找」，未采纳不能规划；蓝图版本与勾选生效、同名阶段复用、冲突预拦；E-52 真机不重问已答、候选贴原话 | 失效条件见第 1 节 T25/T26；T26 只验假上游 |
| E-42 / E-44 / E-45（2026-09-18） | **T27 计划四态 · T29 提案瘦身与页面分家 · T30 节点字段写入口**（决策 28–30 / 33 / 38）：各自 `pytest -q` 294→312 passed、冒烟 10–11 步、lint/tsc 0 | `test_plans.py` / `test_proposals.py` / `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：暂停幂等、收尾/作废不可暂停、作废是单向门；阶段收尾不再产提案、落后只出提醒、老类型批准被拒；改字段留改前改后且 id 不变、清日期、给任务设交付物被拒 | 失效条件见第 1 节对应行。**走查归用户** |
| E-46 / 2026-09-18 | **T31 计划对话的「一条可执行建议」**（决策 39）：`pytest -q` 332 passed、冒烟 11 步、lint/tsc exit=0 | `test_dialogue.py` 可复跑 | 通过：建议一次最多一条、坏建议一条都不落、批准后 **id 不变**且留流水、加东西按序建节点、超 5 件与批内重名都拒、计划离开进行中时批准被拒 | 失效条件见第 1 节同名行。**真实模型只被他走查跑过** |
| E-47 / 2026-09-20 | **P3.6 Agent 核心（T32/T33）**（决策 40）：`pytest -q` 354 passed、冒烟 11 步、lint/tsc exit=0 | `test_agent.py` / `test_dialogue.py` 可复跑 | 通过：目录=注册表、四个工具只读、跨计划参数被拒、两条上限闸、撞上限如实交代且**一条提案都不落**、三种结局都留一行 `agent_run` | 失效条件见第 1 节 Agent 那行。**只验假上游** |
| E-48 / 2026-09-20 | **P3.7：T34 路径形状 + T35 阶段跳过 + T36 追问槽收口**（决策 41）。`pytest -q` 385 passed（+31）、冒烟 11 步、lint/tsc 0；真库补两列 | `test_candidates.py` / `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：`path` 只落一行伞候选、形状与条数对不上判不合格重试、步骤名不进禁区、采纳回执带 steps；阶段跳过留痕 + 视同完成 + 落后不算；追问只问事实、已答的不再问 | 失效条件见第 1 节 T34–T36 那行。**只验假上游** |
| E-49 / 2026-09-21 | **T37 采纳落点留得住**（决策 33 ② 补的洞）：`pytest -q` **392 passed**（+7）、冒烟 11 步、lint/tsc exit=0；真库已补列 | `test_candidates.py` / `test_blueprint.py` 可复跑 | 通过：采纳后重新取行仍定得下来、换计划要拒、落点计划被收尾后报得清楚、否决不留落点 | 失效条件见第 1 节 T37 那行。**只验假上游**；真库里候选 #22 是修之前的，不受益 |
| E-50/E-51/E-53/E-55（2026-09-21–26） | 记忆系统的扫描/裁定/彻底删除与走查整改；`/proposals` 与工作台四件套先后重做：`pytest -q` 429→452 passed、冒烟 17 步、lint/tsc 0 | `test_memory` / `test_agent` / `test_dialogue` 可复跑 | 通过：日期优先、续期不增行、散文兜底、中文工具别名、记忆变化摘要；追问落库并渲染成卡 | memory / agent_tools / dialogue 与工作台组件的这些行为变更需复验；真实模型走查归用户 |
| E-63 / 2026-09-28 | 双入口整改：蓝图就绪、工作台意图闸、找方向逐轮历史/形态裁定/占用与 redo；`pytest -q` 528 passed、冒烟 17 步、lint/tsc 0；真库补 4 列 | `test_candidates` / `test_dialogue` / `test_blueprint` / `test_agent` 可复跑 | 通过：只报近况先澄清、蓝图未就绪拦截、刷新恢复轮次与形态决定、同线程历史进模型 | 后端契约变更后失效；真实模型措辞与桌面观感待用户 |
| E-64＋E-65 / 2026-09-29 | 四旧页重做（评判/档案/服务商/报告）＋工作台 PC 右栏重排；lint 0 错（9 旧警告）、tsc、detector `[]` | 源码；未跑浏览器/真实库 | 仅静态验证；桌面走查归用户；工作台部分已被 E-73 取代 | 四页、`judgment-view.tsx` 或外壳变更后失效 |
| E-66＋E-72 / 2026-09-29–30 | 候选页 PC：左目录＋右单条评审，路径横向轨迹；E-72 补找方向首搜进度、档案提示与候选轮回执；lint/tsc/detect 通过 | 当前源码；未跑浏览器、未写真库 | 仅静态验证；两种形态、历史切换、首搜反馈与裁定待走查；候选页及候选卡展示变更后失效 |
| E-67 / 2026-09-29 | P4 触达（T15–T17）：通知/导出/每周 job、`/api/notify`、设置页；`pytest -q` 566 passed、隔离冒烟 20 步、lint/tsc、detect `[]` | `test_notify` / `test_export` / `test_weekly` 可复跑 | 通过：补发/降频与周编号、空实现、只读导出、邮箱闸有覆盖 | 真邮件未发、Windows 任务计划未接、桌面待走查；通知/导出/job、路由与设置表变更后失效 |
| E-70 / 2026-09-30 | 索引/阅读；档案原文、记忆对照、计划字段/任务、资料四问分型；lint 0 错（9 旧警告）、tsc、`git diff --check` 通过 | 源码；未跑浏览器/真实库 | 静态通过；PC 观感与裁定待用户走查 | 提案页/正文变更后失效；不覆盖后端与 E-69＋E-71 |
| E-69＋E-71 / 2026-09-29–30 | 蓝图标准／增强模式（E-69）及其 09-30 多审查员化（E-71：水平/结构两位审查员逐条表态＋系统防冲突补位）；后端全量 `pytest -q` 577 passed（蓝图专项 55）、冒烟 20 步；前端 lint 0 错（9 旧警告）、tsc、detector `[]` | `test_blueprint.py` 等可复跑 | 通过：标准兼容、两审查员互不见结论、赞同写认可点、反对必带调整、修订回执逐条对账、失败不落提案；真实模型及浏览器未验证 | `blueprint.generate_blueprint`、审查 payload 契约、蓝图请求契约、审查席/模式切换组件变更后失效 |
| E-73～E-76 / 2026-10-01–02 | **成果闭环 P1**（契约/证据/收尾/升级＋/new 接线；复核 8 项）与 **P2**（会话、蓝图 v2、原子批准；后端 6 项、前端 5 项；提案页补回规划入口）。后端 pytest 679、冒烟 29 步；前端 lint 0 错、tsc 0 | 专项测试可复跑 | 通过：必需条件、改标准失效、批准回滚、legacy 闸；v2 可退回同会话 | 契约/会话/蓝图 v2 函数、候选/提案组件或新表变更后失效；**桌面走查归用户** |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库；加表/加列都走它
.\.venv\Scripts\python.exe -m pytest -q           # 全绿
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe -c "import pathlib,tempfile; from tools import smoke_p1; smoke_p1.TEMP_DB=pathlib.Path(tempfile.mkdtemp(prefix='cadence-merge-smoke-'))/'smoke.db'; raise SystemExit(smoke_p1.main())"
.\.venv\Scripts\python.exe tools\dev_server.py    # 开发热重启（**勿用 `uvicorn --reload`**，本机必有日志 Reloading 后卡死，原因见下）
.\.venv\Scripts\python.exe -m app.jobs.weekly_checkpoint [--dry-run]   # 周检查点：干跑只打印三问；真跑＝发信（没配 SMTP 为空实现）→ 导出四个文件 → 记忆周扫描（`--no-memory` 跳过）

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（用 localhost；已在源 next.config 的 allowedDevOrigins 白名单）
                   # 现有页面：/workbench · / · /new · /report · /candidates · /judge · /proposals · /memory · /providers · /profile
                   # 外壳唯一挂在 app/layout.tsx；(app)/layout.tsx 仅路由分组；/memory 只在 (app)/memory/page.tsx
npm run lint; npx tsc --noEmit  # 静态检查（typegen/build 需要时再跑）
```

**端口与服务**：后端 8000、前端 3000。停服务按端口用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端改 3000）找 PID 再 `Stop-Process`；后端 worker 名字不含 uvicorn，别只杀父进程。**热重启只用 `tools\dev_server.py`**（本机 Python 3.14 下 `uvicorn --reload` 首次保存必卡在 `Reloading...`）。全新克隆先装后端依赖；前端 `npm ci` + `build` 后再 `tsc`。

| 任务类型 | 必读文件 |
| --- | --- |
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **前端（写代码前必读）** | `frontend/AGENTS.md`（`next dev` 自动生成，提交它保持工作区干净）与 `frontend/node_modules/next/dist/docs/` 的 `upgrading/version-16.md`、`01-getting-started/06-fetching-data.md`——Next 16 有破坏性变更；再叠 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更与需求背景 | `docs/SPEC.md` 第 10、11、17、18 节（产品/架构）与第 1–9 节（需求背景）、`docs/U2-触达详解.md`（`docs/未决项讨论.md` 已被 SPEC 取代） |

## 7. 给下一个 Agent 的启动提示
1. 开工先完整读本文件并核对 Git 与未提交改动；只读当前目标相关源码与规则。P4 验收标准在 `tasks/todo.md` P4 一节，SMTP 授权码不是开工前提。
2. **探测一律指向临时库**，绝不拿 `data/cadence.db` 做实验（曾误建节点）；细则照 `backend\tools\smoke_p1.py` 与项目 `AGENTS.md`。
