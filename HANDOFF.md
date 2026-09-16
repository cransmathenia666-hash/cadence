# cadence 交接文档

> 最近更新：2026-09-16（档案补入防一字不差重复 `af759fa`——用户走查发现的缺口；另把 Open Question 第 3 条「agent 提炼档案路线」的记录并入版本库）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜基线：后端 `fe4a5b8`、前端脚手架 `d8f9e3c`、T12 `f08f75b`、T11+`/ask` `882cf69`、交接 E-28 `7dec7aa`、档案录入 `163f24e`/`b09bc34`、防重复 `af759fa`｜无远端｜未提交改动只剩用户三件（见第 3 节）与 `.dsh-vision-toolkit/`（非本项目产物，未动）
> 其他工作树：无
> 当前唯一目标：**P3（决策入口）进行中**——T10（provider 管理 + 调用记账）、T11（provider 管理页面）、T12（四问判断链路）、T22（档案录入，2026-09-16 补的清单缺口）已完成，剩下 T13–T14。P1、P2 已完成（走查已过）
> 下一条动作：做 T13（候选清单生成与去重）

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
|---|---|---|---|
| 后端 P0 地基（schema / ledger） | 通过 | 本轮 E-19 | E-01 已失效（`ledger.py` 被改）；现由 E-19 覆盖 |
| P1 规则层（状态机 / 报告与落后量 / 周检查点判定，T4–T6） | 通过 | 本轮 E-19 | E-06 已失效（`backend/` 代码与测试被改）；现由 E-19 覆盖 |
| 建节点防重复提交（T19） | 通过 | 本轮 E-19 | E-07 已失效；HTTP 层由 E-19 复现，其余三条下沉为单测 |
| 台账语义归属（T20，方案 B） | 通过 | 本轮 E-19、E-13 | `ledger.py`、`plan.py` 改动后失效 |
| 接口契约（入参校验 + 方案 C 错误形状 + CORS + 服务启动） | 通过 | 本轮 E-19 | `main.py`、`config.py` 或接口契约改动后失效 |
| 端到端闭环（脚本可复跑） | 通过 | 本轮 E-19 | E-09 已失效；现由 E-19 覆盖 |
| 端到端闭环（用户亲手在 `/docs` 走通） | 通过 | 沿用 E-05，本轮由 E-19 端到端复现 | 用户库数据可随时复查 |
| `backend/tools/` 两个自查脚本 | 通过 | 沿用 E-08，本轮重跑结果逐项一致 | 脚本自身或 `plan.py` 变更后失效 |
| LLM provider 管理与调用记账（T10） | 通过 | E-23（结论由本轮 E-26 的 160 passed 原样重跑覆盖） | `llm.py`、或 `main.py` 里那 6 条 provider / llm-calls 路由与请求模型变更后失效 |
| 前端 `frontend/`（T7 + T8 + T9 + 建节点页） | 通过 | 沿用 E-18、E-21、E-22（行为结论仍在）；lint/tsc 见 E-24 与 E-28（E-28 是 `/ask` 加入后的全项目复跑） | 相关页面的行为或 `lib/api.ts` 的对应函数改动后失效；**样式未做**；浏览器手工走查用户 2026-09-16 回报「前端体验没有问题」 |
| provider 管理页面（T11） | 通过 | 本轮 E-24（前端只验 lint 与 tsc）＋**用户手工走查** | `frontend/app/providers/page.tsx` 的行为、或 `lib/api.ts` 里那 5 个 provider 函数与 `Provider*` 类型改动后失效；**样式未做**；用户 2026-09-15 口头回报走查成功 |
| 四问判断链路（T12） | 通过 | 本轮 E-25、E-26 + **E-27（真实模型跑通）** | `advisor.py`、或 `main.py` 里 `/api/requests`、`/api/profile` 两条路由与 `RequestIn` 变更后失效；单测只打假上游，真实输出质量由 E-27 覆盖一次 |
| `/ask` 四问临时入口页（T14 提前做的一小块） | 通过（仅 lint/tsc） | 本轮追加的 `npm run lint` + `npx tsc --noEmit` 均 exit=0（**这两条是全项目跑的，`/ask` 就此补上**，见 E-28） | `frontend/app/ask/page.tsx`：浏览器走查未单独做，但用户经它真实跑通过一次四问（E-27）且 2026-09-16 回报前端体验没有问题；行为或 `lib/api.ts` 的 `askMaterial`/`getProfile` 改动后失效 |
| 档案录入（T22，2026-09-16 补的清单缺口） | 通过 | 本轮 E-29、E-30 | `main.py` 里那三条 profile 写路由与请求模型、或 `advisor.PROFILE_CATEGORIES` 词表变更后失效；`/profile` 页用户 2026-09-16 已实际补档并跑通一次四问（回报「验证通过」），借此发现的「一字不差可重复补入」缺口由 `af759fa` 结清（E-30） |
| SPEC 第 9 节真实使用验收 | 未验证 | 成功标准 1、5 的后端部分已由 E-05、E-19 覆盖；2、3、4 与前端部分未做 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：P3（决策入口）进行中——T10、T11、T12 已完成，剩下 T13–T14。** 完成定义对应 SPEC 第 9 节成功标准 2 与 3：输入「我不知道该学什么」得到 3–5 条带排序的候选；输入「我发现了某个资料」得到能指回长期档案字段的四问判断。两者都由 LLM 产出**提案**，经用户裁定后才落库。

