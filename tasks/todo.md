# 任务清单 v0.6

配套：`docs/SPEC.md` · `tasks/plan.md`

按依赖顺序排列，不按重要性。每个任务能在一次专注里做完，都带验收与验证方式。

**v0.7 变更（2026-09-17）**：新增 P3.5 一节（排在 P4 之前）——T23 任务层与交付物验收（三级结构，**取代 T4 定下的旧完成判定**：阶段完成＝周检查点全部收尾 → 任务全打勾/跳过 + 交付物已提交）、T24 多计划与严格分开、T25 「找」的加宽、T26 对话式规划与蓝图。对应 SPEC 决策 30–36。

**v0.6 变更**：P3 补 T22（档案录入）——档案此前只有读、没有写入口，四问缺判据；用户 2026-09-16 点名要做，任务补记进清单。

**v0.5 变更**：P1 补充 T20（台账语义归属，方案 B）与 T21（建节点入参校验），均为已完成的后端修补。
**v0.4 变更**：P4 新增 T17 Markdown 导出；验收任务顺延为 T18。
**v0.3 变更**：P3 开头插入 LLM provider 管理与调用记账两个任务（T10、T11），后续任务顺延编号。

## P0 后端骨架与台账

- [x] **T1 后端骨架与依赖**
  - Acceptance：`backend/.venv` 可用；`requirements.txt` 固定版本；FastAPI 能起来并返回 200；`/openapi.json` 可访问（它是前后端契约源）
  - Verify：`pip install -r requirements.txt` 后 `uvicorn app.main:app --port 8000`，打开 `http://127.0.0.1:8000/docs` 与实际接口一致
  - Files：`backend/requirements.txt`、`backend/app/main.py`、`.gitignore`

- [x] **T2 建库与 schema**
  - Acceptance：`backend/sql/schema.sql` 覆盖这些表——`profile_item`（长期档案）、`ledger_event`（台账流水）、`learning_request`（每轮输入）、`candidate`（候选）、`plan`、`plan_node`（两级节点）、`report`（报告）、`proposal`（LLM 提案）、`notification_log`（触达记录）、`llm_provider`（提供商配置）、`llm_call`（调用记账）、`task_model_map`（任务类型 → provider + 模型）；`python -m app.db init` 可重复执行不报错
  - Verify：删掉 `data/cadence.db` 重新 init，`sqlite3 data/cadence.db ".tables"` 表齐全
  - Files：`backend/sql/schema.sql`、`backend/app/db.py`

- [x] **T3 状态台账 `ledger.py`**
  - Acceptance：提供 `supersede` / `void` / `log_event` 与「取当前有效值」的查询；旧值只标记不删除；四类对象（档案、候选、会话决策、计划节点）都走同一入口
  - Verify：单测覆盖「取代后旧值不可见、但仍在流水里」「作废带 reason」「空值 / 重复取代不炸」
  - Files：`backend/app/ledger.py`、`backend/tests/test_ledger.py`

## P1 后端闭环逻辑

- [x] **T4 计划节点状态机**
  - Acceptance：状态为 未开始 / 进行中 / 完成 / 卡住 / 跳过；只允许合法迁移；阶段节点在其全部周检查点完成后产出「是否进入下一阶段」的提案
  - Verify：单测覆盖合法与非法迁移、以及阶段完成时的提案产出
  - Files：`backend/app/plan.py`、`backend/tests/test_plan.py`

- [x] **T5 报告 API 与落后量**
  - Acceptance：`POST /api/report` 接收状态四选一 + 一句话（必填）+ 产物链接、资料评价（可选）；`GET /api/plan` 返回计划、节点树、当前阶段、落后量；报告落库并经台账留痕
  - Verify：`/docs` 里手工提交一条「完成」报告，`GET /api/plan` 状态与落后量正确，`ledger_event` 有流水
  - Files：`backend/app/main.py`、`backend/app/plan.py`、`backend/tests/test_progress.py`

- [x] **T6 周检查点判定**
  - Acceptance：能判定「本周检查点是否该触发」；落后时产出重排提案（减量 / 顺延 / 换交付物）
  - Verify：单测用固定时间桩覆盖按时 / 落后 / 无报告三种情况
  - Files：`backend/app/plan.py`、`backend/tests/test_progress.py`

