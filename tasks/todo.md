# 任务清单 v0.6

配套：`docs/SPEC.md` · `tasks/plan.md`

按依赖顺序排列，不按重要性。每个任务能在一次专注里做完，都带验收与验证方式。

**v0.13 变更（2026-09-20）**：新增并完成 P3.7（T34 候选的路径形状 / T35 阶段跳过 / T36 追问槽收口）——用户走查触发（「五个连续性的方案，不应该独立」、追问槽「有追问、没有输入框」）；方案见 `docs/候选路径与追问槽方案.md`，规格落 SPEC 决策 41 与 31/34/35/36 的修订。用户当日授权 `candidate` 加一列 `payload`；T36 走方案口径 A（加输入框）。

**v0.11 变更（2026-09-18）**：新增并完成 T28（计划级对话——蓝图落地之后接着聊）——用户走查提的第 5 条；对应 SPEC 决策 37 与决策 6 的第二次修订。

**v0.12 变更（2026-09-18）**：侧窗讨论后拍板两条待办——T29（提案瘦身与页面分家：判资料独立、蓝图待批独立、推进提案整类删）与 T30（节点字段写入口＝原地改 + 台账流水）；两条都要先动 SPEC 决策 28/29/30。

**v0.10 变更（2026-09-18）**：新增 T27（计划生命周期四态与历史计划出口，排在 P4 之前）——「作废」拆成 `paused`（暂时不做，可逆）与 `void`（这件事根本不该做，单向门），补暂停 / 重开两条路由与界面出口；SPEC 决策 33 与第 11 节契约表同步。

**v0.9 变更（2026-09-18）**：T25（「找」加宽）与 T26（对话式规划与蓝图）**完工**，各带实施记录；SPEC 决策 35/36 标已实现、第 11 节契约表补三条 plan-chat 路由与 `selected`。

**v0.8 变更（2026-09-18）**：T25/T26 补入「实现要点」（反馈流水的上限与位置、追问槽位形状、对话轮数与历史存储、蓝图 payload、树=版本的取代语义、勾选部分采纳后剩余处理）——SPEC 决策 6/35/36 同步写死，新窗口可照做无需再问。

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
  - 实现要点（2026-09-18 钉死，新窗口照此做）：① 反馈流水 = **最近 5 轮**「找」的记录（时间 / 计划归属 / 候选标题 / 每条候选的裁定结果与否决理由原文），按时间正序，整段**字符上限 1200**、超出从最旧截断，插在档案段之后、「硬性要求」之前；只放已裁定或已过期的，四问记录不进。② 追问槽位 = 输出对象加**可选** `clarify: {question, missing}`；`question`/`missing` 缺一判不合格，`missing` 必须说清缺哪类档案信息（空泛追问判不合格），**追问不能替代清单**（仍要给满 3–5 条），不额外增加调用（仍 1 次 + 最多 1 次重试）。③ `clarify` **不落库**，只在当次响应里返回（与 `start_reason` 同处理）；界面把追问显示在清单上方，用户回答的内容就是下一轮的输入——**不新建表、不加列**。④ 反馈流水的条数与字符上限写成常量，便于按实测调整。
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

- [x] **T23 任务层与交付物验收（P1 骨架改造；取代 T4 的旧完成判定）**
  - Acceptance：结构变三级（计划 → 阶段 → { 任务…、周打卡… }），`plan_node.level` 增 `task`（数据库无约束、不用迁移）；**阶段完成判定 = 全部任务打勾完成或跳过 且 交付物已提交**，满足后才自动产「进下一阶段」提案（文案「任务全部完成、交付物已提交」）；**没有任务的阶段**按「任务条件天然满足」只看交付物；任务一键打勾（走台账）、可跳过（跳过算完成、理由必填）、截止日期可选（带了才进落后量）；交付物提交是阶段上的独立动作（链接 + 一句话，可重新提交、旧值留痕，单独存 `deliverable_submission` 表）；**周打卡不再参与阶段完成判定**（退为周报 / 落后提醒 / P4 触达的节奏职能）；界面：计划表阶段下分两组（任务 / 周打卡）、任务行有打勾按钮、阶段上有「提交交付物」按钮与状态，`/new` 可建任务（选所属阶段）
  - Verify：单测覆盖完成判定四组合（有/无任务 × 交付物已交/未交）、打勾与跳过路径、落后量只吃带日期的任务、交付物重提交留痕；`pytest -q` + `tools\smoke_p1.py`（动了契约与台账写入）；前端 lint 与 tsc exit=0（浏览器走查归用户）
  - Files：`backend/sql/schema.sql`、`backend/app/plan.py`、`backend/app/main.py`、`backend/tests/test_task_layer.py`、`frontend/components/plan-tree.tsx`、`frontend/app/new/page.tsx`、`frontend/lib/api.ts`
  - 实施记录（2026-09-17）：旧判定改写完成——`stage_completion` 只数任务层、`stage_finished` = 任务全收尾 + 交付物已提交（空任务阶段天然满足）；新增 `check_task` / `skip_task`（必填理由）/ `submit_deliverable` 与三条路由；新表 `deliverable_submission`（重提交 = 新行）；推进提案文案改「任务全部完成、交付物已提交」并带 `deliverable_url`；计划表分「任务 / 周打卡」两组、任务可打勾/跳过、阶段可提交与重提交交付物。**老数据不做兼容**：用户选了物理清库（`tools/wipe_plan_data.py`，备份 `data/cadence.db.bak-20260917-234923`）。`pytest -q` 229 passed + `smoke_p1.py` 10 步全绿 + lint/tsc exit=0。提交 `3a6854b`（后端）、`b18b732`（前端）；浏览器走查归用户。

