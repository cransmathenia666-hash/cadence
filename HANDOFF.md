# cadence 交接文档

> 最近更新：2026-09-17（T14 完成：候选页 `/candidates`、提案裁定页 `/proposals`（含判资料）、提案两条后端路由；`/ask` 按 SPEC 决策 29 退场）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜基线：后端 `fe4a5b8`、前端脚手架 `d8f9e3c`；近期：T12 `f08f75b`、T11+`/ask` `882cf69`、T13 `ad1bf18`/`585166f`、T22 `163f24e`/`b09bc34`、防重复 `af759fa`、超时放宽 `ce265ac`、采纳自动落阶段 `ff8a0d8`、T14 后端 `d1fa80f`、T14 前端 `124634d`｜无远端｜未提交改动：本文件（T14 交接更新）＋瘦身版交接与 `AGENTS.md` 一行（另一会话的改动）；`.dsh-vision-toolkit/` 未跟踪（非本项目产物）
> 其他工作树：无
> 当前唯一目标：**P3（决策入口）代码完成，待用户走查**——T10–T14、T22 全部落地；P3 收尾的定义是用户亲手走查两个新页面（真实模型各跑一次「找」与「判」）
> 下一条动作：用户在 `/candidates` 与 `/proposals` 上走查（可顺带裁定库里那 5 条 pending 提案）；Agent 侧下一步是 **P4（触达提醒 + Markdown 导出）**——开工前需用户提供邮箱 SMTP 授权码，并按约定先报计划

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
|---|---|---|---|
| 后端 P0 地基（schema / ledger） | 通过 | E-19 | `ledger.py` 变更后失效 |
| P1 规则层（状态机 / 落后量 / 周检查点判定，T4–T6） | 通过 | E-19 | `backend/` 代码与测试变更后失效 |
| 建节点防重复（T19） | 通过 | E-19 | 同上 |
| 台账语义归属（T20，方案 B） | 通过 | E-19、E-05/E-08/E-13 | `ledger.py`、`plan.py` 变更后失效 |
| 接口契约（入参校验 + 方案 C 错误形状 + CORS + 启动） | 通过 | E-19 | `main.py`、`config.py` 或接口契约变更后失效 |
| 端到端闭环（脚本可复跑 + 用户亲手走通） | 通过 | E-19；E-05/E-08/E-13 | 用户库数据可随时复查；脚本或既有路由变更后失效 |
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
| SPEC 第 9 节真实使用验收 | 未验证 | 标准 1、5 的后端部分由 E-05、E-19 覆盖 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：P3（决策入口）代码已完成，剩用户走查。** 完成定义对应 SPEC 第 9 节成功标准 2 与 3：输入「我不知道该学什么」得到 3–5 条带排序的候选；输入「我发现了某个资料」得到能指回长期档案字段的四问判断（E-27 已跑通一次）。两者都由 LLM 产出**待裁定的结果**，经用户裁定后才落库。

**P3 已完成**：T10 provider 管理与调用记账（密钥只写不读、每次调用落一行账、同一操作最多 3 次调用）；T11 `/providers` 页；T12 四问判断（`advisor.py` + `POST /api/requests`(evaluate) + `GET /api/profile`）；T13 候选清单与去重（`providers/find.py` + `find_candidates` / `propose_candidates` / `decide_candidate` + `POST /api/requests`(search) + `GET /api/candidates` + `POST /api/candidates/{id}/verdict`；去重是硬保证——禁区命中判不合格、重试后仍命中则不落一条）；T22 档案三条写入口 + `/profile` 页；采纳自动建同名阶段（E-36）；**T14 候选页与提案裁定页 + 提案两条后端路由（E-37）**——`/candidates`（找方向的正门，进页先取最近一轮，不用重问模型）、`/proposals`（判一份资料 + 待裁定提案按 `kind` 分流裁定）；临时页 `/ask` 删除（SPEC 决策 29）。

**本轮不做 / 下一步**：触达与 Markdown 导出（P4，开工前需用户给 SMTP 授权码）；台账拆列（方案 A，触发条件见 SPEC 第 17 节第 2 条）。样式方案仍待用户定。

## 3. 当前开放问题

