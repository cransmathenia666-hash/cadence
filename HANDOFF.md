# cadence 交接文档

> 最近更新：2026-09-20（**P3.7 候选路径与「不要」的出口完工（T34–T36）**——385 passed、冒烟 11 步、lint/tsc exit=0；「找」多一个 `path` 形状（一条路＝1 条伞候选 + 2–8 个步骤，整条裁定，步骤**永不进禁区**）、阶段也能跳过、追问槽补了输入框且问过的不再问；真库已 `db.init` 补 `candidate.payload` 与 `learning_request.clarify` 两列。此前：P3.6 受控工具循环）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜**当前在分支 `feat/frontend-a-shell`**（用户 2026-09-19 要求前端走分支、便于重做，`master` 停在 `e866d26`）｜**远端**：`origin` = GitHub 私有库 `cransmathenia666-hash/cadence`（2026-09-20 首次上传；默认分支就是这条 `feat/frontend-a-shell`，`master` 也一并推了）｜近期：T31 前端 `7d61b63`、P3.6 `d5f3793`、P3.7 `5d565ed`｜**未提交改动**：无
> 其他工作树：无
> 当前唯一目标：**P4 触达与导出（T15–T17）**——规格在 `tasks/todo.md` 的 P4 一节；T15 的邮件实现需要用户给 SMTP 授权码，可先做 `notify` 接口与空实现
> 下一条动作：① **用户走查 P3.7**（方案 `docs/候选路径与追问槽方案.md` 第「用户手工走查」五步：输入一个明确方向看清单是不是**一张卡**、采纳后对话区有底稿、蓝图不勾一步看它没进禁区、计划页跳过阶段看完成度与台账、追问条下的输入框）；② 待他定：真库里那两组近似重复的任务要不要用「跳过 + 理由」收尾（要写真库，须他点头）；③ **用户走查 P3.6** 剩下的四步（计划对话那条链**真库还没跑过**）；④ 报一个 P4 的短计划给用户点头

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
| --- | --- | --- | --- |
| 后端地基与规则层（P0 schema / ledger；P1 状态机 / 落后量 / 周检查点 T4–T6；建节点防重复 T19；台账语义归属 T20） | 通过 | E-19、E-38 | `ledger.py` / `plan.py` / `backend/` 代码与测试变更后失效 |
| 接口契约（入参校验 + 方案 C 错误形状 + CORS + 启动） | 通过 | E-19 | `main.py`、`config.py` 或接口契约变更后失效 |
| 端到端闭环（脚本可复跑 + 用户亲手走通） | 通过 | E-19；E-45（冒烟 11 步） | 脚本或既有路由变更后失效；计划类数据已于 2026-09-17 清库（见第 3 节），只有档案可复查 |
| `backend/tools/` 两个自查脚本 | 通过 | E-41（冒烟 10 步跑通同一闭环） | 脚本自身或`plan.py` 变更后失效 |
| LLM provider 管理与调用记账（T10） | 通过 | E-26、E-32 | `llm.py`、或那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 前端 P2 四页（`/`、`/new`、`/report`；T7–T9）与 provider 管理页（T11） | 通过 | 各轮 lint/tsc exit=0（可随时复跑）＋用户走查 | 相应页面行为、`lib/api.ts` 对应函数或 `Provider*` 类型变更后失效 |
| 四问判断链路（T12） | 通过 | E-26、E-27（真实模型一次） | `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 变更后失效 |
| 候选清单页（T14`/candidates`；2026-09-20 起落点可就地新建计划；**追问槽有输入框、路径候选一张卡**——他当日定夺走「加输入框」那版，别删） | 通过 | E-37 | `app/candidates/page.tsx` 的行为、或 `listCandidates` / `findCandidates` / `verdictCandidate` 变更后失效；**走查归用户** |
| 提案裁定（T14`/proposals` + 提案两条后端路由） | 通过 | E-37 | `backend/app/proposals.py`、那两条路由与请求模型、`app/proposals/page.tsx`、`lib/api.ts` 的 `listProposals` / `decideProposal` 变更后失效；走查归用户 |
| 「找」候选清单与去重（T13） | 通过 | E-31/E-32（假上游）、E-33/E-34/E-36（真实模型两次 + 采纳落阶段） | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效 |
| 「找」加宽（T25：反馈流水 + 追问槽位；**T36 已修订追问口径**） | 通过 | E-40/E-41 | `advisor._feedback_block`、`find.py` 的 prompt 段与形状行、`/api/requests` 响应那两个新字段变更后失效；**真实模型效果未验** |
| 档案录入（T22） | 通过 | E-29/E-30 | 那三条 profile 写路由与请求模型、`advisor.PROFILE_CATEGORIES` 变更后失效 |
| 采纳自动落阶段（2026-09-17 用户拍板） | 通过 | E-33/E-34/E-36 | `decide_candidate`、verdict 路由或 `VerdictResult` 变更后失效 |
| 多计划与严格分开（T24） | 通过 | E-39 | `plan.list_plans`/`close_plan`/`void_plan`、`db._ADDED_COLUMNS` 那条加列、`advisor` 的归属与过期逻辑、`find.py` 的计划上下文段、那三条计划路由、计划切换器与 `/candidates` 页面变更后失效；**走查归用户** |
| 三级结构：任务层 + 交付物验收（T23） | 通过 | E-38 | `plan.py` 的判定与三个动作（`stage_completion` / `stage_finished` / `check_task` / `skip_task` / `submit_deliverable`）、`main.py` 那三条动作路由、`deliverable_submission` 表、计划表组件与 `/new` 页变更后失效；**浏览器走查归用户** |
| 计划生命周期四态与历史计划出口（T27） | 通过 | E-42 | `plan.pause_plan` / `reopen_plan` / `list_plans` 的 `ended_*` 两字段、`proposals.decide` 的收尾判据、两条新路由、首页计划管理区与「历史计划」段、`api.ts` 的 `pausePlan` / `reopenPlan` 变更后失效；**浏览器走查归用户** |
| 计划级对话（T28：蓝图落地后接着聊 + 档案变更提案能真写档案） | 通过 | E-47 | `dialogue.py`（2026-09-20 起资料改按需读，见下一行）、`profile.py` 的写入规则、`proposals.decide` 的 profile_change 分支、`plan_dialogue` 表、`components/plan-dialogue.tsx`、`/proposals` 的档案变更渲染变更后失效；**真实模型未跑，走查归用户** |
| 提案瘦身与页面分家（T29）＋节点字段写入口（T30） | 通过 | E-44、E-45 | `plan.update_node_fields` 与 `/fields` 路由、`proposals.decide`、`plan_tree` 的 `behind_reason`/`advice`、`/judge` 页、`/proposals` 的三类分流与蓝图勾选、`blueprint.apply_build` 的交付物回写、`plan-tree.tsx` 的改字段入口、`/api/judgments` 变更后失效；**页面与「改字段」的走查归用户** |
| 计划对话的「一条可执行建议」（T31；2026-09-19 走查后放宽成一次可带 1–5 件任务） | 通过 | E-46 | `plan_change.py`、`dialogue.say` 的信封与提示词、`_land_suggestion`、`proposals.decide` 的 `plan_change` 分支、`plan-dialogue.tsx` 确认条与 `/proposals` 渲染变更后失效；**真实模型已跑过（他走查时用的就是它），批量那版走查归用户** |
| Agent 的资料读取：受控工具循环（P3.6，T32/T33 全完工） | 通过 | E-47 | `backend/app/agent_runtime.py`、`agent_tools.py`、`dialogue.say` 的循环与 `agent_run`、`plan-dialogue.tsx` 的「本轮依据」变更后失效；**真实模型未跑，走查归用户** |
| 候选路径形状 · 阶段跳过 · 追问槽收口（P3.7，T34–T36） | 通过 | E-48 | `advisor` 的 `FoundList` / `_shape_problem` / `_shape_and_steps`、`find.py` 的 prompt 段、`plan.skip_node`、`advisor.pending_clarify` / `record_clarify`、`candidate.payload` 与 `learning_request.clarify` 两列、`/api/requests` 的 `clarify_answer`、`candidates/page.tsx` 与 `plan-tree.tsx` 变更后失效；**真实模型未跑，走查归用户** |
| 对话式规划与蓝图（T26） | 通过 | E-40/E-41 | `backend/app/blueprint.py`、`proposals.decide` 的蓝图分支与 `ProposalDecideIn.selected`、`blueprint.resolve_plan` 的归属解析、`plan_chat` 表、`/candidates` 对话区与 `/proposals` 的蓝图渲染变更后失效；**真实模型未跑，走查归用户** |
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 的后端部分由 E-19 与 E-38 覆盖 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：P4 触达与导出（T15–T17）。**

**已完成**：P1–P3.7 全部落地（T4–T36 前后端全完工，含前端 A 档样式、agent 工具循环与候选路径形状）——逐题范围见 `tasks/todo.md`。

**下一步**：P4 的 T15 `notify` 接口与邮件实现、T16 周检查点 job、T17 Markdown 导出。

## 3. 当前开放问题

无阻塞。**待用户亲手做的事**：见「下一条动作」①②③，外加有空时独立复核 `pytest -q` 与 `tools\smoke_p1.py`（预期值见第 6 节注释）。已回报：P2 四页、`/providers`、真实 provider 与模型（E-27）、T13 清单与去重、采纳自动落阶段、T28/T31 那段对话走查。

候选队列（不影响当前目标）——**一个仍有的事**：`start_reason` 未落库（加列 = Ask first），重看旧候选看不到依据，要依据得重问一轮。

- **真实库现状**：计划类数据曾于 2026-09-17 按用户指示**物理清库**（`tools/wipe_plan_data.py`，备份 `.bak-20260917-234923` 可回退），此后他走查又建了真实计划。**别拿清库时那批旧数字对账**；此后几轮都是 `db.init` 补表补列（最近一次 2026-09-20 补 `candidate.payload` 与 `learning_request.clarify`）。T31 的 `plan_change` 提案已落过真库（含两组近似重复任务）；**计划对话那条链（P3.6）真库还没跑过**。
- **两个已知边界**：① 去重只做「去空白 + 转小写」——换个说法的同一件事仍可能被当新候选；② 长输出慢（E-31/E-32/E-34）：候选清单 23–27 秒 / 约 5000 token，最坏一次 `find` 约 6 分钟；提速得压 prompt，未做。
- **口径与约定**：① 候选与提案别混——候选在 `/candidates` 裁定、提案在 `/proposals`（四类：蓝图 / 档案变更 / 资料判断 / 计划改动）、判资料在 `/judge`；② 档案类别只收五个约定令牌（表外 422）、取代/作废必填理由、同类别同文本回 409；③ 四问输出没给 id 又不说「依据不足」→ 一律判不合格；④ 档案 #4/#5 是修复前留下的重复，**未清**；⑤ **`/report` 只列「最新 active 计划」的节点**；⑥ 两种对话别混（`/candidates` 那段绑候选、6 轮、出树；计划页那块绑计划、不限轮数、能提建议＋提炼档案）；⑦ **落后不再产提案**——计划页显示一句提醒 + 三个建议；⑧ **节点字段能原地改**（T30：标题/交付物/截止日，理由必填、id 不变）；⑨ **T31 的聊天建议**走同一条写入口，但**它自己写不动**——一轮最多一条，落 `plan_change` 提案，计划页点「确认」才真改（见决策 39）；⑩ **「不要了」有三层出口**（T34/T35）：方向层否决＝永久禁区、蓝图不勾＝本版不建、计划里跳过（阶段也能跳）＝留一句理由；**步骤永不进禁区**（决策 41）。
- **样式与零散口径**：前端共用外壳（`globals.css` + `app-header.tsx`）已落地。零散口径：`POST /api/report` 无防重复（正解是前端禁用）；框架生成的 `404`/`405` 文案仍是英文；provider 的「地址/模型」清不成空（留空 = 不改）；`ledger.fetch_active` 取的是「初始业务状态」（名字误导、行为没错）；前端 effect 里同步 setState 会被 `react-hooks/set-state-in-effect` 拦（取数写成 `.then` 回调）。**远景**：SPEC 第 17 节第 3 条「档案自动提炼」——T28 已给出它的第一个生产者。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——节点 / 候选 / 提案都有，`plan` 没有（`closed` 是完成，不是否决），所以 `plan` 的 `void` 由台账写（T27 起）。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、各种判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422` / 与现状冲突 `409` / 业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- **P2 取数架构：浏览器直连**（决策 26）。前端用客户端组件，`lib/api.ts` 在浏览器里 fetch 后端，因此受 CORS 名单约束。不采用 Next 16 主推的服务端取数 + Server Action 路线，理由与代价见 SPEC 第 10 节。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端；LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 41 条见 `docs/SPEC.md` 第 18 节，除用户明确要求不重新讨论；台账拆列（方案 A）只在 SPEC 第 17 节第 2 条那三个条件满足时才重开。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。
- 本地库 `data/cadence.db` 存着用户真实档案与手工验收痕迹（现状见第 3 节）——**不得清库**。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
| --- | --- | --- | --- | --- |
| E-19 / 2026-09-15 | CORS 加固：`pytest -q`、冒烟 9 步、临时库发 10 组真实请求 | 临时脚本与库已删 | 通过：合法来源放行、非法来源不放行（`422` 也带 `allow-origin`）；所有错误出口键集恒为 `{detail, errors}`、`detail` 恒为字符串 | CORS 名单、两个错误处理器或既有路由响应形状变更后失效；**新增互不相关路由、只改 tests 不影响** |
| E-26 / E-27 / 2026-09-16 | T12 四问链路：`pytest -q`（160 passed）、冒烟 9 步；随后**真实模型端到端跑通一次**（5.5 秒，落 `proposal #3`） | `test_advisor.py` 可复跑 | 通过：合格输出落 pending 且引用 id 真实；**没给 id 又不说「依据不足」判不合格**；两次不合格抛错不落提案；无档案不调模型（同时得证真密钥可用） | `advisor.py`、那两条路由、`RequestIn`、provider id=3 配置任一变更后失效。**真实输出质量只跑过 1 次** |
| E-29 / E-30 / 2026-09-16 | T22 档案录入与防重复：`pytest -q`（177 → 181）、冒烟 9 步 | `test_profile_write.py` 可复跑 | 通过：五令牌外 422；取代/作废留痕；历史行再动 409；同类别同文本回 409 并指明已有 id；作废后重填不挡 | 那三条写路由与请求模型、`PROFILE_CATEGORIES`、判重逻辑变更后失效 |
| E-31 / E-32 / 2026-09-16 | T13 候选链路 + 一次超时修复（走查踩到 30 秒读超时，改 `CHAT_TIMEOUT_SECONDS = 180`）：`pytest -q`（199 → 201）、冒烟 9 步 | `test_candidates.py` / `test_llm.py` 可复跑 | 通过：条数越界与禁区命中判不合格并带原因重试、两次不合格不落一条；`rank` 即顺序；否决缺理由报错、已裁定 409、不存在 404；失败调用仍记账 | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`llm.py` 超时常量变更后失效。**只验假上游** |
| E-33 / E-34 / E-36 / 2026-09-17 | **真实模型「找」两次 + 采纳自动落阶段**：请求 3 落候选 #1–#5（否决 #1/#5）、请求 7 落 #6–#10 且**禁区标题一条没出现**；`llm_call #12` 26.7 秒 / 4975 token。`pytest -q` 204 passed、冒烟 9 步 | 用户库 `candidate` / `llm_call`、`test_candidates.py` 可复跑 | 通过：3–5 条带排序由真 provider 跑通、否决过的不再出现；采纳 → 候选 `accepted` 且计划里出现同名阶段，无归属/撞同名未收尾 → 409 且保持 `proposed`、不建节点 | 证的是禁区与采纳落点（`_rejected_titles`、prompt 禁区段、`decide_candidate`、verdict 路由）。**未覆盖**：换说法的同一件事是否漏过；样本仅两轮 |
| E-37 / 2026-09-17 | **T14：提案两条后端路由 + 两个正式页（`/ask` 退场）**。后端 `pytest -q` 216 passed、冒烟 9 步；前端 lint/tsc exit=0、三页均 200 | `test_proposals.py` 可复跑 | 通过：只列 pending、payload 解成对象；裁定落业务终态并补 `decided_at`；驳回缺理由 400、已裁定 409、不存在 404 | `proposals.py`、那两条路由与请求模型、两个页面、`api.ts` 两个函数变更后失效。**只验假上游**；**走查归用户** |
| E-38 / 2026-09-17 | **T23 三级结构与交付物验收**（决策 30–32）：`plan_node.level` 加 `task`、新表 `deliverable_submission`、打勾 / 跳过 / 交交付物三条路由。`pytest -q` 229 passed、冒烟 10 步全绿、lint/tsc exit=0 | `test_task_layer.py` 可复跑 | 通过：四组合判定符合规则；跳过算完成且理由进台账；交付物只对阶段、重提交留痕；打勾/跳过只对任务（层级不符 400、不存在 404） | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-39 / 2026-09-18 | **T24 多计划与严格分开**（决策 33–34）：`learning_request.plan_id` 加列、`list_plans` / `close_plan` / `void_plan` + 三条路由、归属进 prompt 与采纳落点。`pytest -q` 244 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：默认列表只给进行中；采纳落归属计划、无归属不指明 409；同计划上一轮未裁定的候选过期、已裁定的不动 | 失效条件见第 1 节同名行。**浏览器走查归用户** |
| E-40 / E-41 / 2026-09-18 | **T25「找」加宽 + T26 对话式规划与蓝图**（决策 35 / 36）：反馈流水进 prompt + 追问槽位；新表 `plan_chat`（6 轮）、蓝图（树 = 版本）、`selected` 勾选建树。`pytest -q` 274 → 279 passed、冒烟 10 步、lint/tsc exit=0 | `test_candidates.py`、`test_blueprint.py` 可复跑 | 通过：流水只取 `search`/只放已裁定；未采纳不能聊、6 轮封顶、历史从最早截断；蓝图落 `pending`、新版标旧版 `superseded`、只建勾中的、同名阶段复用、三类冲突在建之前拦下 | `find.py` 的 prompt 段、`blueprint.py`、`proposals.decide` 的蓝图分支、`plan_chat` 表变更后失效。**只验假上游**；追问口径已按 E-48 修订 |
| E-42 / 2026-09-18 | **T27 计划生命周期四态**（决策 33）：`pause_plan` / `reopen_plan` + 两条路由、`list_plans` 的 `ended_*`。`pytest -q` 294 passed、冒烟 10 步、lint/tsc exit=0 | `test_plans.py` 可复跑 | 通过：暂停理由留痕、重复暂停幂等、对收尾/作废的暂停回 400；重开把 `paused`/`closed` 放回进行中、作废回 400（单向门）；`ended_*` 反映最后一次结束 | 失效条件见第 1 节同名行。**走查归用户**（界面「暂停」只能用默认理由） |
| E-44 / 2026-09-18 | **T29 提案瘦身与页面分家**（走查后拍板；决策 28/29/30 修订）：删两个规则产提案的生产者、`decide` 拒绝老类型、`plan_tree` 的落后提醒两字段、新页 `/judge`、`/proposals` 装三类。`pytest -q` 301 passed、冒烟 10 步、lint/tsc exit=0 | `test_proposals.py` / `test_progress.py` / `test_plan.py` 可复跑 | 通过：阶段收尾不再产提案（判定仍在）、落后只出提醒、老类型批准被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-45 / 2026-09-18 | **T30 节点字段写入口**（决策 38）：`plan.update_node_fields` = 原地 `UPDATE` + 一条 `update_fields` 流水，**id 不变**；路由 `/api/plan/nodes/{id}/fields`；入口在 `plan-tree.tsx`。`pytest -q` 312 passed、冒烟 11 步全绿、lint/tsc exit=0 | `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：id 与引用不断、流水留改前改后、挪截止日带动落后量、清日期、缺理由/无改动/非法日期/给任务设交付物一律拒、改标题撞同名被拒 | 失效条件见第 1 节同名行。**走查归用户** |
| E-46 / 2026-09-18 | **T31 计划对话的「一条可执行建议」**（决策 39）：新模块 `plan_change.py`、`dialogue.say` 改信封输出、建议落 `kind=plan_change` 提案、`proposals.decide` 增第四类；前端确认条与第四类渲染。**2026-09-19 走查后放宽**：`tasks` 收 1–5 件、加阶段连任务一起建、回执改 `added.nodes`。`pytest -q` 332 passed、冒烟 11 步、lint/tsc exit=0 | `test_dialogue.py` 可复跑 | 通过：建议一次最多一条、坏建议**一条都不落**、批准后 **id 不变**且留流水、加东西按序建节点、超 5 件与批内重名都拒、**忽略什么都没写**、计划离开进行中时批准被拒而提案保持 pending | 失效条件见第 1 节同名行。**真实模型只被他走查跑过** |
| E-47 / 2026-09-20 | **P3.6 Agent 核心（T32/T33）+ 计划对话全链路复跑**（决策 40）：新模块 `agent_runtime` / `agent_tools`、四个只读工具、受控循环（3 次模型 + 6 次工具）、新表 `agent_run`、计划页「本轮依据」。`pytest -q` 354 passed、冒烟 11 步、lint/tsc exit=0 | `test_agent.py` / `test_dialogue.py` 可复跑 | 通过：目录=注册表、四个工具只读、跨计划参数被拒、非法工具不判死这一轮、两条上限闸、撞上限如实说读了什么还缺什么且**一条提案都不落**、三种结局都留一行 `agent_run` | 失效条件见第 1 节 Agent 那行。**只验假上游**：真实模型未跑，走查归用户 |
| E-48 / 2026-09-20 | **P3.7：T34 候选的路径形状 + T35 阶段跳过 + T36 追问槽收口**（决策 41，方案见 `docs/候选路径与追问槽方案.md`）。`pytest -q` **385 passed**（+31）、冒烟 11 步、lint/tsc exit=0。真库已 `db.init` 补两列（`candidate.payload`、`learning_request.clarify`），15 条老候选 payload 全 NULL | `test_candidates.py` / `test_task_layer.py` / `test_blueprint.py` 可复跑 | 通过：`path` 只落一行伞候选且步骤进 payload、形状与条数对不上（path 候选≠1 / 步骤不在 2–8 / 缺 why / directions 却给 steps / 缺 shape）判不合格重试、**步骤名不进禁区**、采纳回执带 steps 且建出伞阶段、老候选按 directions 渲染、过期按伞候选走、步骤草案进规划对话上下文；阶段跳过留痕 + 视同完成 + 落后不算它 + 周打卡不给跳；追问只问「关于我的一件事」（规划类 / 超长 / 多问号 / 换行一律判不合格）、回答拼成「原问题 + 回答」、已答的进反馈流水不再问、无待答追问时当「补充」不吞 | 失效条件见第 1 节 T34–T36 那行。**只验假上游**：真实模型效果与界面走查归用户 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行；加表/加列都走它）
.\.venv\Scripts\python.exe -m pytest -q           # 全绿（P3.7 落地时 385）
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 11 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
                   # 现有页面：/ 计划表（对话区含建议确认条与「本轮依据」） · /new · /report · /candidates · /judge · /proposals（四类） · /providers · /profile
                   # 共用外壳：`app/globals.css` + `components/app-header.tsx`；无组件库
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000。**停服务别只杀父进程**：`--reload` 与 `next dev` 都会留子进程占端口；用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端换 3000）找 PID 再 `Stop-Process`。热加载只监听 Python（改 `sql/schema.sql` 不触发）。

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