- [x] **T24 多计划与严格分开**
  - Acceptance：按 SPEC 决策 33 四条落地——① 候选带计划归属（提问时选计划，或「新方向（不属于任何计划）」）；② 采纳落到候选归属计划，无归属时显式选「进哪个计划 / 新建一个」（**改掉「落最新计划」**）；③ `GET /api/plans` + 计划作废/收尾路由（台账对 plan 的 void/supersede 本就开放），默认只列进行中、作废进历史留理由；④ 界面加计划切换器（首页 / `/new` / `/candidates`）；`/report` 不动
  - Verify：单测覆盖归属传递、无归属时的显式落点、作废后 `GET /api/plans` 不再列出；契约变更同步 SPEC 第 11 节；浏览器走查归用户
  - Files：`backend/app/plan.py`、`backend/app/main.py`、`backend/app/advisor.py`、`backend/tests/test_plans.py`、`frontend/app/page.tsx`、`frontend/app/candidates/page.tsx`、`frontend/app/new/page.tsx`、`frontend/lib/api.ts`
  - 实施记录（2026-09-18）：`db.init` 增加列迁移（`learning_request.plan_id`，老库自动追平）；`plan.list_plans` / `close_plan` / `void_plan` + 三条路由；`advisor` 把计划上下文（目标/当前阶段/还开着的任务）组装进「找」的 prompt、候选随请求继承归属、`decide_candidate` 按「归属 > 显式 plan_id」落点（冲突/缺失/已收尾一律 409 且保持 proposed）、`expire_previous_candidates` 实现决策 34（同计划上一轮未裁定自动过期，不进禁区）；前端加计划切换器与管理区、`/candidates` 选计划与「新建计划并采纳」、`/new` 选落点。`pytest -q` 244 passed + `smoke_p1.py` 10 步全绿 + lint/tsc exit=0。提交 `9f68eab`（后端）、`b6c20ec`（前端）。**`/report` 按决策未动**：它仍只列「最新 active 计划」的节点。

- [x] **T25 「找」的加宽（独立小步，可插队提前）**
  - Acceptance：按 SPEC 决策 35——① 反馈流水（否决理由、采纳/否决记录、每轮清单按时间）进 prompt；② 输出 schema 加可选「追问槽位」，追问必须说清缺哪类信息，否则判不合格（延续「依据不足」纪律）
  - Verify：单测覆盖反馈流水组装与追问槽位的校验；`pytest -q`；真实模型走查归用户
  - Files：`backend/app/advisor.py`、`backend/app/providers/find.py`、`backend/tests/test_candidates.py`、`frontend/app/candidates/page.tsx`、`frontend/lib/api.ts`
  - 实施记录（2026-09-18）：① 反馈流水 = `advisor._feedback_block`（最近 5 轮 `search` 请求：时间 / 计划归属 / 候选标题 / 每条候选的裁定结果与否决理由原文，按时间正序，字符上限 1200、超出从最旧截断；`proposed` 不进这段——「你还没表态」不是反馈；四问请求不进）；条数与上限是常量 `FEEDBACK_ROUNDS` / `FEEDBACK_CHAR_LIMIT`，便于按实测调；经 `Brief.feedback_lines` 交来源层，插在档案段之后、「硬性要求」之前。② 追问槽位 = 可选 `Clarify{question, missing}`；`missing` 必须点名五类档案之一（中文名或英文 token，词表 `CLARIFY_KEYS`），空泛追问判不合格；**追问不替代清单**（仍要 3–5 条）、**不额外增加调用**（仍 1 次 + 最多 1 次重试）；不落库、不加列，只在当次响应里返回。③ 界面把追问显示在清单上方，并标出本轮带上了几轮流水。`pytest -q` 274 passed（244 + 10 条新用例）+ `smoke_p1.py` 10 步全绿（动了响应契约）。提交 `4d8594f`（后端）、`1562da6`（前端）。**未覆盖**：真实模型对反馈流水的利用效果（走查归用户）；条数与上限只按「别让 prompt 更慢」定了常量，未实测调优。

