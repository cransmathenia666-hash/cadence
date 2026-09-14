# 任务清单 v0.1

配套：`docs/SPEC.md` · `tasks/plan.md`

按依赖顺序排列，不按重要性。每个任务能在一次专注里做完，都带验收与验证方式。

## P0 骨架与台账

- [ ] **T1 仓库与依赖骨架**
  - Acceptance：`.venv` 可用；`requirements.txt` 固定版本；FastAPI 能起来并返回一个页面
  - Verify：`pip install -r requirements.txt` 后 `uvicorn app.main:app --port 8000`，浏览器打开返回 200
  - Files：`requirements.txt`、`app/main.py`、`.gitignore`

- [ ] **T2 建库与 schema**
  - Acceptance：`sql/schema.sql` 覆盖这些表——`profile_item`（长期档案）、`ledger_event`（台账流水）、`learning_request`（每轮输入）、`candidate`（候选）、`plan`、`plan_node`（两级节点）、`report`（报告）、`proposal`（LLM 提案）、`notification_log`（触达记录）；`python -m app.db init` 可重复执行不报错
  - Verify：删掉 `data/cadence.db` 重新 init，`sqlite3 data/cadence.db ".tables"` 表齐全
  - Files：`sql/schema.sql`、`app/db.py`

- [ ] **T3 状态台账 `ledger.py`**
  - Acceptance：提供 `supersede` / `void` / `log_event` 与「取当前有效值」的查询；旧值只标记不删除；四类对象（档案、候选、会话决策、计划节点）都走同一入口
  - Verify：单测覆盖「取代后旧值不可见、但仍在流水里」「作废带 reason」「空值/重复取代不炸」
  - Files：`app/ledger.py`、`tests/test_ledger.py`

## P1 计划表与报告闭环

- [ ] **T4 计划节点状态机**
  - Acceptance：状态为 未开始/进行中/完成/卡住/跳过；只允许合法迁移；阶段节点在其全部周检查点完成后产出「是否进入下一阶段」的提案
  - Verify：单测覆盖合法与非法迁移、以及阶段完成时的提案产出
  - Files：`app/plan.py`、`tests/test_plan.py`

- [ ] **T5 计划表页面与报告入口**
  - Acceptance：一个页面显示当前计划、节点状态、当前阶段；能提交报告——状态四选一 + 一句话（必填）+ 产物链接、资料评价（可选）
  - Verify：手工建一个计划，提交一条「完成」报告，页面与库中状态一致，`ledger_event` 有流水
  - Files：`app/main.py`、`app/templates/`、`app/templates/report.html`

- [ ] **T6 落后量计算与检查点判定**
  - Acceptance：能算出「计划时间 vs 最后一条报告」的落后量；能判定「本周检查点是否该触发」；落后时产出重排提案
  - Verify：单测用固定时间桩覆盖按时 / 落后 / 无报告三种情况
  - Files：`app/plan.py`、`tests/test_progress.py`

## P2 决策入口（可与 P3 并行）

- [ ] **T7 四问判断链路（LLM 提案）**
  - Acceptance：输入「我发现了某个资料，要不要学」，产出四问答案，每条能指回 `profile_item` 的具体字段；LLM 输出先过 schema 校验，落成 `proposal`，**不直接写库**
  - Verify：用假 LLM provider 跑单测，验证「合法输出→落提案」「非法输出→如实报错」两条路径
  - Files：`app/advisor.py`、`app/llm.py`、`tests/test_advisor.py`

- [ ] **T8 候选清单生成与去重**
  - Acceptance：输入「我不知道该学什么」，产出 3–5 条候选，每条带「为什么对你有用」与建议深度，带排序和一句「建议先从哪条开始」；已被否决的候选不再出现；「找」走 provider 接口，当前为不联网实现
  - Verify：单测覆盖候选数量约束、排序、去重（否决过的候选被过滤）；成功标准 2 的人工走查
  - Files：`app/advisor.py`、`app/providers/find.py`、`tests/test_candidates.py`

## P3 触达兜底

- [ ] **T9 `notify` 接口与邮件实现**
  - Acceptance：`notify` 为接口，含邮件实现与空实现（本地开发用）；内容组装包含「当前阶段 / 本周交付物推到哪 / 落后时建议怎么调」三问
  - Verify：空实现下内容组装有单测；邮件实现用 `--dry-run` 打印不发送
  - Files：`app/notify.py`、`tests/test_notify.py`

- [ ] **T10 周检查点 job**
  - Acceptance：`python -m app.jobs.weekly_checkpoint --dry-run` 输出正确三问；写 `notification_log`；启动时检查 `last_sent_at` 并补发错过的检查点
  - Verify：`--dry-run` 输出人工核对；补发逻辑用固定时间桩单测
  - Files：`app/jobs/weekly_checkpoint.py`、`tests/test_weekly.py`

## P4 验收

- [ ] **T11 两周试用与成功标准走查**
  - Acceptance：SPEC 第 9 节成功标准 1–5 逐条通过，或记录未通过项与原因
  - Verify：完成一次真实闭环（找 → 认同 → 计划 → 执行 → 报告 → 推进），并留存走查记录
  - Files：`docs/试用记录.md`