**P3 进度：** T10 已完成——`llm.py` + 6 条接口（providers 增删改查、连通性体检、`llm-calls` 记账）；密钥**只写不读**、每次调用落一行账、**同一操作最多 3 次调用**。T11 已完成——`frontend/app/providers/page.tsx` 一页装下列表（掩码）、增、改、删、测连通性、设为默认。T12 已完成——`backend/app/advisor.py`（四问判断：读档案 → 组 prompt → 调模型 → Pydantic 校验 → 不合格就带着原因重试一次 → 合格经台账落 `pending` 提案；**没给 profile_item id 又不说「依据不足」的一律判不合格**）+ `POST /api/requests` + `GET /api/profile`。T22（档案录入）已完成——`POST /api/profile` / `PUT /api/profile/{id}` / `POST /api/profile/{id}/void` 三条写入口（全走台账）+ `/profile` 档案页。**T13 候选清单、T14 候选与提案的前端** 还没做。

**本轮不做 / 下一步：** 触达与 Markdown 导出（P4）、台账拆列（方案 A，触发条件见 SPEC 第 17 节第 2 条）不在本轮。接着做 **T13（候选清单生成与去重）**——「找」与「判」共用 SPEC 第 4 节那一套判据，`advisor.py` 的四问骨架（Operation 调用、Pydantic 校验、重试一次）可以接着用。**T13 的端到端需要真实密钥**（决策 19：用户的 commandcode 已在其库里配成默认），单测一律假 provider 打桩、不花钱。样式方案仍待用户（走查已过）。

## 3. 当前开放问题

无阻塞。**待用户亲手做的只剩一件事**：② 有空时独立复核一次 `pytest -q`（预期 `160 passed`）与 `tools\smoke_p1.py`（预期 9 步全绿）。**已完成**：① P2 四页浏览器走查——用户 2026-09-16 回报「前端体验没有问题」；T11 的 `/providers` 用户 2026-09-15 回报走查成功；**真实 provider 与真实模型**用户 2026-09-16 已跑通（见 E-27）——E-25 的「真密钥待回报」那条已经不欠了。

候选队列（不影响当前目标）：