- [x] **T26 对话式规划与蓝图（含决策 6 修订）**
  - Acceptance：按 SPEC 决策 36——采纳候选后触发多轮对话问清意向（形态「方案一」：保留候选层）；沿对话生成该方向的树（计划 / 阶段 / 任务）；**树 = 版本**（一个计划同时只有一份待裁定蓝图，新版落库时旧版自动取代、留痕）；裁定支持**勾选部分采纳**；决策 6 修订落进 SPEC。实现前先定两件技术细节：对话历史存哪、蓝图提案的 `kind` 与 payload 形状
  - Verify：单测覆盖蓝图提案落库与裁定（勾选部分落库、未勾选部分的处理）、版本取代留痕；契约新增路由同步 SPEC 第 11 节；真实模型走查归用户
  - Files：`backend/app/blueprint.py`、`backend/app/proposals.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/tests/test_blueprint.py`、`frontend/app/candidates/page.tsx`、`frontend/app/proposals/page.tsx`、`frontend/lib/api.ts`、`docs/SPEC.md`（决策 6/35/36）
  - 实施记录（2026-09-18）：新增 `backend/app/blueprint.py` 装两条链路（对话 + 蓝图）。**对话**：`plan_chat` 表（plan_id + candidate_id + role + content，追加式、不经台账）；前提是候选已采纳（否则 409）；每轮 1 次调用、**不重试**（决策 6 修订），整段上限 6 轮（按用户发的话数），历史字符上限 4000 从最早截断（最新一句永远留着）；输出 `{questions(≤3), ready, note}`，既不 ready 又没问题判不合格；助手那侧存 JSON 原文、喂回下一轮时渲染成人话。**蓝图**：`kind=plan_blueprint`，payload 按决策 36 的形状；生成前必须聊过一轮（`can_generate`）；1 次调用 + 不合格带原因重试 1 次；`due_date` 非法判不合格（不悄悄吞）。**树 = 版本**：先建新版再把同计划旧的 pending 标成业务终态 `superseded`（不碰台账生命周期列），被取代的不能再裁。**批准 = 按勾选建树**：`selected` 形如 `["0","1.2"]`（阶段整段 / 单件任务，从 0 起），没勾的直接丢弃，不传 = 整份；`effect=blueprint_built` 且回执带 `built`。**实现时定下的三件事**：① 计划归属复用 `advisor.landing_plan`（原 `_landing_plan` 转为公开）——采纳落点与对话/蓝图归属必须是同一个答案；② 蓝图里与已有阶段**同名**的条目**复用**那条阶段（采纳时自动建的），任务挂到它下面、不重复建，它要交的东西写不进去（改节点字段的写入口不存在）→ 在 `built.notes` 里明说；③ 重名任务 / 下标超界 / 计划已收尾全部在**建之前**查完（台账无请求级事务，验不过就一个节点都不建、提案保持 pending 可重裁）。另把 `advisor._extract_json` 转为公开的 `extract_json` 共用同一套 JSON 取法。`pytest -q` **274 passed**（254 + 20 条新用例）、`smoke_p1.py` 10 步全绿、lint/tsc exit=0；真实库已跑 `python -m app.db init` 追上 `plan_chat` 表。提交 `d24bb58`（后端）、`3ee63b6`（前端）。**未覆盖**：真实模型跑对话与蓝图（走查归用户）；蓝图里「第一版就有同名阶段」这条只由单测覆盖。
  - 扩（2026-09-18，用户要求「只要是这个计划里面的，都该让它知道」）：计划对话的上下文从「计划现在长什么样」扩到「它是怎么来的」——新增两段：**这条方向的来历**（采纳过哪条候选 + 当时给的理由 + 出蓝图之前那段对话，`blueprint.render_reply` 转为公开以复用同一套渲染）与**蓝图全貌**（已建进计划的那版 + 待勾选那版：每阶段的交付物与理由、每件任务、以及**哪些当时没被勾中所以没建**；被顶掉的旧版只报个数）。各有字符上限（来历 1500 / 蓝图 2500，从最旧截断）——这段事实每轮都要重发，是成本旋钮。实测你库里计划 #2 的上下文约 4400 字符。`pytest -q` **317 passed**（+6 条：来历进上下文、蓝图带「没勾」标注、旧版只报数、没有来历时如实说、截断方向）。
  - 修（2026-09-18，用户走查踩到）：**「够了，出方案」认不出计划归属**——一条「新方向」的候选被显式采纳后，落点只活在采纳当刻的响应与前端内存里；页面刷新（前端热更新重建也算）后前端传不出 `plan_id`，而当时只有 `view` 会读 `plan_chat` 记着的归属，「聊一句」与「出方案」只看候选自带的 → 同一流程两套判断标准、最后一步报「没有计划归属」。修法：`blueprint.resolve_plan` 统一成「调用方说明 > 已有对话记着的 > 候选自带」（`_thread_plan` 转公开 `thread_plan`，与 `view` 共用；显式与记着的不一致回 409，绝不改口），前端以 `view.plan_id` 为准并在服务器也说不出时给一个计划选择框。提交 `f7fa658`；`pytest -q` **279 passed**（我 4 条回归 + 并行会话 1 条「对话所属计划已收尾/作废就拦下」）、`smoke_p1.py` 10 步全绿、lint/tsc exit=0。

- [x] **T27 计划生命周期四态与历史计划出口**
  - Acceptance：按 SPEC 决策 33 的 T27 修订落地——`plan.status` 拆成 `active` / `paused` / `closed` / `void` 四态（`paused`＝暂时不做可恢复，`void`＝这件事根本不该做、单向门）；`pause_plan` / `reopen_plan` 两条函数与两条路由；`list_plans` 每项带 `ended_at` / `ended_reason`；界面加「暂停」按钮与「历史计划」折叠段（暂停项「继续做」、收尾项「重开」、作废项不给按钮）。**AI 那一层不做**（只做界面这一层）
  - Verify：单测覆盖暂停 / 重开 / 收尾三条路径与幂等、`void` 的单向门、`ended_*` 反映最后一次结束、`proposals.decide` 对非 active 计划拒绝收尾、两条新路由的 200/400/404 与回执形状；`pytest -q` + `tools\smoke_p1.py`（动了契约）；前端 lint 与 tsc exit=0（浏览器走查归用户）
  - Files：`backend/app/plan.py`、`backend/app/proposals.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/tests/test_plans.py`、`frontend/app/page.tsx`、`frontend/lib/api.ts`、`docs/SPEC.md`（决策 33、第 11 节）
  - 实施记录（2026-09-18）：`plan.pause_plan`（不存在 / 已作废 / 已收尾一律 `PlanError`，已暂停幂等 `changed:false` 且**不写流水**，理由默认「暂时不做了」）与 `plan.reopen_plan`（`paused` / `closed` → `active`，进行中幂等，**作废回 `PlanError`**——单向门）；`close_plan` **未改**（本就挡 `void` / `superseded`，故进行中与暂停的都能收尾）。`list_plans` 增 `ended_at` / `ended_reason`：取 `ledger.history` 里**最后一条** `change_type in ("status_change", "void")` 流水的 `created_at` / `reason`（暂停 → 继续 → 再收尾的序列里只有最后那条回答得了「现在这个状态是怎么来的」），进行中恒为 `None`。`proposals.decide` 的收尾分支判据由 `== "closed"` 改成 `!= "active"`（否则暂停或作废的计划仍会被这一条顺水收尾），文案改「这个计划已经不是进行中（{status}），不必再裁一次」。路由 `POST /api/plans/{id}/pause` 与 `/reopen`（请求模型 `PlanPauseIn` / `PlanReopenIn`，`reason` 可选）。`schema.sql` 的 `plan.status` 注释补四态（顺手改正同块里过时的「两级」与 `level` 注释）。前端：`pausePlan` / `reopenPlan`、`PlanSummary` 两个新字段、首页计划管理区加「暂停」（交互同收尾＝一次点击、用默认理由）、切换器下方加「历史计划」`<details>` 段（暂停项「继续做」/ 收尾项「重开」都调 `reopenPlan`；作废项不给按钮并附「作废是单向门，要重新做就新建一个计划」），作废文案改成新语义。`pytest -q` **294 passed**（279 + 15 条新用例）、`smoke_p1.py` 10 步全绿、lint/tsc exit=0。**未覆盖**：浏览器走查归用户；界面上的「暂停」只能用默认理由（要自定义理由得直接调接口）。

