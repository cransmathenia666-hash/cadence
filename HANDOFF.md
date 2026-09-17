# cadence 交接文档

> 最近更新：2026-09-18（T24 多计划完工；**T25/T26 的规格已钉死**——SPEC 决策 6/35/36 与 `tasks/todo.md` 的 T25/T26 都补了实现要点，新窗口照做即可，不需要再问设计）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜基线：后端 `fe4a5b8`、前端脚手架 `d8f9e3c`；近期：T12 `f08f75b`、T11+`/ask` `882cf69`、T13 `ad1bf18`/`585166f`、T22 `163f24e`/`b09bc34`、防重复 `af759fa`、超时放宽 `ce265ac`、采纳自动落阶段 `ff8a0d8`、T14 `d1fa80f`/`124634d`、规格落盘 `5a8af21`、清库工具 `3316714`、T23 `3a6854b`/`b18b732`、T24 `9f68eab`/`b6c20ec`、T25/T26 规格钉死 `dbc3ba4`｜无远端｜未提交改动：`AGENTS.md` 一行（另一会话）；`.dsh-vision-toolkit/` 与 `.zcode/` 未跟踪，非本项目产物
> 其他工作树：无
> 当前唯一目标：**T25「找」加宽 → T26 对话式规划**（规格见 SPEC 决策 35/36 与 `tasks/todo.md` 的 T25/T26「实现要点」，已无需再定设计）；P4 触达与导出在它们之后
> 下一条动作：① 新窗口直接开工 **T25**（规格见 SPEC 决策 35 与 todo T25 的实现要点）；② 用户走查三级界面与多计划仍未回报

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
|---|---|---|---|
| 后端 P0 地基（schema / ledger） | 通过 | E-19 | `ledger.py` 变更后失效 |
| P1 规则层（状态机 / 落后量 / 周检查点判定，T4–T6） | 通过 | E-19 | `backend/` 代码与测试变更后失效 |
| 建节点防重复（T19） | 通过 | E-19 | 同上 |
| 台账语义归属（T20，方案 B） | 通过 | E-19、E-05/E-08/E-13 | `ledger.py`、`plan.py` 变更后失效 |
| 接口契约（入参校验 + 方案 C 错误形状 + CORS + 启动） | 通过 | E-19 | `main.py`、`config.py` 或接口契约变更后失效 |
| 端到端闭环（脚本可复跑 + 用户亲手走通） | 通过 | E-19；E-05/E-08/E-13 | 脚本或既有路由变更后失效。**注意**：2026-09-17 计划类数据已物理清库，E-05/E-08/E-13 里「用户库数据可随时复查」如今只对档案成立 |
| `backend/tools/` 两个自查脚本 | 通过 | E-08 | 脚本自身或 `plan.py` 变更后失效 |
| LLM provider 管理与调用记账（T10） | 通过 | E-26 轮重跑覆盖（原 E-23 的 22 条 llm 单测 + 6 条 provider 接口测试） | `llm.py`、或那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 前端 P2 四页（`/`、`/new`、`/report`；T7–T9） | 通过 | E-21/E-22/E-24（行为与 lint/tsc）＋用户走查（E-28 轮回报） | 相应页面行为或 `lib/api.ts` 对应函数变更后失效；**样式未做** |
| provider 管理页（T11） | 通过 | E-21/E-22/E-24/E-28（lint/tsc）＋用户手工走查 2026-09-15 | `providers/page.tsx`、那 5 个 provider 函数与 `Provider*` 类型变更后失效；**样式未做** |
| 四问判断链路（T12） | 通过 | E-26、E-27（真实模型一次） | `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 变更后失效；真实输出质量只见过一次 |
| 候选清单页（T14 `/candidates`） | 通过 | E-37（前端只跑了 lint/tsc 与三页 HTTP 200） | `app/candidates/page.tsx` 的行为、或 `listCandidates` / `findCandidates` / `verdictCandidate` 变更后失效；**浏览器走查归用户** |
| 提案裁定（T14 `/proposals` + 提案两条后端路由） | 通过 | E-37（pytest 216、smoke 9 步、lint/tsc） | `backend/app/proposals.py`、那两条路由与请求模型、`app/proposals/page.tsx`、`lib/api.ts` 的 `listProposals` / `decideProposal` 变更后失效；真实数据走查归用户 |
| `/ask` 页两种形态 | 不适用 | 历史证据 E-27/E-28/E-31/E-36 | 2026-09-17 按 SPEC 决策 29 删除，已不在仓库：「找」归 `/candidates`、「判」归 `/proposals` |
| 「找」候选清单与去重（T13） | 通过 | E-31（假上游）、E-33/E-34（真实模型两次） | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效 |
| 档案录入（T22） | 通过 | E-29/E-30 | 那三条 profile 写路由与请求模型、`advisor.PROFILE_CATEGORIES` 变更后失效 |
| 采纳自动落阶段（2026-09-17 用户拍板） | 通过 | E-36 | `decide_candidate`、verdict 路由或 `VerdictResult` 变更后失效 |
| 多计划与严格分开（T24：计划列表/收尾/作废 + 候选归属 + 落点显式化 + 候选过期） | 通过 | E-39（pytest 244、smoke 10 步、lint/tsc） | `plan.list_plans`/`close_plan`/`void_plan`、`db._ADDED_COLUMNS` 那条加列、`advisor` 的归属与过期逻辑、`find.py` 的计划上下文段、那三条计划路由、计划切换器与 `/candidates` 页面变更后失效；**浏览器走查归用户** |
| 三级结构：任务层 + 交付物验收（T23） | 通过 | E-38（pytest 229、smoke 10 步、lint/tsc） | `plan.py` 的判定与三个动作（`stage_completion` / `stage_finished` / `check_task` / `skip_task` / `submit_deliverable`）、`main.py` 那三条动作路由、`deliverable_submission` 表、计划表组件与 `/new` 页变更后失效；**浏览器走查归用户** |
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 的后端部分由 E-19 与 E-38 覆盖 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：按 SPEC 决策 30–36 施工。** T23（三级结构）、T24（多计划）已完工并提交；下一个是 T25「找」加宽（决策 35），随后 T26 对话式规划（决策 36）与 P4。

**已完成（P3 + P3.5 前两项）**：T10 provider 管理与记账、T11 `/providers` 页、T12 四问判断、T13 候选清单与去重、T14 候选页与提案裁定页 + 提案两条路由、T22 档案三条写入口 + `/profile` 页、采纳自动建同名阶段（E-36）、**T23 三级结构**（`task` 层；阶段完成 = 任务全打勾/跳过 且 交付物已提交；打勾/跳过/交付物三个动作；新表 `deliverable_submission`）、**T24 多计划**（计划列表 + 收尾/作废门；候选带归属并把当前阶段带进 prompt；采纳落点显式；同计划上一轮未裁定候选自动过期）。

**本轮不做 / 下一步**：T25「找」加宽（决策 35：反馈流水进 prompt + 追问槽位）；T26 对话式规划（决策 36，含决策 6 修订）；P4 触达与导出（需用户给 SMTP 授权码）。样式方案仍待用户定。

## 3. 当前开放问题

无阻塞。**待用户亲手做的事**：① 走查计划表三级界面（`/new` 建阶段与任务 → 计划表打勾 / 跳过 / 提交交付物 → 看阶段「完成 / 未完成」与推进提案）；② 有空独立复核 `pytest -q` 与 `tools\smoke_p1.py`（预期值见第 6 节命令注释）。已回报：P2 四页、`/providers`、真实 provider 与模型（E-27）、T13 清单与去重（E-33/E-34）、采纳自动落阶段（2026-09-17）。

候选队列（不影响当前目标）：

- **两个仍有的事**：① `start_reason` 未落库（候选表没这列，加列 = Ask first），重看旧候选看不到依据 id 与推荐理由，要依据得重问一轮；② **没有「改节点字段」的写入口**（台账改写只对 `profile_item` / `plan` 开放），节点建好后改不了 `due_date` / `deliverable`，故重排提案批准后只能记方向——要做需先定「改节点算不算一次台账取代」（改契约，未立项）。
- **真实库现状**：计划类数据已按用户指示**物理清库**（工具 `tools/wipe_plan_data.py`，备份 `.bak-20260917-234923` 可回退）；库里只剩长期档案 9 条、provider 3 家、调用记账 17 条——用户为三级结构选了「不做老数据兼容」，别拿旧数量去对账。
- **两个已知边界**：① 去重只做「去空白 + 转小写」，不做模糊匹配——换个说法的同一件事仍可能被当新候选（刻意简单）；② 长输出慢（E-32/E-34）：候选清单 23–27 秒 / 约 5000 token，最坏一次 `find` 约 6 分钟；超时 180 秒 / 体检 20 秒。要提速得压 prompt，未做——**T25 往 prompt 里加反馈流水时把这条放在心上**。
- **口径与约定**：① 候选与提案别混——前者在 `/candidates` 裁定、后者在 `/proposals` 按 `kind` 分流；② `profile_item.category` 只收 `life_habit` / `life_log` / `current_state` / `short_term_goal` / `long_axis` 五个约定令牌（库内自由文本，表外 422）；取代/作废必填理由；同类别同文本回 409 并指明已有 id；③ 四问输出没给 `profile_item` id 又不说「依据不足」→ 一律判不合格；④ 档案 #4/#5 是修复前留下的重复，**未清**——想让档案干净去 `/profile` 作废一条；⑤ **`/report` 只列「最新 active 计划」的节点**（T24 按决策未动它）——多计划下想给别的计划报报告，先从该计划页面进去。
- **样式方案未决 + 零散口径**：前端全部页面**无 CSS、无组件库**。零散口径：`POST /api/report` 无防重复（正解是前端禁用按钮）；框架生成的 `404`/`405` 文案仍是英文；provider 的「地址/模型」清不成空（留空 = 不改）；`ledger.fetch_active` 取的是「初始业务状态」（名字误导、行为没错）；前端 effect 里同步 setState 会被 `react-hooks/set-state-in-effect` 拦（取数写成 `.then` 回调）。**远景**：SPEC 第 17 节第 3 条记着「档案自动提炼」愿景，不阻塞当前目标。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、视频进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——`plan` 没有（`closed` 是完成，不是否决），所以它是例外。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、阶段判定、周检查点判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422`、与现状冲突 `409`、业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- **P2 取数架构：浏览器直连**（SPEC 第 18 节第 26 条）。前端用客户端组件，`lib/api.ts` 在浏览器里 fetch 后端，因此受 CORS 名单约束。不采用 Next 16 主推的服务端取数 + Server Action 路线，理由与代价见 SPEC 第 10 节。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端；LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 29 条见 `docs/SPEC.md` 第 18 节，除用户明确要求不重新讨论；台账拆列（方案 A）仅在 SPEC 第 17 节第 2 条的三个触发条件满足时才重新打开。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。
- 本地库 `data/cadence.db` 有用户真实档案与手工验收数据（7 个计划、17 个节点、6 份报告、19 条候选、5 条 pending 提案）——**不得清库**。