- **P2 代码已完成、走查已过（用户 2026-09-16 回报「前端体验没有问题」）**：`frontend/app/` 下四个页面——`/` 计划表（T8）、`/new` 建计划与建节点、`/report` 提交报告。**样式完全没做**（无 CSS、无组件库，样式方案仍未决）；`sort_order` 前端固定送 0，所以新建节点的顺序按 id 排。
- **调用记账前端已接上（2026-09-16 补做）**：`GET /api/llm-calls` 不再是"只能在 `/docs` 看"——`/providers` 页底部加了一个「查看调用记账」按钮（**点才拉，不自动请求**），展示按周汇总 + 最近流水（provider_id 会对着本页已加载的列表换成名字，删掉的 provider 回退成 `#id`，因为账是故意留着的）。前端因此**不再有缺口**：13 条接口里只剩 `GET /api/health` 没接。
- **provider 的「地址 / 模型」在界面上清不成空**：后端把"没传该字段"当成"别动它"，空串又会被前端挡掉（不送进请求体），所以留空 = 不改。要清空只能直接改库。T11 面板的小字里已写明。
- **T12 定下的三条新约定**（都只影响 P3 后续，别在 T13/T14 里放松）：① `proposal.kind` 新增取值 `material_judgment`（不加列、不用迁移），T14 的裁定界面按 kind 分流；② `profile_item.category` 的**约定词表**是 `life_habit` / `life_log` / `current_state` / `short_term_goal` / `long_axis`（库里是自由文本、无约束，目前只有 `long_axis` 真实用过；表外的类别不会丢，只是不算"缺失类别"）；③ 四问里**没给 profile_item id 又不说「依据不足」的输出一律判不合格**——这是"答案能指回具体字段"那条验收的兜底。
- **用户长期档案目前只有「长期主线」2 条**（id=1 已 superseded「旧主线：先把 Python 学完」，id=2 active「新主线：通用工程基础 + 能上线的项目」），另外四类全空（`GET /api/profile` 的 `missing_categories` 会照实报）。所以真实跑四问时 ②③④ 会得到「依据不足」——那是**正常现象、不是 bug**；要让四问答得实，得先补档案。
- **档案录入已补上（T22，2026-09-16）**：原「只有读、没有写」的缺口已结。写进 `category` 的值只收 `advisor.PROFILE_CATEGORIES` 那五个令牌（请求模型用 Literal 挡住，表外值 422）；取代与作废必填理由，全走台账。补入**防一字不差重复**（`af759fa`）：同类别同文本（含首尾空白差异）回 409 并指明已有条目 id；判重只看当前有效条目——作废后重填同样文字不挡，不同类别同文本不挡。四问要答得实，用 `/profile` 页把空着的那四类补上。
- **前端写 effect 会被 eslint 拦**：`react-hooks/set-state-in-effect` 禁止在 effect 体内**同步** setState。取数要写成 `.then(回调)` 里 setState（"订阅外部系统"的形态），或用 SWR/TanStack（加依赖属 Ask first）。本会话踩过一次。
- **写任何 `frontend/` 下的代码前，必须先读 `frontend/node_modules/next/dist/docs/` 里的对应指南**。`frontend/AGENTS.md` 由 `next dev` 自动生成并会自行重建（删了也会回来，提交它才能保持工作区干净），它明说 Next.js 16 相对训练数据有破坏性变更；已见实例：`app/layout.tsx` 用新的 `LayoutProps` 类型，而不是旧的 `children: React.ReactNode` 写法。至少读 `01-app/02-guides/upgrading/version-16.md` 与 `01-app/01-getting-started/06-fetching-data.md`。
- **只能操作「最新建的那个有效计划」（多计划未支持，待定）**：契约里**没有列出计划的接口**，前端只能拿 `GET /api/plan` 不传参数时的"最新建的那个 active 计划"。用户 2026-09-15 决定**先只补透明度**——`/new` 与计划表现在会写明「将建在 计划 #N 里」。**是否支持多计划待定**：若要，需加一条 `GET /api/plans`（改契约，SPEC 第 16 节 Ask first）。用户库里现有 3 个计划，另两个在界面里够不着。
- **框架生成的 `404` / `405` 文案仍是英文**（`Not Found` / `Method Not Allowed`）——形状已随方案 C 统一，只是文案没汉化；只会在手敲错 URL 时出现，前端调到不存在的端点时来自我们自己的 `raise`（中文）。
- `POST /api/report` 仍**没有**防重复保护（连点会落两条报告）；正解是 P2 前端提交后禁用按钮，不在后端做启发式判重。
- 台账「一列两维度」的根治方案 A（加 `record_state` 拆列 + 真库迁移）**未做**，触发条件见 SPEC 第 17 节第 2 条；走方案 A 前不要处理任何可能被 `void` 覆盖的历史行。
- `ledger.fetch_active` 的措辞与行为不符：它实际是「取处于初始业务状态的记录」（候选只返回 `proposed`、提案只返回 `pending`），不是「取当前有效」；`tools/show_db.py` 同样是有意的**原始视图**，不排除 `void` / `superseded`。两处行为没错，都是名字/视图会误导，未改。
- 到 P4 前需用户提供邮箱 SMTP 授权码（邮件提醒已定为启用）；用户那条留空的需求（原文「2、」后空白）默认不做，等其补。
- 本地库 `data/cadence.db` 有用户真实档案与手工验收数据（3 个计划、节点 1–7、报告 1–4、1 条 pending 提案）——**不得清库**。
- **SPEC 第 17 节新增第 3 条 Open Question（2026-09-16，用户提出并要求记录）**：档案提炼的执行者（agent 路线）——现在的链路靠用户手工提炼五类档案，提炼是全链路唯一没有质检的环节，提炼偏了 AI 判断跟着精准地跑偏；设想 P5 之后由 agent 直接读本地原始信息库、自动提炼五类档案（走 pending 提案 + 用户裁定），四问链路不改。成立前提（每条档案带来源指针 / 定期维护的成本模式 / 原始库只读接口、位置与形态未定）与「依赖 P5 先攒出判断质量基准」详见 SPEC 第 17 节第 3 条。不阻塞当前目标。
- 工作区三件未提交，均由用户决定，Agent 不擅自处理：① `AGENTS.md`（用户新增教学协议）；② `无标题-2026-09-14-2037.excalidraw`（仅清理 56 个 `isDeleted` 墓碑元素，已核实内容与基线一致）；③ `docs/代码串联图.excalidraw`（2026-09-15 重画到 P1 状态，结构自检 0 错 0 警，仍未进版本库）。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、视频进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——`plan` 没有（`closed` 是完成，不是否决），所以它是例外。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、阶段判定、周检查点判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422`、与现状冲突 `409`、业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- **P2 取数架构：浏览器直连**（SPEC 第 18 节第 26 条）。前端页面用客户端组件，`lib/api.ts` 在**浏览器**里 fetch 后端，因此**受 CORS 名单约束**（后端那层不是摆设）。不采用 Next 16 文档主推的服务端取数 + Server Action 路线，理由与代价见 SPEC 第 10 节。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端。
- LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 26 条见 `docs/SPEC.md` 第 18 节；除用户明确要求，不重新讨论。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。
- 台账拆列（方案 A）仅在 SPEC 第 17 节第 2 条的三个触发条件满足时才重新打开。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
|---|---|---|---|---|
| E-01～E-04、E-06、E-07、E-09～E-12、E-14～E-17、E-20 / 2026-09-14～15 | 已失效的旧证据（P0 台账 10 passed、早期接口探活、P1 78 / 103 / 115 passed、临时库闭环与 due_date 校验、T19 五种连发、smoke 9 步、用户在 `/docs` 亲手得到的 422、五条错误路径形状、T7 的 JSON 联通验证页） | 命令与脚本仍在仓库内，可复跑；联通用例的那一页已按计划被 T8 正式页面替换 | 均已失效：后端契约接连改动，且 E-20 验证的那个 JSON 页面已被 T8 页面取代 | 不要引用；后端结论现由 E-19 覆盖，前端现由 E-21、E-24 覆盖（E-22 于 T11 轮按自身条件失效）（E-14 里用户看到的 `422` 是**改动前的数组形状**，新形状须重看） |
| E-05 / 2026-09-15 | **用户亲手**在 `/docs` 操作（真 uvicorn + 自己的库）：建计划→建阶段→建 2 个检查点→3 次报告（含 1 次白点）→`GET /api/plan` | 数据留在用户本地库，可随时复查 | 通过：节点按状态机推进、落后量 5 天→0、产出 1 条 pending `stage_advance` 提案、`GET /api/plan` 七项字段与预测逐项吻合 | 基线 `7eee000`；本轮由 E-19 端到端复现，结论仍有效 |
| E-08 / 2026-09-15 | `python tools/show_db.py`（对用户真实库只读导出） | 脚本在仓库内，可复跑 | 通过：本轮重跑与已知事实逐项一致（3 计划 / plan 3 树 2-2 收尾 / 4 报告 / 台账带理由 / 1 pending 提案） | 纯只读，可反复跑；`app/plan.py` 变更后需重看 |
| E-13 / 2026-09-15 | 处置 Agent 误建的 `plan_node #7`：`plan.transition_node(7, "skipped", actor="agent")`，随后 `tools/show_db.py` 复核 | 用户库内留流水，可复查 | 通过：状态 `not_started → skipped`（理由「契约探测误建」写入台账）；计划 1 的「当前阶段」回到 `无`；计划 2、3 的树与 E-08 逐项一致，报告与提案未受影响 | 用户库数据可随时复查 |
| E-18 / 2026-09-15 | 官方 `create-next-app` 建 `frontend/`（Next.js 16.3.5 + React 19.2.8 + TS，`--empty` 无 Tailwind，`--disable-git`），npm 装 344 包；随后 `npm run dev` 起服务并用 HTTP 请求核对首页，另跑 `npm run lint` | 工程与依赖在仓库内（`node_modules`、`.next` 已被 `frontend/.gitignore` 排除，提交 11 个文件）；dev 服务已停、3000 端口已释放 | 通过：`next dev`（Turbopack）Ready in 311ms，`http://localhost:3000` 返回 **HTTP 200** 且页面含 `Hello world!`；`eslint` exit=0 | 基线 `d8f9e3c`；改动脚手架配置或依赖后失效。**只证明环境可跑，不证明任何前端功能** |
| E-19 / 2026-09-15 | 加完 CORS 后一次跑三样：`pytest -q`（**119 passed**——那是 T10 之前的数字，T10 之后见 E-23 的 147）、`python tools/smoke_p1.py`（9 步全绿）、临时库起 uvicorn 发 10 组真实请求——5 组跨源（合法 / `127.0.0.1` 写法 / 非法来源的预检与实际请求）+ 5 组带 `Origin` 的错误路径（`422`、`409`、`404`、框架生成的路由 `404`、`405`） | 临时脚本与临时库已删；`data/` 只剩 `cadence.db` | 通过：合法来源两种写法都回 `allow-origin`，**非法来源静默不放行**（无该头、预检 `400`）；**`422` 也带 `allow-origin`**，前端因此读得到那句中文；五条错误路径键集恒为 `{detail, errors}`、`detail` 恒为字符串 | **失效条件收窄**：仅当 `config.py` 的 CORS 名单、`main.py` 里那两个错误处理器、或 E-19 覆盖的那几条既有路由的响应形状变更时失效。**新增互不相关的路由不影响**（本轮加 6 条 provider 路由后本行仍有效）；只改 `tests/` 也不使结论失效 |
| E-21 / 2026-09-15 | 最简报告交互（T9 最小版）：`npm run lint` 与 `npx tsc --noEmit`——按 AGENTS.md 的验证纪律，前端只做这两项 | 工程内，可复跑 | 通过：两项均 exit=0。过程中 lint 拦下一次 `react-hooks/set-state-in-effect`（effect 体内同步调用了会 setState 的函数），已改为在 `.then` 回调里 setState。**浏览器手工走查未做**——按纪律归用户 | **按行为写**：仅当 `submitReport`、`/api/report` 契约或 `app/report/page.tsx` 的行为变更时失效；`lib/api.ts` 里**新增别的函数**不影响它 |
| E-22 / 2026-09-15 | T8 计划表页面 + 建计划 / 建节点页面：`npm run lint` 与 `npx tsc --noEmit`；另用三个普通 GET 确认 `/`、`/new`、`/report` 都返回 200 且含各自标题（不是浏览器验证） | 工程内，可复跑 | 通过：lint 与 tsc 均 exit=0，三个路由 HTTP 200。**浏览器手工走查未做**——按纪律归用户 | **按行为写**：仅当**新增页面/路由**或 lint/tsc 配置变更时失效；页面内的文案与展示调整只需重跑 lint 与 tsc（本会话后面补「将建在哪个计划」就是这样）。注意：客户端取数的文案不在首屏 HTML 里，用 GET 断言不到，别拿它当证据。**本轮（T11）它已按自己写的条件失效**——新增了 `/providers` 路由，`app/page.tsx` 也加了入口链接；全项目的 lint/tsc 现由 E-24 覆盖，T8 与 `/new` 两页各自的行为结论不变 |
| E-23 / 2026-09-15 | T10：`pytest -q`（**147 passed**：原 119 + 新增 22 条 llm 单测 + 6 条 provider 接口测试）与 `python tools/smoke_p1.py`（9 步全绿——本轮动了接口契约，按验证纪律要加跑） | 测试文件在仓库内，可复跑；临时库跑完自动删，已核实 `data/` 只剩 `cadence.db` | 通过：掩码只留末 4 位（短密钥全掩）、列表与错误信息里**都不含明文密钥**、每次调用落一行账（**失败也记**）、**第 4 次调用抛错中止**、连通性体检失败时返回结果而不抛异常 | 基线（本次提交）；`llm.py` 或那 6 条 provider / llm-calls 路由与请求模型变更后失效。**本轮（T12）它已按自己写的条件失效**——`llm.py` 被改（`post_json` 加请求头，见 E-25）；但四条结论未变：那 22 条 llm 单测与 6 条 provider 接口测试在 E-26 的 `pytest -q`（160 passed）里原样重跑通过 |
| E-24 / 2026-09-15 | T11：`npm run lint` 与 `npx tsc --noEmit`（按 AGENTS.md 的验证纪律，前端只做这两项） | 工程内，可复跑 | 通过：两项均 exit=0。**浏览器手工走查**：用户 2026-09-15 回报成功 | **按行为写**：仅当 `frontend/app/providers/page.tsx` 的行为、或 `lib/api.ts` 里那 5 个 provider 函数与 `Provider` / `ProviderPatch` 等类型变更时失效；首页那条入口链接改动不使其失效。后端那 6 条 provider 路由未变时（同 E-23 的条件）契约仍对得上 |
| E-25 / 2026-09-15～16 | **真实 provider 首次打通**：commandcode（`https://api.commandcode.ai/provider/v1` + `deepseek/deepseek-v4.1-flash`）体检回 `403 error code: 1010`。用**不带密钥**的对照实验定位（只测 Cloudflare 放不放行）：无 UA → `403 1010`；换成浏览器 UA → **commandcode 自己的 401**。据此给 `llm.post_json` 加 `User-Agent`（+ `Accept`） | 实验命令见第 6 节；Cloudflare 1010 的官方定义在其 1xxx 错误页（"banned your access based on your browser's signature"） | 通过：改后**同一条代码路径**拿到 `401 UNAUTHORIZED`（服务商自己的报错），证明已穿过 Cloudflare；**用户真密钥的体检结果仍待其回报** | `llm.post_json` 的请求头、或 provider 换到别家（换家可能要另加头）后失效。**注意**：这类"在门外被拦"的问题假 provider 永远测不出来——这正是"先接真 AI"的直接收益 |
| E-26 / 2026-09-16 | T12：`pytest -q`（**160 passed**：原 147 + 13 条 advisor 单测）与 `python tools\smoke_p1.py`（9 步全绿——本轮动了新契约与台账写入，按纪律加跑） | `backend/tests/test_advisor.py` 可复跑 | 通过：合法输出落一条 `pending` 提案且引用的档案 id 真实存在；**没给 id 又不说「依据不足」的漂亮话判为不合格**；引用不存在的 id 判为不合格；首次不合格→带原因重试一次→第二次合格则成功（恰好 2 次调用）；两次都不合格→抛 `AdvisorError` 且**不落任何提案**；无档案时一次模型都不调；`\`\`\`json` 代码块被容忍 | `advisor.py`、那两条新路由或 `RequestIn` 变更后失效。**只验了假上游**：真实模型的输出质量不在本证据范围 |
| E-27 / 2026-09-16 | **真实模型端到端跑通**（用户操作，Agent 只读库核对）：用户在 `/ask` 页提交「我要不要学python？」，真 provider = commandcode（id=3，`deepseek/deepseek-v4.1-flash`），`llm_call` 记 `task=judge`、ok=1、549 进 / 598 出 tokens、耗时 **5.5 秒**；`learning_request` 落 1 行；`proposal #3`（kind=`material_judgment`）status=`pending` | 数据在用户库 `data/cadence.db`，可随时复查：`llm_call`（按 task 分组：`connectivity_test` 5 次含 1 次成功、`judge` 1 次成功）、`proposal #3` 的 payload | 通过，且质量符合设计：① 引用了**真实存在的** `#2` 并复述其内容；②③ 同样指回 `#2` 并说明缺哪类；④ 明写「依据不足」且 `profile_item_ids` 为空数组。**「真密钥体检」同时得证**（那 1 次成功的 `connectivity_test`）——E-25 里「待用户回报」一条就此结清 | 用户库数据可复查。**证的是这一条链路**：`/api/requests` 契约、`advisor` 的 prompt 与校验、`llm.post_json` 的请求头、provider id=3 的配置；改任一处即失效。**未覆盖**：真实模型的输出质量是否稳定（只跑过 1 次） |
| E-28 / 2026-09-16 | 落盘提交：T12 后端 `f08f75b`（`advisor.py` / `test_advisor.py` / `main.py` / `llm.py`）与 T11+`/ask` 前端 `882cf69`（`providers/`、`ask/`、`api.ts`、`page.tsx`）；提交前复跑 `npm run lint` 与 `npx tsc --noEmit`（均 exit=0）、`pytest -q`（**160 passed**）；同轮用户回报「前端体验没有问题」（P2 页面浏览器走查） | 两笔提交在仓库内可查；lint / tsc / pytest 均可复跑 | 通过：提交时点前后端全绿；P2 走查通过 | lint/tsc 按行为写：页面行为或 lint/tsc 配置变更后失效；pytest 沿用 E-26 的失效条件（`advisor.py`、那两条路由、`RequestIn`、`llm.py` 变更后失效） |
| E-29 / 2026-09-16 | T22 档案录入：`pytest -q`（**177 passed**：原 160 + 17 条新单测）与 `tools\smoke_p1.py`（9 步全绿——本轮动了契约，按纪律加跑）；前端 `npm run lint` 与 `npx tsc --noEmit`（均 exit=0）。提交 `163f24e`（后端）与 `b09bc34`（前端） | `backend/tests/test_profile_write.py` 可复跑 | 通过：五令牌外的类别 422 拒收；同类别可多条并存；取代后旧值从 `GET /api/profile` 消失但台账留 before/after/理由；作废同理；历史行再动回 409；不存在回 404；纯空白 content/reason 回 400 | 那三条写路由与请求模型、或 `advisor.PROFILE_CATEGORIES` 变更后失效；`/profile` 页按行为写，浏览器走查归用户 |
| E-30 / 2026-09-16 | 档案补入防重复（用户走查发现：一字不差可反复补入）：`pytest -q`（**181 passed**：原 177 + 4 条防重复单测）与 `tools\smoke_p1.py`（9 步全绿——动了契约语义，按纪律加跑）。提交 `af759fa`；同轮用户回报 `/profile` 补档 + `/ask` 四问「验证通过」 | `backend/tests/test_profile_write.py` 可复跑 | 通过：同类别同文本（含首尾空白差异）回 409 并指明已有条目 id；不同类别同文本不挡；作废后重填同文本不挡（判重只看 active 条目） | `post_profile_item` 的判重逻辑变更后失效；其余同 E-29 的条件 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行）
.\.venv\Scripts\python.exe -m pytest -q           # 预期 160 passed
# 验「能不能穿过 Cloudflare」（用假密钥，只看放不放行，不碰真密钥——E-25 就是这个实验）：
#   .\.venv\Scripts\python.exe -c "from app import llm; print(llm.post_json('https://api.commandcode.ai/provider/v1/chat/completions', {'Authorization': 'Bearer dummy'}, llm._chat_payload('deepseek/deepseek-v4.1-flash', [{'role':'user','content':'ping'}]))[0])"
#   预期 401（服务商自己报的错）；若回 403 + error code: 1010，说明请求头又被门外拦了
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 9 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启
```

```powershell
cd D:\cadence\frontend
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
                   # 现有页面：/ 计划表 · /new 建计划与建节点 · /report 提交报告 · /providers LLM 提供商 · /ask 问一句（四问临时入口）
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000，各占一个终端窗口；本轮结束时两个端口都是**空闲**的。