- [x] **T28 计划级对话：蓝图落地之后接着聊**
  - Acceptance：按 SPEC 决策 37——计划表页有一块能跟 AI 接着聊的窗口（蓝图落地之后才真正开始用）；对话跟着**计划**走、**不限轮数**（成本闸换成历史字符上限）；上下文每轮重拼（阶段/任务/状态/截止日/落后量/最近报告/档案）；**只说话与建议，不改计划结构**；聊出的「我的状态变了」能提炼成待裁定的**档案变更提案**（你点按钮才发生；批准才真写档案——顺带让 `profile_change` 这一类的第一个生产者出现）
  - Verify：单测覆盖不限轮数、历史字符截断、上下文七项事实、暂停计划也能聊、空回复不重试但留你的话、提炼的 409/空数组/非法类别重试/超 3 条、以及「聊 → 提炼 → 批准 → 档案真多一条」的端到端；`pytest -q` + `tools\smoke_p1.py`（动了契约）；前端 lint 与 tsc exit=0（浏览器走查归用户）
  - Files：`backend/app/dialogue.py`、`backend/app/profile.py`、`backend/app/proposals.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/tests/test_dialogue.py`、`backend/tests/test_proposals.py`、`frontend/components/plan-dialogue.tsx`、`frontend/app/page.tsx`、`frontend/app/proposals/page.tsx`、`frontend/lib/api.ts`、`docs/SPEC.md`（决策 6 第二次修订 + 决策 37 + 第 11 节）
  - 实施记录（2026-09-18）：分两块落地。**① 档案变更提案能真写档案**（提交 `8db6bcb`）：新增 `backend/app/profile.py`，把档案写入的三条规则与那道**判重闸**（同类别一字不差的当前有效条目不许重复）从接口层搬出来——原先这条规则只写在 `POST /api/profile` 里，而「批准档案变更提案」要走同一道闸，两处各写一遍迟早分叉；`proposals.decide` 新增 profile_change 分支，**验完再写**（类别合法 / 内容非空 / 不撞重复 → 否则 400/409 且提案保持 pending 可重裁），`effect=profile_written` 并回执带 `written`，台账理由记成「按提案 #N 批准写进档案：<聊出的 why>」。**② 计划级对话**（提交 `ed1951b` 后端、`6ec4d94` 前端）：新表 `plan_dialogue` + 新模块 `backend/app/dialogue.py`，三条路由（看 / 聊 / 提炼）；**不限轮数**、每轮 1 次调用不重试、历史字符上限 6000 从最早截断；助手那侧**存人话不存 JSON**（这一段不需要解析它的输出）；上下文每轮重拼（计划目标、阶段与交付物、任务与状态与截止日与落后量、最近 5 份报告、长期档案），所以计划表里刚打的勾下一轮它就看得见；提炼做成**你点按钮才发生**（闲聊不该往 /proposals 撒提案），1 次调用 + 不合格带原因重试 1 次、允许 0 条、类别必须落在五个令牌里；**讨论不按计划状态拦**（暂停了正是最需要商量的时刻）。前端：计划表页底部内嵌 `components/plan-dialogue.tsx`（key 带计划号，换计划整块换掉），`/proposals` 补 profile_change 的专门渲染与 `profile_written` 回执。`pytest -q` **315 passed**、`smoke_p1.py` 10 步全绿、lint/tsc exit=0；真实库已跑 `db.init` 追上 `plan_dialogue` 表。**未覆盖**：真实模型跑这段对话与提炼（走查归用户）；**范围外**：改已有节点的字段（交付物/截止日）仍无写入口——聊出「这个阶段不要了」只能靠计划表里的打勾/跳过表达，那件事按交接文档的约定单独立项。

