# cadence 交接文档

> 最近更新：2026-09-23（**公开仓库收口**：P3.9+P3.10 已提交；私人物件移出版本控制并忽略、全库历史重写清干净、仓库转**公开**。此前 2026-09-21：P3.10 记忆走查整改（T42–T45），`pytest -q` **443 passed**）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜**当前在分支 `feat/frontend-a-shell`**（`master` 刻意停在旧位置不追平）｜**远端**：`origin` = GitHub **公开**库 `cransmathenia666-hash/cadence`（2026-09-23 重写历史清掉私人物件后转公开，默认分支即此；重写前全量备份在 `D:\cadence-history-backup-20260923.bundle`）｜**未提交改动**：只剩并行窗口的前端样式改造（`globals.css` / `layout.tsx` / `app-header.tsx` / `package*.json` 与成批未跟踪的 `components/ui|workbench|primitives`、tokens 等）——**别提交、别清理**
> 其他工作树：无
> 当前唯一目标：**P4 触达与导出（T15–T17）**——规格在 `tasks/todo.md` 的 P4 一节；T15 的邮件实现需要用户给 SMTP 授权码，可先做 `notify` 接口与空实现
> 下一条动作：① **走查 P3.10**（记忆页挑一次复核日期看那行是否常驻、计划对话里让它 slips 一次看还有没有红条、「本轮依据」里不该再有英文令牌；第二批要接真模型：先聊一轮 → 去记忆页改一条 → 回对话看它是否主动重读）；② **走查 P3.7 + T37**（方案那五步；真库候选 #22 在下拉里选一次计划即记住）；③ 待他定：真库那两组近似重复任务要不要「跳过 + 理由」收尾（须他点头）；④ 报 P4 短计划给他点头

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
| --- | --- | --- | --- |
| 后端地基与规则层（P0 schema / ledger；P1 状态机 / 落后量 / 周检查点 T4–T6；建节点防重复 T19；台账语义归属 T20） | 通过 | E-19、E-38 | `ledger.py` / `plan.py` / `backend/` 代码与测试变更后失效 |
| 接口契约（入参校验 + 方案 C 错误形状 + CORS + 启动） | 通过 | E-19 | `main.py`、`config.py` 或接口契约变更后失效 |
| 端到端闭环与两个自查脚本（可复跑 + 用户亲手走通） | 通过 | E-19；E-51（冒烟 17 步） | 脚本、既有路由或 `backend/app/plan.py` 变更后失效；计划类数据已于 2026-09-17 清库（见第 3 节），只有档案可复查 |
| LLM provider 管理与调用记账（T10） | 通过 | E-26、E-32 | `llm.py`、或那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 前端 P2 四页（`/`、`/new`、`/report`；T7–T9）与 provider 管理页（T11） | 通过 | 各轮 lint/tsc exit=0（可随时复跑）＋用户走查 | 相应页面行为、`lib/api.ts` 对应函数或 `Provider*` 类型变更后失效 |
| 四问判断链路（T12） | 通过 | E-26、E-27（真实模型一次） | `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 变更后失效 |
| 候选清单页（T14`/candidates`；2026-09-20 起落点可就地新建计划；**追问槽有输入框、路径候选一张卡**——别删） | 通过 | E-37 | `app/candidates/page.tsx` 的行为、或 `listCandidates` / `findCandidates` / `verdictCandidate` 变更后失效；**走查归用户** |
| 提案裁定（T14`/proposals` + 提案两条后端路由） | 通过 | E-37 | `backend/app/proposals.py`、那两条路由与请求模型、`app/proposals/page.tsx`、`lib/api.ts` 的 `listProposals` / `decideProposal` 变更后失效；走查归用户 |
| 「找」候选清单与去重（T13） | 通过 | E-31/E-32（假上游）、E-33/E-34/E-36（真实模型两次 + 采纳落阶段） | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效 |
| 「找」加宽（T25：反馈流水 + 追问槽位；**T36 已修订追问口径**） | 通过 | E-40/E-41 | `advisor._feedback_block`、`find.py` 的 prompt 段与形状行、`/api/requests` 响应那两个新字段变更后失效；**真实模型效果未验** |
| 档案录入（T22） | 通过 | E-29/E-30 | 那三条 profile 写路由与请求模型、`advisor.PROFILE_CATEGORIES` 变更后失效 |
| 采纳自动落阶段（2026-09-17 用户拍板） | 通过 | E-33/E-34/E-36 | `decide_candidate`、verdict 路由或 `VerdictResult` 变更后失效 |
| 多计划与严格分开（T24） | 通过 | E-39 | `plan.list_plans`/`close_plan`/`void_plan`、`db._ADDED_COLUMNS` 那条加列、`advisor` 的归属与过期逻辑、`find.py` 的计划上下文段、那三条计划路由、计划切换器与 `/candidates` 页面变更后失效；**走查归用户** |
| 三级结构：任务层 + 交付物验收（T23） | 通过 | E-38 | `plan.py` 的判定与三个动作（`stage_completion` / `stage_finished` / `check_task` / `skip_task` / `submit_deliverable`）、`main.py` 那三条动作路由、`deliverable_submission` 表、计划表组件与 `/new` 页变更后失效；**浏览器走查归用户** |
| 计划生命周期四态与历史计划出口（T27） | 通过 | E-42 | `plan.pause_plan` / `reopen_plan` / `list_plans` 的 `ended_*` 两字段、`proposals.decide` 的收尾判据、两条新路由、首页计划管理区与「历史计划」段、`api.ts` 的 `pausePlan` / `reopenPlan` 变更后失效；**浏览器走查归用户** |
| 计划级对话（T28：蓝图落地后接着聊 + 档案变更提案能真写档案） | 通过 | E-47 | `dialogue.py`（资料按需读，见下一行）、`profile.py` 的写入规则、`proposals.decide` 的 profile_change 分支、`plan_dialogue` 表、`plan-dialogue.tsx` 变更后失效；**真实模型未跑，走查归用户** |
| 提案瘦身与页面分家（T29）＋节点字段写入口（T30） | 通过 | E-44、E-45 | `plan.update_node_fields` 与 `/fields` 路由、`proposals.decide`、`plan_tree` 的落后提醒两字段、`/judge` 与 `/proposals` 两类渲染、`plan-tree.tsx` 的改字段入口变更后失效；**页面走查归用户** |
| 计划对话的「一条可执行建议」（T31；2026-09-19 走查后放宽成一次可带 1–5 件任务） | 通过 | E-46 | `plan_change.py`、`dialogue.say` 的信封与提示词、`_land_suggestion`、`proposals.decide` 的 `plan_change` 分支、`plan-dialogue.tsx` 确认条与 `/proposals` 渲染变更后失效；**真实模型已跑过（他走查时用的就是它），批量那版走查归用户** |
| Agent 的资料读取：受控工具循环（P3.6，T32/T33 全完工；**2026-09-21 走查整改 T43/T44/T45 在这一层上加了三处**：回话散文兜底、工具类别收中文别名且报错说人话、每轮开头的记忆变化摘要） | 通过 | E-47、E-51 | `backend/app/agent_runtime.py`、`agent_tools.py`、`dialogue.py` 与 `dialogue.say` 的循环、`agent_run`、`plan-dialogue.tsx` 的「本轮依据」变更后失效；**真实模型未跑，走查归用户** |
| 候选路径形状 · 阶段跳过 · 追问槽收口（P3.7，T34–T36） | 通过 | E-48 | `advisor` 的 `FoundList` / `_shape_problem` / `_shape_and_steps`、`find.py` 的 prompt 段、`plan.skip_node`、`advisor.pending_clarify` / `record_clarify`、`candidate.payload` 与 `learning_request.clarify` 两列、`/api/requests` 的 `clarify_answer`、`candidates/page.tsx` 与 `plan-tree.tsx` 变更后失效；**真实模型未跑，走查归用户** |
| 采纳落点留得住（T37，2026-09-21 走查截图触发） | 通过 | E-49 | `advisor.landing_plan` 的优先序、`decide_candidate` 写落点那一条、`candidate.landing_plan_id` 列、`blueprint.resolve_plan`、`/api/candidates` 的 `landing_plan_id`、`candidates/page.tsx` 的落点优先序变更后失效；**只验假上游** |
| 对话式规划与蓝图（T26） | 通过 | E-40/E-41 | `blueprint.py`、`proposals.decide` 的蓝图分支与 `ProposalDecideIn.selected`、`blueprint.resolve_plan` 的归属解析、`plan_chat` 表、`/candidates` 对话区与 `/proposals` 的蓝图渲染变更后失效；**真实模型未跑，走查归用户** |
| 记忆系统：三层记忆 · 记忆候选 · 扫描 · 彻底删除（P3.9，T38–T41）＋**走查整改（T42–T45：复核日期可选、散文兜底、报错人话、记忆变化感知）** | 通过 | E-50、E-51 | `backend/app/memory.py`、`agent_tools` 的 `TOOLS`、`proposals.decide` 的 `memory_change` 分支、`/api/memory*` 那十一条路由、`frontend/app/memory/page.tsx` 与其 `api.ts` 那一段变更后失效；**真实模型没跑过，走查归用户** |
| E-50 / 2026-09-21 | **P3.9 记忆系统（T38–T41）**（决策 42，方案 `docs/记忆系统.md`）：`pytest -q` **429 passed**、冒烟 **17 步**、lint/tsc exit=0 | `test_memory.py` 可复跑 | 通过：计划之间互相读不到、跨计划动记忆判不合格；来源不存在 / 摘录对不上 / 完全重复 / 取代目标已失效判不合格而**近似重复只提示**；批量只收「新增 + 用户陈述」；到期的不进默认结果；扫描游标只推进处理过的部分、「没有候选」也算成功、失败不落一条；彻底删除把正文（含台账里的副本）都清掉，清不干净时如实列出残留 | 失效条件见第 1 节记忆系统那行。**只验假上游**；**真实模型跑扫描归用户走查** |
| E-51 / 2026-09-21 | **P3.10 记忆走查整改（T42–T45）**（决策 43，方案 `docs/记忆走查整改方案.md`）：`pytest -q` **443 passed**（+14）、冒烟 **17 步**（第 13 步带上所挑复核日期并核对回执）、lint/tsc exit=0；真库已 `db.init` 补 `memory_deletion.plan_id` | `test_memory.py` / `test_agent.py` / `test_dialogue.py` 可复跑 | 通过：显式日期优先于默认 90 天且续期不产生新行；散文只在**重试之后**被收下（第一次仍判不合格）、形状一错到底照样撞上限且一条不落、散文里写「我想先读计划」不算读资料；中文别名读到同一批档案与经历、报错只列中文名；记忆变过 → 摘要列对动作（含**走墓碑**的彻底删除），没变 / 从没读过 / 别的计划的计划内变化都不出现，摘要进下一轮开头且排在额度之后；契约段含硬规则 | 失效条件见第 1 节 Agent 与记忆系统两行。**只验假上游**；**「摘要出现那一轮它真读了记忆」留给用户真机走查** |
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 的后端部分由 E-19 与 E-38 覆盖 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：P4 触达与导出（T15–T17）。**

**已完成**：P1–P3.9 全部落地（T4–T41 前后端全完工）——逐题范围与实施记录见 `tasks/todo.md`。

**下一步**：P4 的 T15 `notify` 接口与邮件实现、T16 周检查点 job、T17 Markdown 导出。

## 3. 当前开放问题

无阻塞。**待用户亲手做的事**：见「下一条动作」①②③，外加有空时独立复核 `pytest -q` 与 `tools\smoke_p1.py`（预期值见第 6 节注释）。

候选队列（不影响当前目标）——**一个仍有的事**：`start_reason` 未落库（加列 = Ask first），重看旧候选看不到依据，要依据得重问一轮。**记忆系统那侧**：周扫描的定时入口要等 P4 的 T16 周任务接上（`memory.weekly_scans` 与 `trigger=weekly` 都已就绪，只差定时器）；真库现在的记忆与收件箱都是空的（老档案已标 `legacy_manual`，但一条记忆都没记过）。

- **真实库现状**：计划类数据曾于 2026-09-17 按用户指示**物理清库**（`tools/wipe_plan_data.py`，备份 `.bak-20260917-234923` 可回退），此后他走查又建了真实计划。**别拿清库时那批旧数字对账**；此后几轮都是 `db.init` 补表补列（最近一次 2026-09-21 补 `candidate.landing_plan_id`）。T31 的 `plan_change` 提案已落过真库（含两组近似重复任务）；4 条已采纳候选里 3 条的落点靠对话记录定得下来，**候选 #22 是 T37 修之前采纳的、落点为空，需要在界面上选一次计划**；**计划对话那条链（P3.6）真库还没跑过**。
- **两个已知边界**：① 去重只做「去空白 + 转小写」——换个说法的同一件事仍可能被当新候选；② 长输出慢（E-31/E-32/E-34）：候选清单 23–27 秒 / 约 5000 token，最坏一次 `find` 约 6 分钟；提速得压 prompt，未做。
- **口径与约定**：① 候选在 `/candidates` 裁定、提案在 `/proposals`（五类：蓝图 / 档案变更 / 资料判断 / 计划改动 / **记忆候选**）、判资料在 `/judge`、记忆在 `/memory`；② 档案类别只收五个约定令牌（表外 422）、取代与作废必填理由、同类别同文本回 409；③ 四问输出没给 id 又不说「依据不足」→ 一律判不合格；④ 档案 #4/#5 是修复前留下的重复，**未清**；⑤ **`/report` 只列「最新 active 计划」的节点**；⑥ 两种对话别混（`/candidates` 那段绑候选、6 轮、出树；计划页那块绑计划、不限轮数、能提建议＋提炼档案）；⑦ **记忆候选只有「新增 + 你明说的」能批量批准**（取代 / 推断 / 彻底删除逐条）；改与作废走台账，彻底删除走「预览 → 确认 → 全库复扫」。其余口径（落后提醒、原地改字段、聊天建议、三层「不要」出口）见 SPEC 决策 30/38/39/41。
- **样式与零散口径**：前端共用外壳（`globals.css` + `app-header.tsx`）已落地。零散口径：`POST /api/report` 无防重复（正解是前端禁用）；框架生成的 `404`/`405` 文案仍是英文；provider 的「地址/模型」清不成空（留空 = 不改）；`ledger.fetch_active` 取的是「初始业务状态」（名字误导、行为没错）；前端 effect 里同步 setState 会被 `react-hooks/set-state-in-effect` 拦（取数写成 `.then` 回调）；**数据库连接关掉了 sqlite3 的跨线程检查**（2026-09-21 修：同步依赖与同步接口不保证同一线程，不关会随机 500）。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——节点 / 候选 / 提案都有，`plan` 没有（`closed` 是完成，不是否决），所以 `plan` 的 `void` 由台账写（T27 起）。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、各种判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422` / 与现状冲突 `409` / 业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- **P2 取数架构：浏览器直连**（决策 26）。前端用客户端组件，`lib/api.ts` 在浏览器里 fetch 后端，因此受 CORS 名单约束。不采用 Next 16 主推的服务端取数 + Server Action 路线，理由与代价见 SPEC 第 10 节。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端；LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 41 条见 `docs/SPEC.md` 第 18 节，除用户明确要求不重新讨论；台账拆列（方案 A）只在 SPEC 第 17 节第 2 条那三个条件满足时才重开。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，**只留本机、已不入库**（公开仓库里没有它），不得删除或改写。
- 本地库 `data/cadence.db` 存着用户真实档案与手工验收痕迹（现状见第 3 节）——**不得清库**。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
| --- | --- | --- | --- | --- |
| E-19 / E-26 / E-27 / E-29 / E-30 / E-31 / E-32 / E-33 / E-34 / E-36（2026-09-15–17） | P0–P3 早期：CORS 与错误响应形状、四问链路（**真实模型端到端跑通一次**）、档案录入与防重复、候选链路与两次真实模型「找」＋采纳自动落阶段。**逐题验收与实施记录见 `tasks/todo.md`** | `test_advisor.py` / `test_profile_write.py` / `test_candidates.py` / `test_llm.py` 可复跑 | 通过：错误键集恒为 `{detail, errors}`；四问引用 id 真实、没给 id 又不说「依据不足」判不合格；档案五令牌外 422、同类别同文本 409；候选条数越界与禁区命中判不合格、两次不落一条；采纳落同名阶段、无归属或撞名 409 | 各自失效条件见第 1 节对应行。**真实模型只跑过 1 次（四问）与 2 轮（「找」）** |
| E-37 / 2026-09-17 | **T14：提案两条后端路由 + 两个正式页（`/ask` 退场）**。后端 `pytest -q` 216 passed、冒烟 9 步；前端 lint/tsc exit=0、三页均 200 | `test_proposals.py` 可复跑 | 通过：只列 pending、payload 解成对象；裁定落业务终态并补 `decided_at`；驳回缺理由 400、已裁定 409、不存在 404 | `proposals.py`、那两条路由与请求模型、两个页面、`api.ts` 两个函数变更后失效。**只验假上游**；**走查归用户** |
| E-38 / 2026-09-17 | **T23 三级结构与交付物验收**（决策 30–32）：`plan_node.level` 加 `task`、新表 `deliverable_submission`、打勾 / 跳过 / 交交付物三条路由。`pytest -q` 229 passed、冒烟 10 步全绿、lint/tsc exit=0 | `test_task_layer.py` 可复跑 | 通过：四组合判定符合规则；跳过算完成且理由进台账；交付物只对阶段、重提交留痕；打勾/跳过只对任务（层级不符 400、不存在 404） | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-39 / 2026-09-18 | **T24 多计划与严格分开**（决策 33–34）：`learning_request.plan_id` 加列、`list_plans` / `close_plan` / `void_plan` + 三条路由、归属进 prompt 与采纳落点。`pytest -q` 244 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：默认列表只给进行中；采纳落归属计划、无归属不指明 409；同计划上一轮未裁定的候选过期、已裁定的不动 | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-40 / E-41 / 2026-09-18 | **T25「找」加宽 + T26 对话式规划与蓝图**（决策 35 / 36）：反馈流水进 prompt + 追问槽位；新表 `plan_chat`（6 轮）、蓝图（树 = 版本）、`selected` 勾选建树。`pytest -q` 274 → 279 passed、冒烟 10 步、lint/tsc exit=0 | `test_candidates.py`、`test_blueprint.py` 可复跑 | 通过：流水只取 `search`、只放已裁定；未采纳不能聊、6 轮封顶；蓝图落 `pending`、新版标旧版 `superseded`、只建勾中的、同名阶段复用、三类冲突在建之前拦下 | 失效条件见第 1 节 T25/T26 两行。**只验假上游**；追问口径已按 E-48 修订 |
| E-42 / 2026-09-18 | **T27 计划生命周期四态**（决策 33）：`pytest -q` 294 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：暂停理由留痕、重复暂停幂等、对收尾/作废的暂停回 400；重开把 `paused` / `closed` 放回进行中、作废回 400（单向门） | 失效条件见第 1 节同名行。**走查归用户** |
| E-44 / 2026-09-18 | **T29 提案瘦身与页面分家**（走查后拍板；决策 28/29/30 修订）：`pytest -q` 301 passed、冒烟 10 步、lint/tsc exit=0 | `test_proposals.py` / `test_progress.py` / `test_plan.py` 可复跑 | 通过：阶段收尾不再产提案（判定仍在）、落后只出提醒、老类型批准被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-45 / 2026-09-18 | **T30 节点字段写入口**（决策 38）：`pytest -q` 312 passed、冒烟 11 步全绿、lint/tsc exit=0 | `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：id 与引用不断、流水留改前改后、挪截止日带动落后量、清日期、缺理由 / 无改动 / 非法日期 / 给任务设交付物一律拒、改标题撞同名被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-46 / 2026-09-18 | **T31 计划对话的「一条可执行建议」**（决策 39）：`pytest -q` 332 passed、冒烟 11 步、lint/tsc exit=0 | `test_dialogue.py` 可复跑 | 通过：建议一次最多一条、坏建议一条都不落、批准后 **id 不变**且留流水、加东西按序建节点、超 5 件与批内重名都拒、忽略什么都没写、计划离开进行中时批准被拒而提案保持 pending | 失效条件见第 1 节同名行。**真实模型只被他走查跑过** |
| E-47 / 2026-09-20 | **P3.6 Agent 核心（T32/T33）**（决策 40，机制见 SPEC 第 10 节第 6 条）：`pytest -q` 354 passed、冒烟 11 步、lint/tsc exit=0 | `test_agent.py` / `test_dialogue.py` 可复跑 | 通过：目录=注册表、四个工具只读、跨计划参数被拒、非法工具不判死这一轮、两条上限闸、撞上限如实说读了什么还缺什么且**一条提案都不落**、三种结局都留一行 `agent_run` | 失效条件见第 1 节 Agent 那行。**只验假上游**：计划对话那条链真库没跑过 |
| E-48 / 2026-09-20 | **P3.7：T34 路径形状 + T35 阶段跳过 + T36 追问槽收口**（决策 41，方案见 `docs/候选路径与追问槽方案.md`）。`pytest -q` 385 passed（+31）、冒烟 11 步、lint/tsc exit=0。真库已补 `candidate.payload` 与 `learning_request.clarify` 两列 | `test_candidates.py` / `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：`path` 只落一行伞候选、形状与条数对不上判不合格重试、**步骤名不进禁区**、采纳回执带 steps；阶段跳过留痕 + 视同完成 + 落后不算 + 周打卡不给跳；追问只问事实（规划类 / 超长 / 多问号 / 换行判不合格）、回答拼成「原问题 + 回答」、已答的不再问 | 失效条件见第 1 节 T34–T36 那行。**只验假上游** |
| E-49 / 2026-09-21 | **T37 采纳落点留得住**（决策 33 ② 补的洞；用户走查截图触发）：`pytest -q` **392 passed**（+7）、冒烟 11 步、lint/tsc exit=0；真库已补列 | `test_candidates.py` / `test_blueprint.py` 可复跑 | 通过：临时库**复现了缺口**（刷新后 `view.plan_id` 为空、直接聊报「没有计划归属」）；采纳后重新取行仍定得下来、换计划要拒、落点计划被收尾后报得清楚、否决不留落点 | 失效条件见第 1 节 T37 那行。**只验假上游**；真库里候选 #22 是修之前的，不受益 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行；加表/加列都走它）
.\.venv\Scripts\python.exe -m pytest -q           # 全绿（P3.10 落地时 443）
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 17 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
                   # 现有页面：/ 计划表 · /new · /report · /candidates · /judge · /proposals · /memory · /providers · /profile
                   # 共用外壳：`app/globals.css` + `components/app-header.tsx`；无组件库
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000。**停服务别只杀父进程**：`--reload` 与 `next dev` 都会留子进程占端口；用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端换 3000）找 PID 再 `Stop-Process`。热加载只监听 Python（改 `sql/schema.sql` 不触发）。**全新克隆验收（2026-09-23 公开前跑过）**：`pip install -r requirements.txt` → 443 passed；`npm ci` + `build` 之后再 `tsc` 才过——Next 16 的 `LayoutProps` 这类类型构建时生成了才有。

| 任务类型 | 必读文件 |
| --- | --- |
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **前端（写代码前必读）** | `frontend/AGENTS.md`（`next dev` 自动生成，提交它保持工作区干净）与 `frontend/node_modules/next/dist/docs/` 的 `upgrading/version-16.md`、`01-getting-started/06-fetching-data.md`——Next 16 相对训练数据有破坏性变更，凭记忆写会撞已换掉的 API；再叠 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更与需求背景 | `docs/SPEC.md` 第 10、11、17、18 节（产品/架构）与第 1–9 节（需求背景）、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git 与未提交改动；只读当前目标（**P4：T15–T17**）的源码与规则——验收在 `tasks/todo.md` 的 P4 一节；**SMTP 授权码不是开工前提**。
2. 证据能否沿用，看第 1 节的「证据依据」列与第 5 节的失效条件；没有触发条件不做全量复验。
3. **探测一律指向临时库**（照 `tools/smoke_p1.py`：临时库 + 空闲端口），绝不拿 `data/cadence.db` 做实验（曾误建 `plan_node #7`）。遵守项目 `AGENTS.md`。
4. **没有事实变化就不更新交接文档；要更新时只做定点编辑**，并按 `AGENTS.md` 跑校验脚本至 errors=0、warnings=0。
