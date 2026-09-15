# cadence 交接文档

> 最近更新：2026-09-15 11:55（本地时区）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜`2904831`（本交接文档随后提交）｜无远端｜有未提交改动（见第 3 节）
> 代码基线：`2904831`（P1 代码，含 T19 防重复提交）
> 其他工作树：无
> 当前唯一目标：P1 已实现并由用户亲手验收；**下一步等用户在 B（自查工具）与 C（P2 前端）之间选**
> 下一条动作：用户跑 `pytest -q`（预期 `88 passed`）并在 `/docs` 复核"双击第二次得 409"，然后选 B 或 C

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
|---|---|---|---|
| 后端 P0 地基（schema / ledger） | 通过 | 沿用 E-01 | `ledger.py`、`schema.sql` 改动后失效（P1 未动这两个文件） |
| 台账单测 | 通过 | 沿用 E-01 | `backend/tests/test_ledger.py` 改动后需重跑 |
| P1 计划节点状态机（T4） | 通过 | 本轮 E-03 | `backend/app/plan.py` 改动后失效 |
| P1 报告 API 与落后量（T5） | 通过 | 本轮 E-03、E-04 | `plan.py`、`main.py` 改动后失效 |
| P1 周检查点判定（T6） | 通过 | 本轮 E-03 | **只有单测证据**：未接 API、未接定时任务（P4 才接） |
| 服务启动与契约生成 | 通过 | 本轮 E-04（取代已失效的 E-02） | `main.py` 改动后失效 |
| 前端 `frontend/` | 未验证 | 未实现 | 无 |
| P1 闭环端到端（用户亲手在 `/docs` 走通） | 通过 | 本轮 E-05 | `plan.py`、`main.py` 改动后失效 |
| 建节点防重复提交（T19） | 通过 | 本轮 E-06、E-07 | `plan.py`、`main.py` 改动后失效 |
| 真实使用验收（`docs/SPEC.md` 第 9 节） | 未验证 | 成功标准 1 与 5 的**后端部分**已由 E-04、E-05 覆盖；2、3、4 及前端部分未做 | 需 P2–P4 完成后 |

注：T19 改动了 `plan.py` 与 `main.py`，上表中以这两个文件为失效条件的证据，其代码基线已前进；T19 之后已重跑单测与 HTTP 冒烟（E-06、E-07），用户完整闭环（E-05）如需在 T19 之后复核，请重跑第 6 节脚本。

## 2. 当前目标与完成定义

**P1（后端闭环）已实现，并由用户亲手在自己的库走通**：在 `/docs` 里建计划与两级节点 → 提交报告 → 节点状态按状态机推进、落后量可见 → 台账留流水 → 阶段收尾产出推进提案（E-05）。可复跑的验收命令在第 6 节。

**本轮不做：** 前端页面（P2）、LLM 与 provider 管理（P3）、触达与 Markdown 导出（P4）。

## 3. 当前开放问题

无阻塞问题。**仅剩：用户回报 `pytest -q`（预期 `88 passed`）并复核"双击第二次得 409"**（项目约定第 9 条：验收命令由用户亲手跑）。

候选队列（不影响当前目标）：

- `POST /api/report` 仍**没有**防重复保护（连点会落两条报告）。正解是 P2 前端提交后禁用按钮，不在后端做启发式判重。
- 本地库 `data/cadence.db` 有用户手工验收的演示数据（计划 1/2/3、节点 1–6、报告 1–4、1 条 pending 提案）——**不得清库**；曾提议但未获授权的 `backend/tools/show_db.py`、`backend/tools/smoke_p1.py` 仍待用户点头。

