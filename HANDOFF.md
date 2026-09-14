# cadence 交接文档

> 最近更新：2026-09-15 00:28（本地时区）
> 仓库根目录：`D:\cadence`
> 主工作树：`D:\cadence`｜`master`｜`8d995bd`｜无远端｜干净
> 其他工作树：无
> 当前唯一目标：完成 P1——后端闭环逻辑（计划节点状态机、报告 API 与落后量、周检查点判定）
> 下一条动作：新建 `backend/app/plan.py`，实现计划节点状态机与合法迁移校验，并补 `backend/tests/test_plan.py`

## 1. 当前状态

| 范围 | 状态 | 证据依据 | 有效范围/失效条件 |
|---|---|---|---|
| 后端 P0 地基（schema / db / ledger） | 通过 | 本轮 E-01、E-02 | 适用于 HEAD `8d995bd`；`schema.sql`、`db.py`、`ledger.py` 改动后失效 |
| 台账单测 | 通过 | 本轮 E-01 | `backend/tests/test_ledger.py` 改动后需重跑 |
| 服务启动与契约生成 | 通过 | 本轮 E-02 | `backend/app/main.py` 改动后失效 |
| P1 闭环逻辑（状态机、报告 API、落后量） | 未验证 | 未实现 | 无 |
| 前端 `frontend/` | 未验证 | 未实现 | 无 |
| 真实使用验收（`docs/SPEC.md` 第 9 节成功标准 1–5） | 未验证 | 未开始 | 需 P1–P4 完成后 |

## 2. 当前目标与完成定义

**目标：** 完成 P1——后端闭环逻辑可用。

**原因：** 整个产品的闭环能否成立，取决于「提交报告 → 节点状态推进 → 落后量可见」这条链。

**完成定义：** 能用 `/docs` 手工建一个计划与节点、提交一条报告，看到节点状态变化与落后量，且 `ledger_event` 留下对应流水。

**验收路径：** `cd D:\cadence\backend` → 起服务 → 在 `/docs` 建计划与两级节点 → `POST /api/report` → `GET /api/plan` 看到状态与落后量 → 查库确认 `ledger_event` 有流水。

**本轮不做：** 前端页面（P2）、LLM 与 provider 管理（P3）、触达与 Markdown 导出（P4）。

## 3. 当前开放问题

无阻塞问题。

候选队列（不影响当前目标）：

- 用户未写完的那条需求（原文写作「2、」后留空），默认不做，等用户补。
- 到 P3 前需用户提供：至少一家 LLM 提供商的名称、基址、密钥。
- 到 P4 前需用户提供：邮箱 SMTP 授权码，并确认是否启用邮件提醒。

## 4. 稳定边界与重新打开条件

- 本产品只做**方向层**：学习过程追踪（时长、视频进度、笔记、打卡、番茄钟）已明确排除，不得重新实现。
- 状态变更必须经 `backend/app/ledger.py`，不得直接 UPDATE 业务表；缺理由或对已作废记录动手，一律抛 `LedgerError`，不静默忽略。
- 前端不得持久化业务状态，不得直连数据库或 LLM；业务规则在后端算完再给前端。
- LLM 只产出结构化提案，写入必须经用户裁定。
- 已定决策共 18 条见 `docs/SPEC.md` 第 18 节；除用户明确要求，不重新讨论。
- `无标题-2026-09-14-2037.excalidraw` 是用户手绘的原始设计图，只读，不得删除或改写。

## 5. 证据记录

| 编号/日期 | 来源、操作与环境 | 留存/访问 | 结论 | 适用范围/失效条件 |
|---|---|---|---|---|
| E-01 / 2026-09-14 | `cd backend; .\.venv\Scripts\python.exe -m pytest -q`（Python 3.14.7） | 测试文件在仓库内，命令可复跑 | 通过：10 passed | 适用于 HEAD `8d995bd`；`ledger.py`、`schema.sql`、`test_ledger.py` 变更后失效 |
| E-02 / 2026-09-14 | `cd backend; python -m app.db init`，再起 uvicorn 并请求 `/api/health`、`/openapi.json` | 命令可复跑；产物 `data/cadence.db` 本地且不入版本库 | 通过：建库成功；health 返回 `{"status":"ok"}`；openapi title=cadence | 适用于 HEAD `8d995bd`；`db.py`、`main.py`、`schema.sql` 变更后失效 |

## 6. 启动、验收与上下文

```powershell
cd D:\cadence\backend
.\.venv\Scripts\python.exe -m app.db init
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

预期结果：第一条建库并打印路径；第二条 10 passed；第三条服务监听 8000，`http://127.0.0.1:8000/docs` 可打开。前置条件：无环境变量。长任务停止条件：不适用。

| 任务类型 | 必读文件 |
|---|---|
| 当前目标（P1） | `backend/app/ledger.py`、`backend/sql/schema.sql`、`tasks/todo.md`（T4–T6）、`tasks/plan.md`（P1） |
| 产品/架构变更 | `docs/SPEC.md`（第 10、11、18 节） |
| 需求背景 | `docs/SPEC.md` 第 1–9 节、`docs/U2-触达详解.md`、`docs/未决项讨论.md` |

## 7. 给下一个 Agent 的启动提示

1. 完整读取本文件并核对 Git、工作树与未提交改动。
2. 只读取当前目标路由的源码、测试与规则。
3. 先判断既有证据是否仍在有效范围，再决定是否运行最小相关验证；E-01/E-02 在 `ledger.py`、`schema.sql`、`db.py`、`main.py` 未变时可沿用。
4. 先执行「下一条动作」；没有新需求时不要制造目标。
5. 不因工作区缺少外部附件而推断附件从未提供，不把历史阻塞写回当前阻塞。
6. 不删除分支、工作树或用户数据，除非用户明确授权。
7. 完成后运行相称的验证并更新本文件。