- [x] **T29 提案瘦身与页面分家（2026-09-18 用户走查后拍板）**
  - Acceptance：他定的三条——① 判资料页只装判资料的（四问输入 + 当次答案 + 历史记录），**「学什么」那条路的产物一律不进这页**（原话：「学什么那一类的产物不要出现在『值不值得学这个界面』」）；② 蓝图待批**单独一页**（他明确否掉「并进计划页」）；③ **推进提案整类删**（规则自动产、批准不改任何东西；手动收尾已在计划页有），落后的「重排」降级成计划页上的一句提醒
  - 前置：SPEC 决策 28（提案按 kind 分流裁定）与 29（两个正式页）要先改写，决策 30 里「阶段完成即产推进提案」那半句也要一起删；已存在的旧提案（含库里 pending 的 5 条推进提案）要先定处置口径（一次性标终态，还是留着只读）
  - 他那两条未定的**已答**（2026-09-18）：判资料页**留只读历史**；存量 5 条 pending 推进提案**标终态加理由**
  - Verify：单测删掉 `stage_advance` 相关断言、补「阶段完成后不再产提案」与「落后只出提醒」；`pytest -q` + `tools\smoke_p1.py`（动了契约）；前端两页各自 200（浏览器走查归用户）
  - Files：`backend/app/plan.py`、`backend/app/proposals.py`、`backend/app/main.py`、`backend/tools/smoke_p1.py`、`docs/SPEC.md`（决策 28/29/30）、`frontend/app/judge/`、`frontend/app/proposals/page.tsx`、`frontend/components/blueprint-body.tsx`、`frontend/components/judgment-view.tsx`、`frontend/lib/api.ts`
  - 实施记录（2026-09-18）：**先落规格再开工**——决策 28 收窄成三类（`material_judgment` 只记账 / `profile_change` 真写档案 / `plan_blueprint` 勾选建树），`stage_advance` 与 `plan_replan` 整类删除、落后的做法降级成计划页一句提醒；决策 29 改成「一件事一页」；决策 30 删掉「阶段完成即产推进提案」半句；另加**决策 38**（节点字段写入口，给 T30）。**存量处置**：真实库 5 条 pending 推进提案（#4–#8）走台账标 `rejected`、理由写明整类删除，pending 清零。**后端**：删两个生产者（`maybe_stage_advance_proposal` / `ensure_weekly_replan_proposal`）连同它的调用口、去重函数与只被它用的 `_next_stage`；三个动作与报告不再回 `proposal_id`（键留着恒为 null）；`plan_tree` 多带 `behind_reason` 与 `advice`（三个方向只是建议；判定本身留着给 P4 的周 job）；`decide` 去掉两个分支与 `option`（请求模型、路由、回执、前端一起去掉），**批准**老类型明确 400 而**驳回**仍可。**前端**：`/judge` 新页（四问输入 + 当次答案 + **只读历史**，历史走新接口 `GET /api/judgments` = `proposals.list_decided`，只列**已裁定**的四问判断）；`/proposals` 收紧成只装「点头能改动东西」的三类（蓝图带勾选树、档案变更、资料判断）；计划页显示落后提醒；`/report` 与 `/candidates` 的旧文案、各页导航跟着改。验证：`pytest -q` **301 passed**（删 14 条引用已删功能的用例，新增「阶段收尾不再产提案」「落后只出提醒」「老类型批准被拒但可驳回」）、`smoke_p1.py` **10 步全绿**（第 7 步期望改成「不再产提案」并顺带断言完成判定仍在）、lint/tsc exit=0。提交 `b41431e`（后端）、`86d120e`（前端）。**未覆盖**：浏览器走查归用户（两个新页第一次进要走一遍）。
  - 复核记录（2026-09-18，主对话）：侧窗这一轮先按「蓝图单独开一页 `/blueprints`」实现并提交，而用户当天对「其余待裁定放哪」的答复是**跟蓝图同一页**——两处对不上。复核时按用户答复把它并回来：蓝图渲染抽成 `frontend/components/blueprint-body.tsx`（勾选规范化、默认整份、`selectionOf` 一起搬过去），`/proposals` 重新渲染三类并按 kind 分流，`/blueprints` 目录删除，首页 / `/candidates` / `/judge` 四处导航与文案跟着改；`docs/SPEC.md` 决策 29 早已写着「同住这一页」，实现这一轮才追上。同轮收口另两处不一致：SPEC 里重复的节点改字段路由行（`/update` 与 `/fields` 两条，留实现的 `/fields`）、判资料历史的口径（SPEC 与页面文案写「含未裁定」，实现是只列已裁定——统一成实现的写法并改文案）。复核后 `pytest -q` **312 passed**、`smoke_p1.py` **11 步全绿**、lint/tsc exit=0。

- [x] **T30 节点字段写入口（改一个已经建好的节点）**
  - Acceptance：口径**已定**（2026-09-18 他选「第二条」）——**原地改字段 + 台账记一条流水**（谁、何时、改前改后、理由），**id 不变**；**不用「取代」**：取代会让 id 变、报告 / 交付物 / 引用全断，而且台账明禁对 `plan_node` 做生命周期操作（决策 22——节点的「不算数」由跳过表达，改字段不属于生命周期事件）。做出来要能改 `title` / `deliverable` / `due_date` 三个字段
  - 解锁：批准「重排」能真执行（挪截止日），计划页那块对话才能改东西而不只是往里加（今天有三处卡在这条：T26 的「同名阶段复用、交付物写不进去」、T14 的「重排只记方向」、T28 的「只说话与建议，不改计划结构」）
  - 待确认（**2026-09-18 已答**）：计划页那块对话**T30 之后接上「能改」**——他此前说的「聊是本体、改计划不是目的」是指当时不该顺手让对话写计划，不是永远不接
  - Verify：单测覆盖改字段留痕（改前改后进流水）、id 与引用不断（报告 / 交付物仍指得到）、缺理由报错、非节点对象拒绝；`pytest -q`（动了台账写入语义，加跑 `tools\smoke_p1.py`）
  - Files：`backend/app/plan.py`、`backend/app/main.py`（一条路由）、`backend/app/blueprint.py`（复用阶段写交付物）、`backend/tests/test_task_layer.py`、`backend/tests/test_blueprint.py`、`backend/tools/smoke_p1.py`、`docs/SPEC.md`（决策 38 已随 T29 落盘 + 第 11 节）、`frontend/components/plan-tree.tsx`、`frontend/app/page.tsx`、`frontend/lib/api.ts`
  - 实施记录（2026-09-18）：`plan.update_node_fields`——**原地改 + 一条台账流水**（`change_type='update_fields'`，before/after 是两个字段级的 JSON），配一次原地 `UPDATE`，**id 与所有引用一个不动**（这就是不选「取代」的原因：取代换 id，报告与交付物提交会全指不到；何况台账本就明禁对 `plan_node` 做生命周期操作）。规矩：只传要改的字段、**传空字符串表示清空**（交付物 / 截止日可清，标题不许清）、**理由必填**、一个字段都没真变则报错（不写噪音流水）；**改标题同样过防重复闸**（「改」不能成为绕过它的后门）；交付物只对阶段有效（任务与周打卡的产出用报告说明）；日期按 `YYYY-MM-DD` 校验。路由 `POST /api/plan/nodes/{id}/fields`（请求模型 `NodeFieldsIn`）。冒烟补第 11 步（改阶段交付物 + 缺理由被拒），并把结论改成按步数动态输出。验证：`pytest -q` **311 passed**（+10 条：id 不变与引用不断、流水留改前改后、挪截止日带动落后量、清空日期、缺理由 / 无改动 / 非法日期 / 任务给交付物被拒、改标题撞同名被拒、路由的 404/400 与回执）、`smoke_p1.py` **11 步全绿**。
  - 复核补齐（2026-09-18，主对话）：侧窗这一轮按 T30 的范围只做了后端，把两块一起补上。**① 界面入口**：`components/plan-tree.tsx` 加 `NodeFieldsEditor`（默认只是一个「改字段」按钮，点开才出现标题 / 要交的东西（只阶段）/ 截止日 / 理由四个输入），阶段、任务、周打卡三个层级都挂上；`lib/api.ts` 加 `updateNodeFields` + `NodeFieldsInput`/`NodeFieldsResult`；计划页把结果接进 `run()`（写完重新取计划与两份列表，失败把中文原因摆到页面上）。**② 解除 T26 那条限制**：批准蓝图时复用同名阶段，若蓝图里的交付物与那条阶段现有的不同，就**走同一道改字段流水写进去**（不是悄悄覆盖），`built.notes` 改成说清「写成了什么 / 从什么改成了什么」，原先那句「改节点字段的写入口还不存在」删掉；新增一条用例覆盖「本来有别的交付物 → 被这一版改写并留下 note」。复核后 `pytest -q` **312 passed**、`smoke_p1.py` **11 步全绿**、lint/tsc exit=0。**未覆盖**：界面入口与新页面一样，浏览器走查归用户。

