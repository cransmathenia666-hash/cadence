# cadence 交接文档

> 最近更新：2026-09-15 14:35（本地时区）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜后端代码基线 `fe4a5b8`、前端脚手架 `d8f9e3c`｜无远端｜未提交改动仅剩用户三件（见第 3 节）
> 其他工作树：无
> 当前唯一目标：P1（后端闭环）+ 四处 P1 补充**已完成**；**P2 的 T7 已完成前半**（前端环境就绪 + 后端 CORS），只差 `frontend/lib/api.ts`。下一步待用户指定
> 下一条动作：用户跑 `pytest -q`（预期 `119 passed`）与 `tools\smoke_p1.py`（预期 9 步全绿），并到 `/docs` 用 `due_date = 2026-14-15` 建节点看 `detail` 是否已是中文一句话；之后写 `frontend/lib/api.ts` 即完成 T7

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
| 前端 `frontend/`（仅环境） | 通过 | 本轮 E-18 | 脚手架、依赖或配置改动后失效；**业务代码未写**——T7 只剩 `lib/api.ts` |
| SPEC 第 9 节真实使用验收 | 未验证 | 成功标准 1、5 的后端部分已由 E-05、E-19 覆盖；2、3、4 与前端部分未做 | 需 P2–P4 完成后 |

## 2. 当前目标与完成定义

**目标：P1 及其补充——已完成。** 完成定义是「在 `/docs` 建计划与两级节点 → 提交报告 → 节点状态按状态机推进、落后量可见 → 台账留流水 → 阶段收尾产出推进提案」，已由用户亲手走通（E-05），本轮再由脚本端到端复现（E-19）。

**本轮补的三件事（都已完成）：** T20 把「台账生命周期」与「业务状态」的归属划清——台账的作废 / 取代只对 `profile_item` 与 `plan` 开放，节点 / 候选 / 提案用业务终态（`skipped` / `rejected`）表达「不再算数」，并对历史幽灵记录兜底过滤；T21 把建节点的 `due_date` 收成真日期类型，非法日期在边界返回 `422`；方案 C 把全部错误出口收成一个形状，`detail` 恒为中文一句话、`errors` 恒为数组。

**本轮不做 / 下一步：** 本轮没做前端业务代码（T8/T9）、LLM 与 provider 管理（P3）、触达与 Markdown 导出（P4）、台账拆列（方案 A，触发条件见 SPEC 第 17 节第 2 条）。按用户指示先不写前端业务代码，**T7 现在只剩一件**：写 `frontend/lib/api.ts`（先做 `getPlan()`，错误按方案 C 的形状处理）；再往后 T8 计划表页面、T9 报告提交表单。

**前端环境（本轮建好）：** `frontend/` 是官方 `create-next-app` 生成的最小工程——Next.js 16.3.5 + React 19.2.8 + TypeScript 5，App Router 且带 `--empty`（无示例内容），**不启用 Tailwind**（样式留到 T8），并用 `--disable-git` 避免嵌套 git 仓库。`npm run dev` 起得来、`npm run lint` 通过（E-18）。

## 3. 当前开放问题

无阻塞。**仅剩用户侧 `pytest` 与冒烟结果未回报**（按项目约定第 9 条，这两条命令由用户亲手跑）：`pytest -q`（预期 `119 passed`）、`tools\smoke_p1.py`（预期 9 步全绿）。另请用户重看一次 `/docs` 的错误文案——上次看到的 `422` 是**改动前的数组形状**，方案 C 落地后应变成中文一句话。

候选队列（不影响当前目标）：