- [x] **T19 建节点防重复提交（P1 补充）**
  - Acceptance：同一层级下已有未收尾的同名节点时，再次创建返回 `409` 并说明是哪一条；已完成 / 跳过的同名节点不挡路
  - Verify：`/docs` 里对一个新检查点连点两次 `Execute`，第二次得 409，库里只多一条
  - Files：`backend/app/plan.py`、`backend/app/main.py`、`backend/tests/test_progress.py`

- [x] **T20 台账语义归属（P1 补充，方案 B）**
  - Acceptance：台账的「作废 / 取代」只对 `profile_item` 与 `plan` 开放；节点 / 候选 / 提案调用时明确报错并给出替代动作（`skipped` / `rejected`）。`plan_node` 侧的读取把 `void` / `superseded` 当不存在（不可见、不占位、不进阶段进度分母）
  - Verify：`pytest -q`（拒绝路径 + 幽灵兜底共 7 条）；用绕过台账的 `UPDATE` 造一个 `void` 节点，确认它不再当当前阶段、不挡同名重建、不算落后
  - Files：`backend/app/ledger.py`、`backend/app/plan.py`、`backend/tests/test_ledger.py`、`backend/tests/test_progress.py`

- [x] **T21 建节点入参校验（P1 补充）**
  - Acceptance：`POST /api/plan/nodes` 的 `due_date` 只接受零填充 ISO 日期，`2026-13-01` / `2026/09/30` / `2026-9-3` 一律 `422`；库里仍存 TEXT
  - Verify：单测直接测请求模型（不引入 httpx）；临时库发真实请求，非法日期 422、合法日期 201 且落库为 `'2026-09-30'`
  - Files：`backend/app/main.py`、`backend/tests/test_api_contract.py`

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

- [ ] **T10 LLM provider 管理与调用记账（后端）**
  - Acceptance：`llm_provider` 表 CRUD 与「设为默认 / 启用停用」；`POST /api/providers/{id}/test` 发最小请求测连通性；`llm_call` 每次调用落一行记账（provider、模型、输入/输出 tokens、耗时、成功与否）；**同一操作内最多 3 次模型调用**，超出即中止报错；密钥**只写不读**，列表只返回掩码
  - Verify：单测覆盖密钥不回传明文、掩码格式、记账落行、循环保护在第 4 次调用时中止；用假 provider 打桩，不打真实接口
  - Files：`backend/app/llm.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/tests/test_llm.py`

- [ ] **T11 provider 管理页面（前端）**
  - Acceptance：页面能列出已配置提供商（掩码）、新增、修改、删除、测连通性、设为默认；密钥输入框只写不读
  - Verify：新增一家假的本地 provider，测连通性得到失败提示且不报异常；列表里密钥始终是掩码
  - Files：`frontend/app/providers/page.tsx`、`frontend/lib/api.ts`

- [ ] **T12 四问判断链路（LLM 提案）**
  - Acceptance：`POST /api/requests` 收「我发现了某个资料，要不要学」，产出四问答案，每条能指回 `profile_item` 的具体字段；LLM 输出先过 schema 校验，落成 `proposal`，**不直接写库**
  - Verify：用假 LLM provider 跑单测，验证「合法输出→落提案」「非法输出→重试一次后如实报错」两条路径
  - Files：`backend/app/advisor.py`、`backend/app/llm.py`、`backend/tests/test_advisor.py`