- [x] **T31 计划对话接上「能改」（一条可执行建议 + 当场裁定）**
  - Acceptance：按 SPEC 决策 39（2026-09-18 用户拍板，推翻了决策 37 原先「只说话、不接写入口」那半句）。四步：① 它这一轮除人话外**最多附一条结构化建议**（改哪个东西、从什么改成什么、为什么），字段缺一即判不合格、带原因重说一次，没有建议时就是纯聊天；② 建议落成一条 `kind=plan_change` 的待裁定提案，界面上的「确认」＝当场裁定——**改的走原地改字段**（id 不变、台账一条流水）、**加的走建节点**（同一道防重名闸）；**忽略＝当场驳回**，台账记一句「聊天里先不动」；③ 计划页对话区在那条消息下显示确认条（人话一行 + 确认 / 忽略两个按钮），点完就地刷新对话与计划表；④ 提示词钉死能干与不能干（一轮最多一条；只在改动明确、理由具体时提，拿不准先问一句；不能删节点、不能替你打勾/跳过/交交付物、不能碰别的计划）
  - 他按默认做的两条：**加东西时一次只加一件**（任务挂到指定的阶段下、新阶段排最后）；**删除永远不做**——「这块不做了」在计划里用打勾/跳过/收尾表达，删掉会把报告与交付物的痕迹一起断掉
  - Verify：单测盯五件事——一次最多一条建议、字段不合法判不合格、节点不存在（或不属于本计划）要拒、批准后节点真改了且**编号不变**、忽略什么都没写；`pytest -q` + `tools\smoke_p1.py`（动了契约与台账写入语义）；前端 lint 与 tsc exit=0。真实模型效果与界面走查归用户
  - Files：`backend/app/plan_change.py`（新）、`backend/app/dialogue.py`、`backend/app/proposals.py`、`backend/app/main.py`、`backend/tests/test_dialogue.py`、`backend/tests/test_proposals.py`、`frontend/components/plan-dialogue.tsx`、`frontend/app/proposals/page.tsx`、`frontend/lib/api.ts`、`docs/SPEC.md`（决策 6 第三次修订 + 37 修订 + 新增 39 + 第 11 节）
  - 实施记录（2026-09-19）：后端完工（`plan_change.py`，328 passed / 冒烟 11 步全绿）；前端完工（`plan-dialogue.tsx` 确认条与就地刷新、`/proposals` 第四类 `plan_change` 渲染与裁定回执、`lib/api.ts` 类型契约同步）；lint 与 tsc 校验通过。全部交付。
  - **走查后放宽「一次一件」（2026-09-19 用户真实模型走查后拍板）**：他在真模型上跑了一遍，原话是「一次只能增加一个阶段或者是任务……要增加一个阶段，阶段里面是包含着多个任务的，这极其的不自然」。查到真库里的现场：他让模型给「开发日志上线」配任务，模型按当时提示词里的「一轮最多一条 + 一次只加一件」**顶了三轮嘴**（原话「一轮就一条，这是规矩，不是我不给」），他连点五次确认才排出一个阶段（提案 #11–#15），中途还因为防重名只认字面而落了两组近似重复的任务（「选定一个平台开号，发出第一条」≈「开号并发出第一条」；「录一条两账号互不可见的屏」≈「录制两账号互不可见的演示视频」）。**改法**：`add_task` / `add_stage` 收 `tasks`（1–5 件，`plan_change.MAX_TASKS`），加阶段时**连着它下面的任务一起建**——形状照蓝图那条路（`blueprint.apply_build` 本来就是阶段带任务）；提示词里那句「一次只加一件」删掉，改成「他要是说『排一下』就把该排的一次排完」；旧形状 `task` 仍然收。**上限仍在**：一条建议最多 5 件、批内重名拦、与已有任务撞名仍拒。回执从 `added` 单条改成 `added.nodes`（按建的顺序列出）。验证：`pytest -q` **332 passed**（+4 条：批量任务、阶段带任务、超 5 件判不合格、批内重名判不合格）、lint/tsc exit=0。**未做**：真库那两组重复任务没动（要收尾得像产品规矩那样用「跳过 + 理由」，等用户点头）。

## P3.6 Agent 核心（2026-09-20 定；方案见 `docs/agent落地.md`，**按用户同日定的「剥离记忆系统」那一版执行**）

原始方案分三段（循环 / 工具 / 上下文管理 + 长期记忆 + 界面收口）；用户 2026-09-20 追加的执行策略把**记忆那一整段剥掉**：不新增 `profile_evidence`、不扩 `profile_change`、不碰档案页与现有提炼流程——现有长期档案只作为 Agent 的一个**只读**信息来源。剩下的三块（循环、工具、上下文管理）与失败收口落成下面两条。