- 用户未写完的那条需求（原文写作「2、」后留空），默认不做，等用户补。
- 到 P4 前需用户提供：邮箱 SMTP 授权码（邮件提醒已定为启用）。
- 提交或留在工作区由用户决定，Agent 不擅自处理：① `AGENTS.md` 被用户新增「教学协议 + 开发配合义务」（未提交）；② `无标题-2026-09-14-2037.excalidraw` 被改写（已核实仅清理 56 个 `isDeleted` 墓碑元素，元素与文字与基线一致，无内容丢失）；③ 未跟踪新文件 `docs/代码串联图.excalidraw`。
- P1 新增了两个接口（`POST /api/plan`、`POST /api/plan/nodes`），已补进 SPEC 第 11 节契约表；SPEC 第 16 节把「改契约」列为 Ask first，**需用户确认是否接受这两个接口**。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、视频进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- 业务规则集中在 `backend/app/plan.py`（状态机、落后量、阶段判定、周检查点判定），接口层只翻译 HTTP 状态码。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端。
- LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 21 条见 `docs/SPEC.md` 第 18 节；除用户明确要求，不重新讨论。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
|---|---|---|---|---|
| E-01 / 2026-09-14 | `cd backend; .\.venv\Scripts\python.exe -m pytest -q`（Python 3.14.7） | 测试文件在仓库内，命令可复跑 | 通过：10 passed（P0 台账） | 适用于 `ledger.py`、`schema.sql`、`test_ledger.py`；三者变更后失效（P1 未变，故仍有效） |
| E-02 / 2026-09-14 | 建库 + 起 uvicorn + 请求 `/api/health`、`/openapi.json` | 命令可复跑 | 通过（当时） | **已失效**：`main.py` 在 P1 被重写，由 E-04 取代 |
| E-03 / 2026-09-15 | `cd backend; .\.venv\Scripts\python.exe -m pytest -q` | 测试文件在仓库内，命令可复跑 | 通过：78 passed | 适用于 HEAD `7eee000`；`backend/` 下代码或测试变更后失效 |
| E-04 / 2026-09-15 | 临时库起 uvicorn（`127.0.0.1:8099`），用 Invoke-RestMethod 走完建计划→建阶段→建 2 个检查点→提交 3 条报告→`GET /api/plan`→查库 | 临时库与临时脚本已删除；命令见第 6 节，可复跑 | 通过：进行中落后量 5 天、全部收尾后 0 且 `behind=false`、节点级仍留 5 天；非法迁移 400；缺 `parent_id` 400；`ledger_event` 含 create 与 status_change（before/after）；`stage_advance` 提案落库为 pending | 适用于 HEAD `7eee000` 的后端；`plan.py`、`main.py` 变更后失效 |
| E-05 / 2026-09-15 | **用户亲手**在 `/docs` 操作（真 uvicorn + 自己的 `data/cadence.db`）：建计划→建阶段→建 2 个检查点→3 次报告（含 1 次白点）→`GET /api/plan` | 数据留在用户本地库，可随时复查 | 通过：节点按状态机推进、落后量 5 天→0、阶段收尾产出 1 条 pending `stage_advance` 提案、`GET /api/plan` 七项字段与预测逐项吻合 | 适用于当时的 `7eee000`；`plan.py`、`main.py` 已在 T19 后变更 |
| E-06 / 2026-09-15 | T19 后重跑 `cd backend; .\.venv\Scripts\python.exe -m pytest -q` | 测试文件在仓库内，命令可复跑 | 通过：88 passed | 适用于 HEAD `2904831`；`backend/` 下代码或测试变更后失效 |
| E-07 / 2026-09-15 | T19 后临时库起 uvicorn（`127.0.0.1:8099`）+ Invoke-RestMethod：建计划/阶段后对同名检查点连点两次、带空格标题、换标题、已收尾后重建同名、同名阶段 | 临时库已删除；复跑方式：按第 6 节脚本建好计划与阶段后，对同一 `POST /api/plan/nodes` 连发两次 | 通过：第二次 409（带中文原因）；空格标题同样 409；换标题 201；已收尾后重建同名 201；同名阶段 409 | 适用于 HEAD `2904831`；`plan.py`、`main.py` 变更后失效 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

预期：第一条建库并打印路径；第二条 `78 passed`；第三条监听 8000，`http://127.0.0.1:8000/docs` 可打开。

**验收命令（请用户亲手跑，对应 E-03/E-04）：**

```powershell
# 1) 单测
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m pytest -q

# 2) 起服务（另开一个终端保持运行）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 3) 走一次闭环（第三个终端；中文用 UTF-8 字节，避免编码问题）
$base = "http://127.0.0.1:8000"
function Post-Json($url, $payload) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Compress -Depth 5))
    Invoke-RestMethod $url -Method Post -ContentType "application/json; charset=utf-8" -Body $bytes
}
$plan  = Post-Json "$base/api/plan" @{ goal = "我的第一个计划" }
$stage = Post-Json "$base/api/plan/nodes" @{ plan_id = $plan.id; level = "stage"; title = "阶段 1"; deliverable = "一个能访问的地址"; sort_order = 10 }
$cp    = Post-Json "$base/api/plan/nodes" @{ plan_id = $plan.id; parent_id = $stage.id; level = "checkpoint"; title = "检查点 1"; due_date = "2026-09-10" }
Invoke-RestMethod "$base/api/plan"                       # 看当前阶段与落后量（应为 5 天）
Post-Json "$base/api/report" @{ node_id = $cp.id; status = "done"; note = "做完了" }
Invoke-RestMethod "$base/api/plan"                       # 落后量应回到 0，并产出推进提案
```

| 任务类型 | 必读文件 |
|---|---|
| 当前目标（P1 验收） | `backend/app/plan.py`、`backend/app/main.py`、`tasks/todo.md`（T4–T6） |
| 下一步（P2 前端） | `tasks/plan.md`（P2）、`docs/SPEC.md` 第 10、11 节 |
| 产品/架构变更 | `docs/SPEC.md` 第 10、11、18 节 |
| 需求背景 | `docs/SPEC.md` 第 1–9 节、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git、工作树与未提交改动。
2. 只读取当前目标路由的源码、测试与规则。
3. 先判断既有证据是否仍在有效范围：E-01 在 `ledger.py`/`schema.sql`/`test_ledger.py` 未变时可沿用；E-06/E-07 在 `plan.py`/`main.py` 未变时可沿用；E-02 已失效、E-03/E-04/E-05 已被 T19 越过的代码基线，不要直接引用。
4. 用户已亲手走通 P1 闭环（E-05），但仍需其回报 `pytest` 结果；**开工 P2 或任何新工作前，必须先报三句话计划并等确认**。
5. 遵守项目 AGENTS.md 的教学协议：讲代码先给全景（怎么跑起来、请求怎么流、数据谁持有），分清现状与蓝图，每条知识配一个用户能亲手执行的动作，并让用户复述。
6. 新工作开工前先用三句话报计划（动哪几个文件、什么顺序、怎么算验证过）等用户确认；每次提交 ≤200 行有效改动。
7. 不因工作区缺少外部附件而推断附件从未提供，不把历史阻塞写回当前阻塞；不删除分支、工作树或用户数据，除非用户明确授权（`data/cadence.db` 里有用户真实档案与手工验收数据，不得清库）。