- [x] **T13 候选清单生成与去重**
  - Acceptance：输入「我不知道该学什么」，产出 3–5 条候选，每条带「为什么对你有用」与建议深度，带排序和一句「建议先从哪条开始」；已被否决的候选不再出现；「找」走 provider 接口，当前为不联网实现
  - Verify：单测覆盖候选数量约束、排序、去重（否决过的候选被过滤）；成功标准 2 的人工走查
  - Files：`backend/app/advisor.py`、`backend/app/providers/find.py`、`backend/tests/test_candidates.py`
  - 实施记录（2026-09-16）：入口复用 `POST /api/requests`（`kind=search`）；来源抽成 `providers/find.py` 的 `Brief` + `Source`（甲档 `route_only`，返回 `source.networked=false` 自证不联网）；候选与四问共用可回溯底线（`why` 要么给 id、要么说「依据不足」）；`recommended_start` 必须一字不差复制某条 title；去重是硬保证——禁区内标题命中即判不合格重试，两次仍命中就报错且不落一条；裁定走 `accepted`/`rejected` 终态，否决理由进 `reject_reason` 与台账并成为下次禁区。`pytest -q` 199 passed + 冒烟 9 步全绿。**归一化只做去空白与大小写**：换个说法的同一件事仍可能漏过，未做模糊匹配。

- [ ] **T14 候选与提案的前端交互**
  - Acceptance：页面上能看候选、采纳或否决；能看待裁定提案并裁定（档案变更 / 计划重排）；否决结果落台账
  - Verify：否决一条候选后重新请求同类输入，该候选不再出现；裁定提案后档案与计划表按预期变化
  - Files：`frontend/app/candidates/page.tsx`、`frontend/app/proposals/page.tsx`、`frontend/lib/api.ts`

- [x] **T22 档案录入（P3 补充）**
  - Acceptance：`POST /api/profile`（新增）、`PUT /api/profile/{id}`（取代，旧值留痕）、`POST /api/profile/{id}/void`（作废）三条写入口**全走台账**；`category` 只收 `advisor.PROFILE_CATEGORIES` 五个约定令牌；取代与作废必填理由；同类别允许多条并存但**一字不差的当前有效条目 409**（判重只看 active，作废后重填不挡）；前端 `/profile` 页能看五类缺口、补、改、作废
  - Verify：`pytest -q` 177 passed（含 17 条新单测）+ `tools\smoke_p1.py` 9 步全绿；前端 lint 与 tsc exit=0（浏览器走查归用户）
  - Files：`backend/app/main.py`、`backend/tests/test_profile_write.py`、`frontend/app/profile/page.tsx`、`frontend/lib/api.ts`

## P3.5 结构与流程改造（2026-09-17 定，排在 P4 之前）

- [ ] **T23 任务层与交付物验收（P1 骨架改造；取代 T4 的旧完成判定）**
  - Acceptance：结构变三级（计划 → 阶段 → { 任务…、周打卡… }），`plan_node.level` 增 `task`（数据库无约束、不用迁移）；**阶段完成判定 = 全部任务打勾完成或跳过 且 交付物已提交**，满足后才自动产「进下一阶段」提案（文案「任务全部完成、交付物已提交」）；**没有任务的阶段**按「任务条件天然满足」只看交付物；任务一键打勾（走台账）、可跳过（跳过算完成、理由必填）、截止日期可选（带了才进落后量）；交付物提交是阶段上的独立动作（链接 + 一句话，可重新提交、旧值留痕，单独存 `deliverable_submission` 表）；**周打卡不再参与阶段完成判定**（退为周报 / 落后提醒 / P4 触达的节奏职能）；界面：计划表阶段下分两组（任务 / 周打卡）、任务行有打勾按钮、阶段上有「提交交付物」按钮与状态，`/new` 可建任务（选所属阶段）
  - Verify：单测覆盖完成判定四组合（有/无任务 × 交付物已交/未交）、打勾与跳过路径、落后量只吃带日期的任务、交付物重提交留痕；`pytest -q` + `tools\smoke_p1.py`（动了契约与台账写入）；前端 lint 与 tsc exit=0（浏览器走查归用户）
  - Files：`backend/sql/schema.sql`、`backend/app/plan.py`、`backend/app/main.py`、`backend/tests/test_task_layer.py`、`frontend/components/plan-tree.tsx`、`frontend/app/new/page.tsx`、`frontend/lib/api.ts`