- [x] **T32 Agent 会主动读取（受控工具循环 + 四个只读工具 + `agent_run`）**
  - Acceptance：① 新增 `backend/app/agent_runtime.py`（统一循环）与 `backend/app/agent_tools.py`（四个只读工具：`read_current_plan` / `read_recent_reports` / `read_profile` / `read_plan_origin`）；② 模型每轮只输出两种结果之一——要读资料（`{"tool_calls": [...]}`，一次可批量要好几样）或直接回话（现有信封 `{"reply", "suggestion"}`）；③ **上限真实生效**：每轮最多 3 次模型调用（工具轮与「输出不合格重说一次」**共用**）、最多 6 次只读工具调用，工具结果另有总字符上限（超出从最旧那一轮往下丢）；④ **上下文不再每轮预装**全部业务事实——工具目录里只有「能读什么」；⑤ **计划编号由系统注入**，参数里出现 `plan_id` 一律拒（读不到别的计划）；⑥ 新增 `agent_run` 表，每轮落一行（成功 / 撞上限 / 失败三种），记模型调用次数、工具调用次数、每次工具的名称与参数摘要与成败与耗时、停止原因；⑦ `POST /api/plan-dialogue` **只新增** `run` / `tools_used` / `stop_reason` 三个可选字段（老字段一个没删没改），`GET` 的每条消息另带 `run`；⑧ 计划页在那条消息下列出可折叠的「本轮依据」（工具名 + 结果摘要 + 为什么停下）
  - Verify：单测盯「先读计划再回答」「一轮批量读两样」「非法工具与跨计划参数被拒且不判死这一轮」「撞上限时如实说读了什么还缺什么且一条提案不落」「失败也留运行账」；`pytest -q`；前端 lint 与 tsc exit=0。真实模型效果与界面走查归用户
  - Files：`backend/app/agent_runtime.py`（新）、`backend/app/agent_tools.py`（新）、`backend/app/dialogue.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/tests/test_agent.py`（新）、`backend/tests/test_dialogue.py`、`frontend/lib/api.ts`、`frontend/components/plan-dialogue.tsx`、`docs/SPEC.md`（第 10 节新增第 6 条 + 第 11 节两条契约行 + 决策 6 第四次修订 / 37 修订 / **新增决策 40**）
  - 实施记录（2026-09-20）：**循环**按「信封里有没有一个非空的 `tool_calls` 数组」分流——有就是读资料（那一轮里别的东西一概不看，读完它自然会再说一次），没有就交给原有的信封验收器判形状；工具层的错（名字不认识、参数非法）**不中断这一轮**，原文回给模型让它自己换一个，反复问不存在的东西最终撞上限、那一刻才中止。**工具**目录由注册表生成（目录与执行不会走偏），四个工具各有自己的字符上限，读空时说「一条都没有 / 还没有报告」而不是留白。**运行账**成功挂在助手那条回话上、失败挂在你那一句上——所以「它为什么没答上来」查得到；上游挂了（超时、没配 provider）也留一行失败账再往上抛。**删掉**了被新运行时取代的旧上下文拼接（`dialogue.context_text` / `_lineage` / `_blueprints` / `_recent_reports`，前三个搬进 `agent_tools` 成为工具，不保留两套执行路径）。前端加 `Evidence` 折叠块（原生 `<details>`，无新依赖），刷新页面依据还在。
  - 验证：`pytest -q` **354 passed**（T31 结束时 332；`test_dialogue.py` 的既有用例改到新循环上、新增 `test_agent.py` 19 条）、lint/tsc exit=0。

- [x] **T33 失败收口与文档收口**
  - Acceptance：工具不存在、参数非法、模型结构错误、达到调用上限都返回统一中文错误；失败运行**不落计划提案、不改档案**；确认 / 忽略仍走原有台账；SPEC、任务状态与交接文档同步；旧上下文拼接代码删除，不保留两套执行路径
  - Verify：`pytest -q` + `tools\smoke_p1.py`（动了接口契约）；lint 与 tsc exit=0
  - Files：`docs/SPEC.md`、`tasks/todo.md`、`HANDOFF.md`
  - 实施记录（2026-09-20）：四种失败出口都是中文一句话，撞上限时明说「读过哪些、还没读哪些」（撞上限与一直不合格是两种，`agent_run.status` 分别记 `limit` / `failed`）；失败与中止都**不落提案**，你那句话仍留在对话里（再说一句就接着聊）。冒烟脚本 11 步全绿（它没走计划对话这条链，只有回归意义）。
  - **未做（按剥离策略）**：`profile_evidence` 表、`profile_change` 的新增/取代联合协议、来源与置信度与事实时间、档案页显示依据——全部属于被剥掉的记忆那一段，本阶段不做。
  - **走查归用户**：五步里剩下的四步（问「下一步先做什么」看它读了计划、追问「结合最近情况」看它按需读报告、要求改截止日确认前不变、用测试桩触发上限看页面说明缺口）。

## P3.7 候选的路径形状与「不要」的出口（2026-09-20 定；方案见 `docs/候选路径与追问槽方案.md`）

用户 2026-09-20 走查触发：他输入一个明确方向（「学 agent 开发怎么学」），「找」回了一条路上的五个先后步骤，却按五个互相竞争的方向摆出来——每条独立采纳/否决，而否决＝永久拉黑。他的原话：「五个连续性的方案，不应该独立」。同日他还指出追问槽「有追问、没有输入框」。三条一次收口，规格落 SPEC 决策 41（另修订 31/34/35/36 与第 11 节三条契约行）。**T36 的口径由他 2026-09-20 当场定夺：走方案里的口径 A（加输入框）**。