无阻塞。**待用户亲手做的事**：① 走查两个新页面（`/candidates`：来一轮真实「找」并采纳/否决；`/proposals`：判一份资料 + 裁定库里那 5 条 pending 提案里的任意一条）；② 有空独立复核 `pytest -q` 与 `tools\smoke_p1.py`（预期值见第 6 节命令注释）。已回报的走查：P2 四页、`/providers`、`/ask`（2026-09-16「前端体验没有问题」）、真实 provider 与真实模型（E-27）、T13 清单质量与去重（E-33/E-34）、采纳自动落阶段（用户 2026-09-17 回报「验证通过」）。

候选队列（不影响当前目标）：

- **T14 的结论与后续开放项（2026-09-17 用户拍板）**：① 两个入口分清了——`/candidates` 只答「学什么方向」、`/proposals` 只答「这份资料要不要学」；`/ask` 删除（SPEC 决策 29）；② `start_reason` 仍未落库（候选表没这列，加列 = Ask first），重看旧候选时不显示依据 id 与推荐理由，要看依据得重问一轮；③ **没有「改节点字段」的写入口**——台账改写只对 `profile_item` / `plan` 开放，节点改不了 `due_date` / `deliverable`，故重排提案批准后只能记方向、落不了地；要做需先定「改节点算不算一次台账取代」（改契约，未立项）。
- **真实库现状（2026-09-17 只读核对）**：**7 个计划全部 active**（id 1–7，含试建计划）、17 个节点、6 份报告、19 条候选、**5 条 pending 提案且一条未裁**：`#1`/`#2` 是「后面没有更多阶段」的推进提案（指向计划 #3 与 #6，批准即**收尾那个计划**）、`#3`–`#5` 是三次四问判断。注意：**采纳候选自动建的阶段落进最新 active 计划（现为 #7「asdasdsadsad」）**——想落进正经计划就先建一个。
- **样式方案未决**：前端全部页面**无 CSS、无组件库**；`sort_order` 前端固定送 0，新建节点按 id 排。
- **两个已知边界**：① 去重只做「去空白 + 转小写」归一化、不做模糊匹配——「学 Python」与「Python 基础」仍可能被当新候选（刻意的简单口径）；② 长输出慢（E-32/E-34）——候选清单 23–27 秒 / 4350–4975 token，最坏一次 `find` 约 6 分钟；聊天类超时 180 秒、体检 20 秒。要提速得压 prompt 输出长度，都未做。
- **T12/T13 的约定**（T14 不得放松）：① 候选与提案别混——T13 落 `candidate`（`status='proposed'`）、四问落 `proposal(kind=material_judgment)`，前者在 `/candidates` 页采纳/否决、后者在 `/proposals` 页按 kind 分流裁定；② `proposal.kind` 新增取值 `material_judgment`（不加列、无迁移）；③ `profile_item.category` 约定词表 `life_habit` / `life_log` / `current_state` / `short_term_goal` / `long_axis`（库内自由文本；表外值不丢，只是不算「缺失类别」）；④ 四问输出没给 `profile_item` id 又不说「依据不足」→ 一律判不合格。
- **档案现状与写入口径**（T22）：五类已补齐——`long_axis` id 2/3、`short_term_goal` id 4/5、`current_state` id 6、`life_habit` id 7、`life_log` id 9（id 1 superseded、id 8 void）。写入口径：`category` 只收这五个令牌（Literal 挡住，表外 422）；取代/作废必填理由；防一字不差重复——同类别同文本回 409 并指明已有 id，判重只看当前有效条目（作废后重填不挡、不同类别不挡）。注意 #4/#5 是修复前留下的一字不差重复（`short_term_goal`），**未清**（用户数据不擅自动）；想让档案干净去 `/profile` 作废一条。
- **只能操作「最新建的那个有效计划」**（多计划未支持，待定）：契约没有列出计划的接口，前端只能拿 `GET /api/plan` 不传参的「最新 active 计划」；`/new` 与计划表已写明「将建在 计划 #N 里」。支持多计划需加 `GET /api/plans`（改契约）。用户库现有 3 个计划，另两个界面够不着。
- 零散口径：`POST /api/report` 无防重复（连点落两条；正解是前端提交后禁用按钮）；框架生成的 `404`/`405` 文案仍是英文（错误形状已统一）；provider 的「地址/模型」在界面上清不成空（留空 = 不改，要清只能改库）；`ledger.fetch_active` 实际取「初始业务状态」（候选只回 `proposed`、提案只回 `pending`），名字误导但行为没错；前端 effect 里同步 setState 会被 `react-hooks/set-state-in-effect` 拦——取数写成 `.then(回调)` 里 setState。
- **P4 前与远景**：P4 前需用户提供邮箱 SMTP 授权码（邮件提醒已定为启用）；用户那条留空的需求（原文「2、」后空白）默认不做、等其补；SPEC 第 17 节第 3 条（用户提出）记着「档案自动提炼」愿景——由 agent 读本地原始库、自动提炼五类档案（走 pending 提案 + 用户裁定），不阻塞当前目标。

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
| E-05 / E-08 / E-13（用户库三类事实） | 用户亲手在 `/docs` 走通 P1 闭环（建计划→阶段→2 检查点→3 报告→`GET /api/plan`）；`tools/show_db.py` 只读自查；误建 `plan_node #7` 处置（转 `skipped`、理由入台账） | 用户库 `data/cadence.db` 留痕，可随时复查 | 通过：节点按状态机推进、落后量 5 天→0、产出 1 条 pending `stage_advance` 提案、`GET /api/plan` 七项与预测吻合；show_db 与已知事实逐项一致 | 不受代码变更影响；`plan.py` 变更后 E-08 需重看 |
| E-19 / 2026-09-15 | 加完 CORS 后一次跑三样：`pytest -q`、`smoke_p1.py` 9 步、临时库起 uvicorn 发 10 组真实请求（5 组跨源 + 5 组错误路径） | 临时脚本与库已删；`data/` 只剩 `cadence.db` | 通过：合法来源两种写法回 `allow-origin`、非法来源静默不放行；**`422` 也带 `allow-origin`**；五条错误路径键集恒为 `{detail, errors}`、`detail` 恒为字符串 | 仅当 `config.py` 的 CORS 名单、`main.py` 两个错误处理器、或既有路由响应形状变更时失效；**新增互不相关路由、只改 tests 均不影响** |
| E-21 / E-22 / E-24 / E-28（前端早期各轮） | `/report`、`/`、`/new`、`/providers` 各轮 `npm run lint` + `npx tsc --noEmit`，另有路由 HTTP 200 探查；E-28 轮为提交前全项目复跑，同轮用户回报 P2 四页走查通过 | 工程内，可复跑 | 通过：各轮均 exit=0；lint 曾拦下 `react-hooks/set-state-in-effect`（已改为 `.then` 回调里 setState） | **按行为写**：各页行为或其对应 `lib/api.ts` 函数变更后失效；新增互不相关的函数不影响。最近一次全项目复跑见 E-36 |
| E-25 / 2026-09-15～16 | **真实 provider 首次打通**：commandcode 体检回 `403 error code: 1010`；用不带密钥的对照实验定位——无 UA 回 `403 1010`、换浏览器 UA 回服务商自己的 `401`；据此给 `llm.post_json` 加 `User-Agent`（+ `Accept`） | 对照命令见第 6 节；Cloudflare 1010 =「按浏览器指纹拒绝访问」 | 通过：改后同一代码路径拿到 `401`（服务商报错），已穿过 Cloudflare；用户真密钥体检由 E-27 结清 | `llm.post_json` 的请求头、或 provider 换家后失效。这类「门外被拦」假 provider 永远测不出 |
| E-26 / E-27 / 2026-09-16 | T12 四问链路：`pytest -q`（160 passed：147 + 13 条 advisor 单测）与 `smoke_p1.py` 9 步（动了契约与台账写入，按纪律加跑）；随后**真实模型端到端跑通一次**（用户操作，Agent 只读库核对）：`/ask` 提「我要不要学python？」→ 记账 `judge` 成功、549 进 / 598 出 token、5.5 秒，`proposal #3`（`material_judgment`）pending | `backend/tests/test_advisor.py` 可复跑；用户库 `llm_call` 与 `proposal #3` 可复查 | 通过：合格输出落 pending 提案且引用 id 真实存在；**没给 id 又不说「依据不足」判不合格**；首次不合格带原因重试一次、两次不合格抛错且不落提案；无档案不调模型；三反引号包裹的 json 被容忍。真实那次质量符合设计（引用真实存在的 `#2` 并复述内容；④ 明写「依据不足」），**真密钥体检同时得证** | `advisor.py`、那两条路由、`RequestIn`、`post_json` 请求头、provider id=3 配置任一变更后失效。**真实输出质量只跑过 1 次** |
| E-29 / E-30 / 2026-09-16 | T22 档案录入与防重复：`pytest -q`（177 → 181：+17 条录入 +4 条防重复）与 `smoke_p1.py` 9 步（动了契约语义，按纪律加跑）；提交 `163f24e`/`b09bc34`/`af759fa` | `backend/tests/test_profile_write.py` 可复跑 | 通过：五令牌外 422；取代/作废后旧值从 `GET /api/profile` 消失但台账留 before/after/理由；历史行再动 409；同类别同文本（含首尾空白差异）回 409 并指明已有 id；作废后重填不挡、不同类别不挡 | 那三条写路由与请求模型、`PROFILE_CATEGORIES`、或 `post_profile_item` 判重逻辑变更后失效 |
| E-31 / 2026-09-16 | T13：`pytest -q`（199 passed：181 + 18 条候选单测）与 `smoke_p1.py` 9 步（动了契约）；提交 `ad1bf18`（后端）、`585166f`（前端） | `backend/tests/test_candidates.py` 可复跑 | 通过：3–5 条越界判不合格并带原因重试一次、两次不合格则不落一条；`rank` 即顺序、`is_recommended` 落在 `recommended_start` 那条；`why` 无依据又不说「依据不足」判不合格；**禁区命中即判不合格**（含空白/大小写变体）；否决缺理由报错、理由进 `reject_reason` 与台账；已裁定再改 409、不存在 404；`GET /api/candidates` 默认取最近一轮、无候选返回空 | `find_candidates` / `_check_find` / `decide_candidate`、`providers/find.py`、那三条路由、`ledger.set_status` 的 `extra` 变更后失效。**只验假上游** |
| E-32 / 2026-09-16 | 用户走查 T13 踩到读超时（连续两次在恰好 30 秒处 `TimeoutError`，记账 `#9/#10`）；修法：聊天类 `CHAT_TIMEOUT_SECONDS = 180`、体检 `CONNECTIVITY_TIMEOUT_SECONDS = 20`，超时消息补「等了多久 + 建议重试」；`pytest -q` 201 passed（+2 条回归）。提交 `ce265ac` | `backend/tests/test_llm.py` 可复跑；`llm_call` 记账可复查 | 通过：常量与 `post_json` 默认值一致且 ≥120 秒、体检短于聊天；超时抛 `LlmError` 且消息可操作；失败调用仍记账 | `llm.py` 超时常量、`post_json` 默认值或 `Operation.chat` 错误包装变更后失效。修的是「超时太紧」，不保证长清单必然一次成功 |
| E-33 / E-34 / 2026-09-17 | **真实模型「找」两次**（用户操作，Agent 只读库核对）：请求 3 落候选 #1–#5，用户否决 #1/#5（理由「1」）；请求 7 再问落 #6–#10，**两条禁区标题一条都没出现**；用户采纳 #6（按设计不进禁区）。记账 `llm_call #12`：26.7 秒、输出 4975 token | 用户库 `candidate` / `learning_request` / `llm_call` 可复查 | 通过：成功标准 2 前半句（3–5 条带排序、真 provider）由 E-33 满足；后半句（否决过的不再出现，字面口径）由 E-34 满足 | 证的是「否决 → 禁区 → 重问不出现」链路（`_rejected_titles`、prompt 禁区段、`_check_find`）。**未覆盖**：换说法的同一件事是否漏过（归一化只做去空白与小写）；样本仅两轮 |
| E-36 / 2026-09-17 | **采纳自动落阶段**（用户拍板，方案经确认后实现）：`decide_candidate` 采纳时在最新 active 计划建同名阶段（预检前置）；响应新增 `plan_id`/`node_id`；`/ask` 采纳提示改为「已在计划 #N 建了阶段 #M」。`pytest -q` 204 passed（+3 条）、`smoke_p1.py` 9 步、前端 lint/tsc exit=0。提交 `ff8a0d8` | `backend/tests/test_candidates.py` 可复跑 | 通过：采纳 → 候选 `accepted` 且计划里出现同名 `stage`（台账紧随 create 事件）；无 active 计划或同名未收尾阶段 → `CandidateConflict`（409）且候选保持 `proposed`、不建节点；否决不建节点 | `decide_candidate`、verdict 路由、`VerdictResult` 或 `/ask` 采纳分支变更后失效 |
| E-37 / 2026-09-17 | **T14：提案两条后端路由（提交 `d1fa80f`）＋两个正式页与 `/ask` 退场（提交 `124634d`）**。后端：`pytest -q` **216 passed**（+12 条提案单测）、`smoke_p1.py` 9 步（动了契约，按纪律加跑）；前端：`npm run lint` 与 `npx tsc --noEmit` 均 exit=0，`/`、`/candidates`、`/proposals` 在 dev 服务上均返回 200（**不是浏览器验证**）。另用运行中的后端只读验 `GET /api/proposals` 能列出真实库里那 5 条 pending 提案 | `backend/tests/test_proposals.py` 可复跑；提案数据在用户库可复查 | 通过：`GET /api/proposals` 只列 pending、payload 解成对象、可按 kind 过滤、坏 JSON 不炸整页；裁定 → 提案落 `accepted`/`rejected` 且补上 `decided_at`；驳回缺理由 400、已裁定 409、不存在 404、批准重排未选/选错方向 400（且验不过时提案保持 pending）；批准「最后一段」推进提案 → 计划真的 `closed`（唯一结构性动作），批准「进下一阶段」与四问判断 → 只记账；重排选中的方向进台账 | `proposals.py`、那两条路由与 `ProposalDecideIn`、两个新页面、`lib/api.ts` 的 `listProposals`/`decideProposal` 变更后失效。**只验假上游**（提案 payload 手写 + 真实生产者各打一遍）；**浏览器走查归用户** |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行）
.\.venv\Scripts\python.exe -m pytest -q           # 预期 216 passed
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 9 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启
# 验「能不能穿过 Cloudflare」（假密钥，只看放不放行，见 E-25）：
#   .\.venv\Scripts\python.exe -c "from app import llm; print(llm.post_json('https://api.commandcode.ai/provider/v1/chat/completions', {'Authorization': 'Bearer dummy'}, llm._chat_payload('deepseek/deepseek-v4.1-flash', [{'role':'user','content':'ping'}]))[0])"
#   预期 401（服务商报错）；若回 403 + error code: 1010，说明请求头又被门外拦了