## 5. 证据记录

只保留仍支撑第 1 节结论的行；已退休的旧证据见本文件的 git 历史。

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
|---|---|---|---|---|
| E-05 / E-08 / E-13（用户库三类事实） | 用户亲手在 `/docs` 走通 P1 闭环（建计划→阶段→2 检查点→3 报告→`GET /api/plan`）；`tools/show_db.py` 只读自查；误建 `plan_node #7` 处置（转 `skipped`、理由入台账） | **用户库里的计划类数据已于 2026-09-17 物理清库**（备份 `data/cadence.db.bak-20260917-234923`）；本行只剩历史意义 | 通过（当时）：节点按状态机推进、落后量 5 天→0、产出 1 条 pending `stage_advance` 提案、`GET /api/plan` 七项与预测吻合；show_db 与已知事实逐项一致 | 不受代码变更影响；但**「可随时复查」不再成立**——清库后旧计划数据不在库里 |
| E-19 / 2026-09-15 | 加完 CORS 后一次跑三样：`pytest -q`、`smoke_p1.py` 9 步、临时库起 uvicorn 发 10 组真实请求（5 组跨源 + 5 组错误路径） | 临时脚本与库已删；`data/` 只剩 `cadence.db` | 通过：合法来源两种写法回 `allow-origin`、非法来源静默不放行；**`422` 也带 `allow-origin`**；五条错误路径键集恒为 `{detail, errors}`、`detail` 恒为字符串 | 仅当 `config.py` 的 CORS 名单、`main.py` 两个错误处理器、或既有路由响应形状变更时失效；**新增互不相关路由、只改 tests 均不影响** |
| E-21 / E-22 / E-24 / E-28（前端早期各轮） | `/report`、`/`、`/new`、`/providers` 各轮 `npm run lint` + `npx tsc --noEmit`，另有路由 HTTP 200 探查；E-28 轮为提交前全项目复跑，同轮用户回报 P2 四页走查通过 | 工程内，可复跑 | 通过：各轮均 exit=0；lint 曾拦下 `react-hooks/set-state-in-effect`（已改为 `.then` 回调里 setState） | **按行为写**：各页行为或其对应 `lib/api.ts` 函数变更后失效；新增互不相关的函数不影响。最近一次全项目复跑见 E-36 |
| E-25 / 2026-09-15～16 | **真实 provider 首次打通**：commandcode 体检回 `403 error code: 1010`；用不带密钥的对照实验定位——无 UA 回 `403 1010`、换浏览器 UA 回服务商自己的 `401`；据此给 `llm.post_json` 加 `User-Agent`（+ `Accept`） | 对照命令见第 6 节；Cloudflare 1010 =「按浏览器指纹拒绝访问」 | 通过：改后同一代码路径拿到 `401`（服务商报错），已穿过 Cloudflare；用户真密钥体检由 E-27 结清 | `llm.post_json` 的请求头、或 provider 换家后失效。这类「门外被拦」假 provider 永远测不出 |
| E-26 / E-27 / 2026-09-16 | T12 四问链路：`pytest -q`（160 passed：147 + 13 条 advisor 单测）与 `smoke_p1.py` 9 步（动了契约与台账写入，按纪律加跑）；随后**真实模型端到端跑通一次**（用户操作，Agent 只读库核对）：`/ask` 提「我要不要学python？」→ 记账 `judge` 成功、549 进 / 598 出 token、5.5 秒，`proposal #3`（`material_judgment`）pending | `backend/tests/test_advisor.py` 可复跑；用户库 `llm_call` 与 `proposal #3` 可复查 | 通过：合格输出落 pending 提案且引用 id 真实存在；**没给 id 又不说「依据不足」判不合格**；不合格带原因重试一次、两次不合格抛错且不落提案；无档案不调模型。真实那次质量符合设计，**真密钥体检同时得证** | `advisor.py`、那两条路由、`RequestIn`、`post_json` 请求头、provider id=3 配置任一变更后失效。**真实输出质量只跑过 1 次** |
| E-29 / E-30 / 2026-09-16 | T22 档案录入与防重复：`pytest -q`（177 → 181：+17 条录入 +4 条防重复）与 `smoke_p1.py` 9 步（动了契约语义，按纪律加跑）；提交 `163f24e`/`b09bc34`/`af759fa` | `backend/tests/test_profile_write.py` 可复跑 | 通过：五令牌外 422；取代/作废后旧值从 `GET /api/profile` 消失但台账留痕；历史行再动 409；同类别同文本回 409 并指明已有 id；作废后重填不挡、不同类别不挡 | 那三条写路由与请求模型、`PROFILE_CATEGORIES`、或 `post_profile_item` 判重逻辑变更后失效 |
| E-31 / 2026-09-16 | T13：`pytest -q`（199 passed：181 + 18 条候选单测）与 `smoke_p1.py` 9 步（动了契约）；提交 `ad1bf18`（后端）、`585166f`（前端） | `backend/tests/test_candidates.py` 可复跑 | 通过：3–5 条越界判不合格并带原因重试、两次不合格不落一条；`rank` 即顺序、`is_recommended` 落在 `recommended_start`；`why` 无依据又不说「依据不足」判不合格；**禁区命中即判不合格**（含空白/大小写变体）；否决缺理由报错、理由进 `reject_reason` 与台账；已裁定 409、不存在 404；`GET /api/candidates` 取最近一轮、无候选返回空 | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效。**只验假上游** |
| E-32 / 2026-09-16 | 用户走查 T13 踩到读超时（连续两次在恰好 30 秒处 `TimeoutError`，记账 `#9/#10`）；修法：聊天类 `CHAT_TIMEOUT_SECONDS = 180`、体检 `CONNECTIVITY_TIMEOUT_SECONDS = 20`，超时消息补「等了多久 + 建议重试」；`pytest -q` 201 passed（+2 条回归）。提交 `ce265ac` | `backend/tests/test_llm.py` 可复跑；`llm_call` 记账可复查 | 通过：常量与 `post_json` 默认值一致且 ≥120 秒、体检短于聊天；超时抛 `LlmError` 且消息可操作；失败调用仍记账 | `llm.py` 超时常量、`post_json` 默认值或 `Operation.chat` 错误包装变更后失效。修的是「超时太紧」，不保证长清单必然一次成功 |
| E-33 / E-34 / 2026-09-17 | **真实模型「找」两次**（用户操作，Agent 只读库核对）：请求 3 落候选 #1–#5，用户否决 #1/#5（理由「1」）；请求 7 再问落 #6–#10，**两条禁区标题一条都没出现**；用户采纳 #6（按设计不进禁区）。记账 `llm_call #12`：26.7 秒、输出 4975 token | 用户库 `candidate` / `learning_request` / `llm_call` 可复查 | 通过：成功标准 2 前半句（3–5 条带排序、真 provider）由 E-33 满足；后半句（否决过的不再出现，字面口径）由 E-34 满足 | 证的是「否决 → 禁区 → 重问不出现」链路（`_rejected_titles`、prompt 禁区段、`_check_find`）。**未覆盖**：换说法的同一件事是否漏过（归一化只做去空白与小写）；样本仅两轮 |
| E-36 / 2026-09-17 | **采纳自动落阶段**（用户拍板，方案经确认后实现）：`decide_candidate` 采纳时在最新 active 计划建同名阶段（预检前置）；响应新增 `plan_id`/`node_id`；`/ask` 采纳提示改为「已在计划 #N 建了阶段 #M」。`pytest -q` 204 passed（+3 条）、`smoke_p1.py` 9 步、前端 lint/tsc exit=0。提交 `ff8a0d8` | `backend/tests/test_candidates.py` 可复跑 | 通过：采纳 → 候选 `accepted` 且计划里出现同名 `stage`（台账紧随 create 事件）；无 active 计划或同名未收尾阶段 → `CandidateConflict`（409）且候选保持 `proposed`、不建节点；否决不建节点 | `decide_candidate`、verdict 路由、`VerdictResult` 或 `/ask` 采纳分支变更后失效 |
| E-37 / 2026-09-17 | **T14：提案两条后端路由（`d1fa80f`）＋两个正式页与 `/ask` 退场（`124634d`）**。后端 `pytest -q` **216 passed**（+12 条）、`smoke_p1.py` 9 步；前端 lint/tsc exit=0、`/`、`/candidates`、`/proposals` 均 200（非浏览器验证） | `backend/tests/test_proposals.py` 可复跑 | 通过：只列 pending、payload 解成对象、可按 kind 过滤；裁定落 `accepted`/`rejected` 并补 `decided_at`；驳回缺理由 400、已裁定 409、不存在 404、批错方向 400（验不过时保持 pending）；批准「最后一段」→ 计划真 `closed`，其余只记账 | `proposals.py`、那两条路由与 `ProposalDecideIn`、两个新页面、`api.ts` 的 `listProposals`/`decideProposal` 变更后失效。**只验假上游**；**浏览器走查归用户** |
| E-38 / 2026-09-17 | **T23 三级结构与交付物验收**（规格见 SPEC 决策 30–32）：`plan_node.level` 加 `task`；阶段完成 = 任务全打勾/跳过 **且** 交付物已提交；打勾 / 跳过（必填理由）/ 提交交付物三个动作与三条路由；新表 `deliverable_submission`（重提交 = 新行）；推进提案文案改「任务全部完成、交付物已提交」。验证：`pytest -q` **229 passed**（新增 `test_task_layer.py` 10 条 + 改写 8 条旧用例）、`smoke_p1.py` **10 步全绿**（三级流程）、前端 lint/tsc exit=0。提交 `3a6854b`/`b18b732`；规格落盘 `5a8af21`；清库工具 `3316714` | `backend/tests/test_task_layer.py` 可复跑 | 通过：四组合判定符合新规则；无任务阶段只看交付物；周打卡不参与判定；跳过算完成且理由进台账；交付物只对阶段、重提交留痕、当前值 = 最新一行；打勾/跳过只对任务（层级不符 400、不存在 404） | `plan.py` 判定与三个动作、那三条路由、`deliverable_submission` 表、计划表组件与 `/new`、`api.ts` 三个函数变更后失效。**老数据不做兼容**（物理清库，备份 `.bak-20260917-234923`）；**浏览器走查归用户** |
| E-39 / 2026-09-18 | **T24 多计划与严格分开**（规格见 SPEC 决策 33–34）：`db.init` 加列迁移（`learning_request.plan_id`）；`plan.list_plans`/`close_plan`/`void_plan` + 三条路由；「找」把计划上下文（目标/当前阶段/开着任务）带进 prompt；候选随请求继承归属；采纳落点 = 归属计划（无归属须显式 `plan_id`，否则 409）；同计划上一轮未裁定候选自动过期。验证：`pytest -q` **244 passed**（+12 条新测试、改写 3 条）、`smoke_p1.py` 10 步全绿、前端 lint/tsc exit=0。提交 `9f68eab`/`b6c20ec` | `backend/tests/test_plans.py` 可复跑 | 通过：默认列表只给进行中；收尾幂等进历史；作废理由必填且从列表消失；归属随请求传递并可带出；落归属计划、无归属不指明 409 且保持 `proposed`、冲突/不存在/已收尾一律拒；同计划上一轮未裁定的过期、已裁定的不动、别的计划不受影响、过期不进禁区 | `list_plans`/`close_plan`/`void_plan`、加列迁移、`advisor` 归属与过期逻辑、`find.py` 计划上下文段、三条计划路由、计划切换器与 `/candidates` 变更后失效。**浏览器走查归用户** |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行）
.\.venv\Scripts\python.exe -m pytest -q           # 预期 244 passed
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 10 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启
# 验「能不能穿过 Cloudflare」（假密钥，只看放不放行，见 E-25）：
#   .\.venv\Scripts\python.exe -c "from app import llm; print(llm.post_json('https://api.commandcode.ai/provider/v1/chat/completions', {'Authorization': 'Bearer dummy'}, llm._chat_payload('deepseek/deepseek-v4.1-flash', [{'role':'user','content':'ping'}]))[0])"
#   预期 401（服务商报错）；若回 403 + error code: 1010，说明请求头又被门外拦了

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
                   # 现有页面：/ 计划表（含打勾/跳过/提交交付物） · /new 建计划与建阶段任务 · /report 提交报告 · /candidates 候选清单 · /proposals 待裁定提案（含判资料） · /providers LLM 提供商 · /profile 长期档案
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000，各占一个终端窗口；本轮结束时两个端口空闲。**停服务别只杀父进程**：`--reload` 与 `next dev` 都会留子进程占端口（`job_kill` 只杀 PowerShell 外壳）；用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端换 3000）找 PID 再 `Stop-Process`。热加载只监听 Python 文件（改 `sql/schema.sql` 不触发），且走轮询（未装 watchfiles）。**待用户执行的验收**见第 3 节第一段。