- [x] **T34 候选的路径形状**
  - Acceptance：① `providers/find.py` 的输出契约加必填 `shape`（`directions` / `path`）与 `steps`；提示词写清「只有当这几条明显是同一条路上的先后步骤时才用 path」。② `advisor` 校验新形状（形状缺失/取值不对、`path` 候选≠1 条或步骤不在 2–8、步骤缺 title 或 why、`directions` 却给 steps，一律判不合格带原因重试一次）。③ `path` 落**一行**伞候选，步骤进 `candidate.payload`（**新列**，用户当日授权；老库由 `db.init` 加列迁移补齐）。④ 采纳分支：`path` 采纳＝建伞阶段 + 回执带 `steps`；`blueprint` 的规划对话上下文加「步骤草案」段、`GET /api/plan-chat` 也带 `steps`。⑤ 契约：`GET /api/candidates` 对每条回 `shape` 与 `steps`（从 payload 解出、不外发原始 JSON）；`verdict` 回执带 `shape` 与 `steps`。⑥ 否决分支不变（否决伞候选＝永久禁区，**步骤不进禁区**）；决策 34 的过期按伞候选走。⑦ 清单页一张卡渲染 + 形状标签 + 只读步骤列表
  - Verify：单测盯「path 只落一行伞候选且 payload 带 steps」「步骤条数越界 / 缺 why / path 却给多条候选 / directions 却给 steps 都判不合格」「否决后步骤名不进禁区」「采纳回执带 steps 且建出伞阶段」「老候选（无 payload）按 directions 渲染」「过期按伞候选走」「步骤草案进规划对话上下文」；`pytest -q`、`tools\smoke_p1.py`、前端 lint 与 tsc exit=0。真实模型效果与界面走查归用户
  - Files：`backend/app/providers/find.py`、`backend/app/advisor.py`、`backend/app/blueprint.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/app/db.py`、`backend/tests/test_candidates.py`、`backend/tests/test_blueprint.py`、`backend/tests/test_plans.py`、`frontend/lib/api.ts`、`frontend/app/candidates/page.tsx`、`docs/SPEC.md`（决策 34/36 补充 + 第 11 节三条 + 新增决策 41）
  - 实施记录（2026-09-20）：`FoundList` 加必填 `shape` 与 `steps`（`PathStep`：title / deliverable / why，title 与 why 走 strip 校验，空白不算给了）；`_shape_problem` 管形状与条数是否对得上；`_shape_and_steps` / `candidate_steps` 从 payload 解出形状与草案（解不开或老候选一律 `directions` + 空步骤）。落库只写一行伞候选，payload 只在 `path` 时写；`list_candidates` 把原始 JSON 换成解好的 `shape` / `steps` 再往外发。前端一张卡（形状标签 + 只读步骤列表 + 「采纳整条路 / 否决整条路」），对话区顶部列出草案当底稿。**真库已 `db.init` 补列**（`candidate.payload`），15 条老候选 payload 全为 NULL，按 directions 渲染

- [x] **T35 阶段跳过**
  - Acceptance：① `plan.py` 放开跳过层级（阶段也认，理由必填），完成判定与落后量按「跳过视同完成」口径（其下任务原样留着）；② 路由与请求模型照任务跳过形状（同一条 `/skip`）；③ 计划页阶段行加「跳过这一步」入口（与「改字段」并排，跳过的阶段给一句说明）；④ `api.ts` 加类型与函数
  - Verify：单测盯「阶段跳过留痕、缺理由被拒」「跳过视同完成（`stage_finished` 为真）」「落后量不算它、当前阶段往后走」「周打卡不给跳」；`pytest -q`、lint/tsc exit=0。界面走查归用户
  - Files：`backend/app/plan.py`、`backend/app/main.py`、`backend/tests/test_task_layer.py`、`frontend/lib/api.ts`、`frontend/components/plan-tree.tsx`、`frontend/app/page.tsx`、`docs/SPEC.md`（决策 31 + 新增 41）
  - 实施记录（2026-09-20）：新增 `plan.skip_node`（阶段与任务共用一条路，`SKIPPABLE_LEVELS` 里没有周打卡），`skip_task` 保留原口径（只认任务，转调 `skip_node`），`/skip` 路由改调 `skip_node`，回执多一个 `level` 字段。前端把 `onTask` 改名 `onNode`（阶段与任务共用同一个回调），阶段行加「跳过这一步」按钮与跳过后的说明行。跳过不需要新的落后量/完成判定改动——`node_lag_days` 对 `skipped` 本就返回 None，`stage_finished` 本就把 `skipped` 当终态

- [x] **T36 追问槽收口**
  - Acceptance：① **补真入口**（口径 A）：追问条下加输入框 + 「带着这个回答再问一轮」，提交时把「原问题 + 你的回答」拼成下一轮输入（追问不拼原问题，下一轮模型就丢了前提）；② **收紧问的范围**：提示词与校验都钉死——追问只问档案里缺的那一类事实、一句话能答（长度上限 + 不许换行 + 不许一次问好几件），问「这条路怎么走」判不合格；③ **问过的不再问**：已答的追问（问题 + 回答一句）进该轮的反馈流水行，后续轮次看得到，模型不得重复问同一事实；④ 文案把「追问槽 / 规划对话」各管一段说清
  - Verify：单测盯「规划类追问判不合格并重试」「太长 / 多问号 / 换行的追问判不合格」「关于事实的追问照旧合格」「回答拼成下一轮输入」「已答的追问进反馈流水且 prompt 让模型别再问」「没有待答追问时不吞掉那句话」；`pytest -q`、lint/tsc exit=0
  - Files：`backend/app/advisor.py`、`backend/app/main.py`、`backend/sql/schema.sql`、`backend/app/db.py`、`backend/tests/test_candidates.py`、`frontend/lib/api.ts`、`frontend/app/candidates/page.tsx`、`docs/SPEC.md`（决策 35 两条修订 + 新增 41）
  - 实施记录（2026-09-20）：**追问改为落库**（`learning_request.clarify`，可空 JSON `{question, missing, answer}`）——T25 定的「不落库」挡着「问过的别再问」，因为反馈流水是从库里拼的；它不另立表，追问本就是「这一轮说过什么」的一部分。回答走 `POST /api/requests` 的可选新字段 `clarify_answer`：后端取同一计划归属下最近一轮待答的追问，把它标成已答，并把「原问题 + 我的回答」拼成这一轮的输入；**没有待答追问时**（页面刷新过、或已经答过一遍）那句话作为「补充」缀在 raw_text 后面，不吞掉。追问的判据加了三条：长度上限 60 字、不许换行、问号最多一个，「这条路怎么走」用明确说法表（先学哪 / 什么顺序 / 分几步 / 怎么排 / 取舍…）拦下——只收明确的规划说法，不收「先」「节奏」这类单独的词，免得误伤「你平时的作息节奏」。**真库已 `db.init` 补列**（`learning_request.clarify`）

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