**待用户执行的验收**：见第 3 节——现在只剩独立复核 `pytest -q`（预期 `160 passed`）与冒烟 9 步那一件；P2 走查与真实 provider 均已回报（E-28、E-27）。旧的「用 `due_date = 2026-14-15` 建节点应回 `422`」已在 E-19 验过，不必重做。

**热加载与停服务**：`--reload` 只监听 Python 文件（改 `sql/schema.sql` 不触发），且没装 `watchfiles`（走轮询）。**停服务别只杀父进程**：`--reload` 会派生父子两个进程，`job_kill` 也只杀 PowerShell 外壳；正确做法是 `Get-NetTCPConnection -LocalPort 8000 -State Listen`（前端换成 3000）找出 PID 再 `Stop-Process`。

| 任务类型 | 必读文件 |
|---|---|
| P1 回归 / 验收 | `backend/app/plan.py`、`backend/app/main.py`、`backend/app/ledger.py`、`backend/tools/`、`tasks/todo.md`（T4–T6、T19–T21） |
| **P2 前端（写代码前必读）** | `frontend/AGENTS.md`、`frontend/node_modules/next/dist/docs/` 里的 `upgrading/version-16.md` 与 `01-getting-started/06-fetching-data.md`；再叠上 `tasks/plan.md`（P2）与 `docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更 | `docs/SPEC.md` 第 10、11、17、18 节 |
| 需求背景 | `docs/SPEC.md` 第 1–9 节、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git、工作树与未提交改动。
2. 只读取当前目标路由的源码、测试与规则。
3. 证据沿用规则：**E-19**（后端既有路由的 CORS 与错误形状）在 `config.py` 的 CORS 名单、`main.py` 的两个错误处理器、或那几条既有路由的响应形状未变时可沿用——**新增互不相关的路由不影响它**；**E-23**（T10）因 T12 轮改了 `llm.py`（`post_json` 加请求头）已按自身条件失效，但它的四条结论由 E-26 的 `pytest -q` 重跑覆盖（`test_llm.py` 全部通过）；**E-24**（T11）在 `app/providers/page.tsx` 与 `lib/api.ts` 里那 5 个 provider 函数、`Provider*` 类型未变时可沿用；**E-25**（真实 provider 打通 1010）在 `llm.post_json` 的请求头与 provider 未变时可沿用；**E-26**（T12）在 `advisor.py`、`/api/requests`、`/api/profile`、`RequestIn` 未变时可沿用（但它**只验了假上游**，真实模型输出质量不在其范围）；**E-27**（真实模型跑通一次）在上述链路与 provider id=3 的配置未变时可沿用，但**只跑过 1 次**，不代表输出质量稳定；**E-21**（前端）按它行里"按行为写"的条件判断，`lib/api.ts` 里新增互不相关的函数**不**使其失效；**E-22** 因 T11 轮新增了 `/providers` 路由已按自身条件失效（T8 与 `/new` 两页的行为结论仍在），全项目 lint/tsc 改看 **E-24**；E-18 只证明前端环境可跑；E-08 纯只读可反复跑；E-13 记录用户库的一次数据处置，不受代码变更影响；其余（E-01～E-04、E-06、E-07、E-09～E-12、E-14～E-17、E-20）已失效，不要引用。
4. 用户已亲手走通 P1 闭环（E-05），但其本轮 `pytest` 与冒烟结果尚未回报；**开工任何新工作前，必须先报三句话计划并等确认**。
5. **探测一律指向临时库**：本会话曾把契约探测打到真实库上，误建了 `plan_node #7`（已按 T20 口径处置）。照 `tools/smoke_p1.py` 的做法起临时库 + 空闲端口，绝不拿 `data/cadence.db` 做实验。
6. **写 `frontend/` 下的代码前先读它自带的官方文档**（`frontend/node_modules/next/dist/docs/`）。Next.js 16 相对训练数据有破坏性变更，`frontend/AGENTS.md` 把「先读指南再写代码」写成了强制要求；凭记忆写很可能撞上这个版本已经换掉的 API。
7. **停服务别只杀父进程**：`job_kill` 只杀 PowerShell 外壳，`uvicorn` 与 `next dev` 都会留下子进程占着 8000 / 3000 端口（本会话两种都踩过）；按第 6 节的办法找 PID 再停。
8. 遵守项目 AGENTS.md 的教学协议：讲代码先给全景，分清现状与蓝图，每条知识配一个用户能亲手执行的动作，并让用户复述。
9. 每次提交 ≤200 行有效改动；加依赖与改契约属 Ask first；不因工作区缺少外部附件而推断附件从未提供，不把历史阻塞写回当前阻塞；不删除分支、工作树或用户数据，除非用户明确授权（`data/cadence.db` 里有用户真实档案与手工验收数据，不得清库）。**本文件每轮只更新一次、只做定点编辑，不整份重写**——整份重写会把全文重复计入上下文，本会话曾因此白烧约三万字符。