- **T7 只剩一件事**：`frontend/lib/api.ts` 未写（后端 CORS 已随 `fe4a5b8` 落地）。用户明确「先配环境、只做 CORS」，所以写前端代码等他发话。
- **写任何 `frontend/` 下的代码前，必须先读 `frontend/node_modules/next/dist/docs/` 里的对应指南**。`frontend/AGENTS.md` 由 `next dev` 自动生成并会自行重建（删了也会回来，提交它才能保持工作区干净），它明说 Next.js 16 相对训练数据有破坏性变更；已见实例：`app/layout.tsx` 用新的 `LayoutProps` 类型，而不是旧的 `children: React.ReactNode` 写法。至少读 `01-app/02-guides/upgrading/version-16.md` 与 `01-app/01-getting-started/06-fetching-data.md`。
- npm 12 的 install-scripts 策略拦下了 `unrs-resolver` 的 postinstall；实测**不影响 lint**（exit=0），暂不处理，若将来 ESLint 报模块解析错误再回头批准。
- **框架生成的 `404` / `405` 文案仍是英文**（`Not Found` / `Method Not Allowed`）——形状已随方案 C 统一，只是文案没汉化；只会在手敲错 URL 时出现，前端调到不存在的端点时来自我们自己的 `raise`（中文）。
- `POST /api/report` 仍**没有**防重复保护（连点会落两条报告）；正解是 P2 前端提交后禁用按钮，不在后端做启发式判重。
- 台账「一列两维度」的根治方案 A（加 `record_state` 拆列 + 真库迁移）**未做**，触发条件见 SPEC 第 17 节第 2 条；走方案 A 前不要处理任何可能被 `void` 覆盖的历史行。
- `ledger.fetch_active` 的措辞与行为不符：它实际是「取处于初始业务状态的记录」（候选只返回 `proposed`、提案只返回 `pending`），不是「取当前有效」；`tools/show_db.py` 同样是有意的**原始视图**，不排除 `void` / `superseded`。两处行为没错，都是名字/视图会误导，未改。
- 到 P4 前需用户提供邮箱 SMTP 授权码（邮件提醒已定为启用）；用户那条留空的需求（原文「2、」后空白）默认不做，等其补。
- 本地库 `data/cadence.db` 有用户真实档案与手工验收数据（3 个计划、节点 1–7、报告 1–4、1 条 pending 提案）——**不得清库**。
- 工作区三件未提交，均由用户决定，Agent 不擅自处理：① `AGENTS.md`（用户新增教学协议）；② `无标题-2026-09-14-2037.excalidraw`（仅清理 56 个 `isDeleted` 墓碑元素，已核实内容与基线一致）；③ `docs/代码串联图.excalidraw`（2026-09-15 重画到 P1 状态，结构自检 0 错 0 警，仍未进版本库）。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、视频进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- **台账的作废 / 取代只对 `profile_item` 与 `plan` 开放**（SPEC 第 18 节第 22 条）。节点 / 候选 / 提案用业务终态：节点 `skipped`、候选与提案 `rejected`。判据是「它有没有表达否决的业务终态」——`plan` 没有（`closed` 是完成，不是否决），所以它是例外。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、阶段判定、周检查点判定、防重复）；接口层只翻译 HTTP 状态码。状态码口径：参数不合法 `422`、与现状冲突 `409`、业务规则拒绝 `400`。
- 错误响应统一为 `{"detail": 中文一句话, "errors": 数组}`（SPEC 第 18 节第 24 条）；前端只按这一种形状处理。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端。
- LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 25 条见 `docs/SPEC.md` 第 18 节；除用户明确要求，不重新讨论。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。
- 台账拆列（方案 A）仅在 SPEC 第 17 节第 2 条的三个触发条件满足时才重新打开。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
|---|---|---|---|---|
| E-01～E-04、E-06、E-07、E-09～E-12、E-14～E-17 / 2026-09-14～15 | 已失效的旧证据（P0 台账 10 passed、早期接口探活、P1 78 / 103 / 115 passed、临时库闭环与 due_date 校验、T19 五种连发、smoke 9 步、用户在 `/docs` 亲手得到的 422、五条错误路径形状） | 命令与脚本仍在仓库内，可复跑 | 均已失效：`ledger.py`、`plan.py`、`main.py`、`config.py` 与接口契约接连改动，触发了它们各自的失效条件 | 不要引用；对应结论现由 E-19 覆盖（E-14 里用户看到的 `422` 是**改动前的数组形状**，新形状须重看） |
| E-05 / 2026-09-15 | **用户亲手**在 `/docs` 操作（真 uvicorn + 自己的库）：建计划→建阶段→建 2 个检查点→3 次报告（含 1 次白点）→`GET /api/plan` | 数据留在用户本地库，可随时复查 | 通过：节点按状态机推进、落后量 5 天→0、产出 1 条 pending `stage_advance` 提案、`GET /api/plan` 七项字段与预测逐项吻合 | 基线 `7eee000`；本轮由 E-19 端到端复现，结论仍有效 |
| E-08 / 2026-09-15 | `python tools/show_db.py`（对用户真实库只读导出） | 脚本在仓库内，可复跑 | 通过：本轮重跑与已知事实逐项一致（3 计划 / plan 3 树 2-2 收尾 / 4 报告 / 台账带理由 / 1 pending 提案） | 纯只读，可反复跑；`app/plan.py` 变更后需重看 |
| E-13 / 2026-09-15 | 处置 Agent 误建的 `plan_node #7`：`plan.transition_node(7, "skipped", actor="agent")`，随后 `tools/show_db.py` 复核 | 用户库内留流水，可复查 | 通过：状态 `not_started → skipped`（理由「契约探测误建」写入台账）；计划 1 的「当前阶段」回到 `无`；计划 2、3 的树与 E-08 逐项一致，报告与提案未受影响 | 用户库数据可随时复查 |
| E-18 / 2026-09-15 | 官方 `create-next-app` 建 `frontend/`（Next.js 16.3.5 + React 19.2.8 + TS，`--empty` 无 Tailwind，`--disable-git`），npm 装 344 包；随后 `npm run dev` 起服务并用 HTTP 请求核对首页，另跑 `npm run lint` | 工程与依赖在仓库内（`node_modules`、`.next` 已被 `frontend/.gitignore` 排除，提交 11 个文件）；dev 服务已停、3000 端口已释放 | 通过：`next dev`（Turbopack）Ready in 311ms，`http://localhost:3000` 返回 **HTTP 200** 且页面含 `Hello world!`；`eslint` exit=0 | 基线 `d8f9e3c`；改动脚手架配置或依赖后失效。**只证明环境可跑，不证明任何前端功能** |
| E-19 / 2026-09-15 | 加完 CORS 后一次跑三样：`pytest -q`（**119 passed**）、`python tools/smoke_p1.py`（9 步全绿）、临时库起 uvicorn 发 10 组真实请求——5 组跨源（合法 / `127.0.0.1` 写法 / 非法来源的预检与实际请求）+ 5 组带 `Origin` 的错误路径（`422`、`409`、`404`、框架生成的路由 `404`、`405`） | 临时脚本与临时库已删；`data/` 只剩 `cadence.db` | 通过：合法来源两种写法都回 `allow-origin`，**非法来源静默不放行**（无该头、预检 `400`）；**`422` 也带 `allow-origin`**，前端因此读得到那句中文；五条错误路径键集恒为 `{detail, errors}`、`detail` 恒为字符串 | 基线 `fe4a5b8`；`plan.py`、`main.py`、`config.py`、`tests/` 或接口契约变更后失效 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init         # 建库（可重复执行）
.\.venv\Scripts\python.exe -m pytest -q           # 预期 119 passed
.\.venv\Scripts\python.exe tools\show_db.py       # 只读看库：计划树 / 报告 / 台账 / 提案
.\.venv\Scripts\python.exe tools\smoke_p1.py      # 闭环冒烟：自起临时库跑 9 步，不动真实数据
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # 开发用：改代码自动重启
```

```powershell
cd D:\cadence\frontend
npm run dev        # 开发服务，默认 http://localhost:3000（Turbopack，Ready 约 0.3 秒）
npm run lint       # ESLint，当前 exit=0
```

**端口与服务**：后端 8000、前端 3000，各占一个终端窗口；本轮结束时两个端口都是**空闲**的。

**待用户执行的验收**：先起后端，跑 `pytest -q` 预期 `119 passed`，再到 `/docs` 用 `due_date = 2026-14-15` 建节点——预期 `422`，且 `detail` 是**中文一句话**（形如「参数不合法：到期日不是有效日期（要零填充的 ISO 日期，如 2026-09-30）」），旁边多一个 `errors` 数组（含 `loc`）。这一步是在看**改动后的新形状**：你上次看到的是旧的数组形状。

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
3. 证据沿用规则：E-19 在 `ledger.py` / `plan.py` / `main.py` / `config.py` / `tests/` 未变时可沿用；E-18 只证明前端环境可跑（不代表任何前端功能），脚手架或依赖变了就失效；E-08 纯只读可反复跑；E-05 的结论由 E-19 复现；E-13 记录用户库的一次数据处置，不受代码变更影响；E-01～E-04、E-06、E-07、E-09～E-12、E-14～E-17 已失效，不要引用。
4. 用户已亲手走通 P1 闭环（E-05），但其本轮 `pytest` 与冒烟结果尚未回报；**开工任何新工作前，必须先报三句话计划并等确认**。
5. **探测一律指向临时库**：本会话曾把契约探测打到真实库上，误建了 `plan_node #7`（已按 T20 口径处置）。照 `tools/smoke_p1.py` 的做法起临时库 + 空闲端口，绝不拿 `data/cadence.db` 做实验。
6. **写 `frontend/` 下的代码前先读它自带的官方文档**（`frontend/node_modules/next/dist/docs/`）。Next.js 16 相对训练数据有破坏性变更，`frontend/AGENTS.md` 把「先读指南再写代码」写成了强制要求；凭记忆写很可能撞上这个版本已经换掉的 API。
7. **停服务别只杀父进程**：`job_kill` 只杀 PowerShell 外壳，`uvicorn` 与 `next dev` 都会留下子进程占着 8000 / 3000 端口（本会话两种都踩过）；按第 6 节的办法找 PID 再停。
8. 遵守项目 AGENTS.md 的教学协议：讲代码先给全景，分清现状与蓝图，每条知识配一个用户能亲手执行的动作，并让用户复述。
9. 每次提交 ≤200 行有效改动；加依赖与改契约属 Ask first；不因工作区缺少外部附件而推断附件从未提供，不把历史阻塞写回当前阻塞；不删除分支、工作树或用户数据，除非用户明确授权（`data/cadence.db` 里有用户真实档案与手工验收数据，不得清库）。