| 任务类型 | 必读文件 |
|---|---|
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **前端（写代码前必读）** | `frontend/AGENTS.md`（`next dev` 自动生成、会自行重建，提交它保持工作区干净）与 `frontend/node_modules/next/dist/docs/` 的 `upgrading/version-16.md`、`01-getting-started/06-fetching-data.md`——Next 16 相对训练数据有破坏性变更（已见实例：`app/layout.tsx` 用新 `LayoutProps` 类型），凭记忆写会撞已换掉的 API；再叠 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更与需求背景 | `docs/SPEC.md` 第 10、11、17、18 节（产品/架构）与第 1–9 节（需求背景）、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git、工作树与未提交改动；只读取当前目标（下一步 **T25**，其后 T26）的源码、测试与规则——**规格已钉死**：先读 SPEC 决策 35（找加宽）/36（对话式规划）/6（调用位置修订）与 `tasks/todo.md` 里 T25、T26 的「实现要点」，照做即可，不必重新设计。
2. 证据能否沿用，看第 1 节的「证据依据」列与第 5 节每行的失效条件；没有触发条件不做全量复验。
3. **探测一律指向临时库**（照 `tools/smoke_p1.py`：临时库 + 空闲端口），绝不拿 `data/cadence.db` 做实验——曾把契约探测打到真库、误建 `plan_node #7`。遵守项目 `AGENTS.md`：教学协议、验证纪律、每次提交 ≤200 行有效改动、加依赖与改契约 Ask first。
4. **没有事实变化就不更新交接文档；要更新时只做定点编辑**（含退休检查：删已解除问题、失效无引用的证据行、跨节重复），并按 `AGENTS.md` 跑校验脚本至 errors=0、warnings=0。
