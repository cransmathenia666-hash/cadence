# 任务清单 v0.2

配套：`docs/SPEC.md` · `tasks/plan.md`

按依赖顺序排列，不按重要性。每个任务能在一次专注里做完，都带验收与验证方式。

## P0 后端骨架与台账

- [ ] **T1 后端骨架与依赖**
  - Acceptance：`backend/.venv` 可用；`requirements.txt` 固定版本；FastAPI 能起来并返回 200；`/openapi.json` 可访问（它是前后端契约源）
  - Verify：`pip install -r requirements.txt` 后 `uvicorn app.main:app --port 8000`，打开 `http://127.0.0.1:8000/docs` 与实际接口一致
  - Files：`backend/requirements.txt`、`backend/app/main.py`、`.gitignore`

- [ ] **T2 建库与 schema**
  - Acceptance：`backend/sql/schema.sql` 覆盖这些表——`profile_item`（长期档案）、`ledger_event`（台账流水）、`learning_request`（每轮输入）、`candidate`（候选）、`plan`、`plan_node`（两级节点）、`report`（报告）、`proposal`（LLM 提案）、`notification_log`（触达记录）；`python -m app.db init` 可重复执行不报错
  - Verify：删掉 `data/cadence.db` 重新 init，`sqlite3 data/cadence.db ".tables"` 表齐全
  - Files：`backend/sql/schema.sql`、`backend/app/db.py`

- [ ] **T3 状态台账 `ledger.py`**
  - Acceptance：提供 `supersede` / `void` / `log_event` 与「取当前有效值」的查询；旧值只标记不删除；四类对象（档案、候选、会话决策、计划节点）都走同一入口
  - Verify：单测覆盖「取代后旧值不可见、但仍在流水里」「作废带 reason」「空值 / 重复取代不炸」
  - Files：`backend/app/ledger.py`、`backend/tests/test_ledger.py`

## P1 后端闭环逻辑

- [ ] **T4 计划节点状态机**
  - Acceptance：状态为 未开始 / 进行中 / 完成 / 卡住 / 跳过；只允许合法迁移；阶段节点在其全部周检查点完成后产出「是否进入下一阶段」的提案
  - Verify：单测覆盖合法与非法迁移、以及阶段完成时的提案产出
  - Files：`backend/app/plan.py`、`backend/tests/test_plan.py`

- [ ] **T5 报告 API 与落后量**
  - Acceptance：`POST /api/report` 接收状态四选一 + 一句话（必填）+ 产物链接、资料评价（可选）；`GET /api/plan` 返回计划、节点树、当前阶段、落后量；报告落库并经台账留痕
  - Verify：`/docs` 里手工提交一条「完成」报告，`GET /api/plan` 状态与落后量正确，`ledger_event` 有流水
  - Files：`backend/app/main.py`、`backend/app/plan.py`、`backend/tests/test_progress.py`

- [ ] **T6 周检查点判定**
  - Acceptance：能判定「本周检查点是否该触发」；落后时产出重排提案（减量 / 顺延 / 换交付物）
  - Verify：单测用固定时间桩覆盖按时 / 落后 / 无报告三种情况
  - Files：`backend/app/plan.py`、`backend/tests/test_progress.py`

## P2 前后端打通（第一个可验证切片）

- [ ] **T7 Next.js 脚手架与 API 客户端**
  - Acceptance：`frontend/` 用 TypeScript + App Router 建起来；后端加 CORS 只放行 `http://localhost:3000`；`lib/api.ts` 能调通 `GET /api/plan`
  - Verify：两个终端分别起 `uvicorn` 与 `npm run dev`，页面显示后端返回的 JSON，浏览器控制台无 CORS 报错
  - Files：`frontend/package.json`、`frontend/lib/api.ts`、`backend/app/main.py`

- [ ] **T8 计划表页面**
  - Acceptance：一个页面显示当前计划、节点状态、当前阶段、落后量；数据全部来自 `GET /api/plan`，前端不做任何业务计算
  - Verify：手工造几条节点数据，页面展示与数据库一致
  - Files：`frontend/app/page.tsx`、`frontend/components/`、`frontend/lib/api.ts`

- [ ] **T9 报告提交表单**
  - Acceptance：表单提交后走 `POST /api/report`，节点状态与台账随之更新；这就是闭环的第一段
  - Verify：从页面提交一条「完成」报告，后端状态、落后量、`ledger_event` 三者一致（成功标准 1 与 5）
  - Files：`frontend/app/report/page.tsx`、`frontend/lib/api.ts`

## P3 决策入口

- [ ] **T10 四问判断链路（LLM 提案）**
  - Acceptance：`POST /api/requests` 收「我发现了某个资料，要不要学」，产出四问答案，每条能指回 `profile_item` 的具体字段；LLM 输出先过 schema 校验，落成 `proposal`，**不直接写库**
  - Verify：用假 LLM provider 跑单测，验证「合法输出→落提案」「非法输出→如实报错」两条路径
  - Files：`backend/app/advisor.py`、`backend/app/llm.py`、`backend/tests/test_advisor.py`

- [ ] **T11 候选清单生成与去重**
  - Acceptance：输入「我不知道该学什么」，产出 3–5 条候选，每条带「为什么对你有用」与建议深度，带排序和一句「建议先从哪条开始」；已被否决的候选不再出现；「找」走 provider 接口，当前为不联网实现
  - Verify：单测覆盖候选数量约束、排序、去重（否决过的候选被过滤）；成功标准 2 的人工走查
  - Files：`backend/app/advisor.py`、`backend/app/providers/find.py`、`backend/tests/test_candidates.py`

- [ ] **T12 候选与提案的前端交互**
  - Acceptance：页面上能看候选、采纳或否决；能看待裁定提案并裁定（档案变更 / 计划重排）；否决结果落台账
  - Verify：否决一条候选后重新请求同类输入，该候选不再出现；裁定提案后档案与计划表按预期变化
  - Files：`frontend/app/candidates/page.tsx`、`frontend/app/proposals/page.tsx`、`frontend/lib/api.ts`

## P4 触达兜底

- [ ] **T13 `notify` 接口与邮件实现**
  - Acceptance：`notify` 为接口，含邮件实现与空实现（本地开发用）；内容组装包含「当前阶段 / 本周交付物推到哪 / 落后时建议怎么调」三问
  - Verify：空实现下内容组装有单测；邮件实现用 `--dry-run` 打印不发送
  - Files：`backend/app/notify.py`、`backend/tests/test_notify.py`

- [ ] **T14 周检查点 job**
  - Acceptance：`python -m app.jobs.weekly_checkpoint --dry-run` 输出正确三问；写 `notification_log`；启动时检查 `last_sent_at` 并补发错过的检查点
  - Verify：`--dry-run` 输出人工核对；补发逻辑用固定时间桩单测
  - Files：`backend/app/jobs/weekly_checkpoint.py`、`backend/tests/test_weekly.py`

## P5 验收

- [ ] **T15 两周试用与成功标准走查**
  - Acceptance：SPEC 第 9 节成功标准 1–5 逐条通过，或记录未通过项与原因
  - Verify：完成一次真实闭环（找 → 认同 → 计划 → 执行 → 报告 → 推进），并留存走查记录
  - Files：`docs/试用记录.md`