cd D:\cadence\frontend   # ——— 以下在另一个终端 ———
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
                   # 现有页面：/ 计划表 · /new 建计划与建节点 · /report 提交报告 · /candidates 候选清单 · /proposals 待裁定提案（含判资料） · /providers LLM 提供商 · /profile 长期档案
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000，各占一个终端窗口；本轮结束时两个端口空闲。**停服务别只杀父进程**：`--reload` 与 `next dev` 都会留子进程占端口（`job_kill` 只杀 PowerShell 外壳）；用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端换 3000）找 PID 再 `Stop-Process`。热加载只监听 Python 文件（改 `sql/schema.sql` 不触发），且走轮询（未装 watchfiles）。**待用户执行的验收**见第 3 节第一段。

| 任务类型 | 必读文件 |
|---|---|
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **前端（写代码前必读）** | `frontend/AGENTS.md`（`next dev` 自动生成、会自行重建，提交它保持工作区干净）与 `frontend/node_modules/next/dist/docs/` 的 `upgrading/version-16.md`、`01-getting-started/06-fetching-data.md`——Next 16 相对训练数据有破坏性变更（已见实例：`app/layout.tsx` 用新 `LayoutProps` 类型），凭记忆写会撞已换掉的 API；再叠 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更与需求背景 | `docs/SPEC.md` 第 10、11、17、18 节（产品/架构）与第 1–9 节（需求背景）、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git、工作树与未提交改动；只读取当前目标（T14 已完工；下一步是 P4 触达与 Markdown 导出，或按用户指派）的源码、测试与规则。
2. 证据能否沿用，看第 1 节的「证据依据」列与第 5 节每行的失效条件；没有触发条件不做全量复验。
3. **探测一律指向临时库**（照 `tools/smoke_p1.py`：临时库 + 空闲端口），绝不拿 `data/cadence.db` 做实验——曾把契约探测打到真库、误建 `plan_node #7`。遵守项目 `AGENTS.md`：教学协议、验证纪律、每次提交 ≤200 行有效改动、加依赖与改契约 Ask first。
4. **没有事实变化就不更新交接文档；要更新时只做定点编辑**，每次更新顺带执行退休检查（删已解除问题、已失效且无引用的证据行、跨节重复叙述），并按 `AGENTS.md` 跑校验脚本至 errors=0、warnings=0。