- [ ] **T24 多计划与严格分开**
  - Acceptance：按 SPEC 决策 33 四条落地——① 候选带计划归属（提问时选计划，或「新方向（不属于任何计划）」）；② 采纳落到候选归属计划，无归属时显式选「进哪个计划 / 新建一个」（**改掉「落最新计划」**）；③ `GET /api/plans` + 计划作废/收尾路由（台账对 plan 的 void/supersede 本就开放），默认只列进行中、作废进历史留理由；④ 界面加计划切换器（首页 / `/new` / `/candidates`）；`/report` 不动
  - Verify：单测覆盖归属传递、无归属时的显式落点、作废后 `GET /api/plans` 不再列出；契约变更同步 SPEC 第 11 节；浏览器走查归用户
  - Files：`backend/app/plan.py`、`backend/app/main.py`、`backend/app/advisor.py`、`backend/tests/test_plans.py`、`frontend/app/page.tsx`、`frontend/app/candidates/page.tsx`、`frontend/app/new/page.tsx`、`frontend/lib/api.ts`

- [ ] **T25 「找」的加宽（独立小步，可插队提前）**
  - Acceptance：按 SPEC 决策 35——① 反馈流水（否决理由、采纳/否决记录、每轮清单按时间）进 prompt；② 输出 schema 加可选「追问槽位」，追问必须说清缺哪类信息，否则判不合格（延续「依据不足」纪律）
  - Verify：单测覆盖反馈流水组装与追问槽位的校验；`pytest -q`；真实模型走查归用户
  - Files：`backend/app/advisor.py`、`backend/app/providers/find.py`、`backend/tests/test_candidates.py`

- [ ] **T26 对话式规划与蓝图（含决策 6 修订）**
  - Acceptance：按 SPEC 决策 36——采纳候选后触发多轮对话问清意向（形态「方案一」：保留候选层）；沿对话生成该方向的树（计划 / 阶段 / 任务）；**树 = 版本**（一个计划同时只有一份待裁定蓝图，新版落库时旧版自动取代、留痕）；裁定支持**勾选部分采纳**；决策 6 修订落进 SPEC。实现前先定两件技术细节：对话历史存哪、蓝图提案的 `kind` 与 payload 形状
  - Verify：单测覆盖蓝图提案落库与裁定（勾选部分落库、未勾选部分的处理）、版本取代留痕；契约新增路由同步 SPEC 第 11 节；真实模型走查归用户
  - Files：`backend/app/blueprint.py`（或并入 `advisor.py`）、`backend/app/main.py`、`backend/tests/test_blueprint.py`、`frontend/app/candidates/page.tsx`（或新页）、`frontend/lib/api.ts`、`docs/SPEC.md`（决策 6）

## P4 触达兜底

- [ ] **T15 `notify` 接口与邮件实现**
  - Acceptance：`notify` 为接口，含邮件实现与空实现（本地开发用）；内容组装包含「当前阶段 / 本周交付物推到哪 / 落后时建议怎么调」三问
  - Verify：空实现下内容组装有单测；邮件实现用 `--dry-run` 打印不发送
  - Files：`backend/app/notify.py`、`backend/tests/test_notify.py`

- [ ] **T16 周检查点 job**
  - Acceptance：`python -m app.jobs.weekly_checkpoint --dry-run` 输出正确三问；写 `notification_log`；启动时检查 `last_sent_at` 并补发错过的检查点
  - Verify：`--dry-run` 输出人工核对；补发逻辑用固定时间桩单测
  - Files：`backend/app/jobs/weekly_checkpoint.py`、`backend/tests/test_weekly.py`

- [ ] **T17 Markdown 导出**
  - Acceptance：单向导出四个只读文件到 `exports/`——`计划-当前.md`、`决策台账.md`、`档案-当前.md`、`周检查点-YYYY-WW.md`；**永不回写数据库**；与每周兜底推送同一时刻自动导出，页面上另有手动导出按钮
  - Verify：手动触发导出，四个文件内容与数据库一致；手工改 md 后重新导出会被覆盖（以此证明是单向的）
  - Files：`backend/app/export.py`、`backend/tests/test_export.py`

## P5 验收

- [ ] **T18 两周试用与成功标准走查**
  - Acceptance：SPEC 第 9 节成功标准 1–5 逐条通过，或记录未通过项与原因
  - Verify：完成一次真实闭环（找 → 认同 → 计划 → 执行 → 报告 → 推进），并留存走查记录
  - Files：`docs/试用记录.md`
