-- cadence 表结构（唯一来源）
-- 全部用 IF NOT EXISTS，因此 `python -m app.db init` 可重复执行。
-- 刻意避开 SQLite 专有语法，将来上云换 Postgres 时只需改连接串。

-- ========== 长期档案与状态台账 ==========

-- 长期档案五类：生活习惯 / 生活记录 / 当前状态 / 短期目标+痛点 / 长期主线
-- 记忆系统（2026-09-21，见 docs/记忆系统.md）把它当作「全局长期记忆」，并补三列可空元数据：
-- fact_time    这条事实说的是什么时候的状态（不是入库时间）
-- review_at    到了这个时候进「待复核」，Agent 不再默认使用它
-- source_kind  user_stated（用户明确陈述）/ agent_inferred（Agent 推断）/ legacy_manual（历史手工录入）
-- 三列都可空：老库由 db.init 加列迁补齐并统一标成 legacy_manual，**不猜测证据**。
CREATE TABLE IF NOT EXISTS profile_item (
  id            INTEGER PRIMARY KEY,
  category      TEXT    NOT NULL,
  content       TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'active',   -- active / superseded / void
  superseded_by INTEGER,
  fact_time     TEXT,
  review_at     TEXT,
  source_kind   TEXT,
  valid_from    TEXT    NOT NULL,
  created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_active ON profile_item (category, status);

-- 台账流水：每一次状态变更都在这里留痕。
-- 台账的价值就是能回答「上周为什么这么定」，所以旧值只标记不删除。
CREATE TABLE IF NOT EXISTS ledger_event (
  id           INTEGER PRIMARY KEY,
  entity_type  TEXT    NOT NULL,
  entity_id    INTEGER NOT NULL,
  change_type  TEXT    NOT NULL,   -- create / supersede / void / status_change / arbitrate
  before_value TEXT,
  after_value  TEXT,
  reason       TEXT,
  actor        TEXT    NOT NULL,   -- user / agent
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_entity ON ledger_event (entity_type, entity_id);

-- ========== 决策入口 ==========

-- 每轮输入：search（不知道学什么）/ evaluate（判断某个资料值不值得学）
-- plan_id（2026-09-17 T24 加）：这一轮针对哪个计划；为空 = 「新方向（不属于任何计划）」。
-- 候选随请求继承这个归属，采纳时才知道该落进哪个计划。老库由 db.init 的加列迁移补齐。
-- clarify（2026-09-20 T36 加）：这一轮问出的追问（JSON：{question, missing, answer}）。
-- 追问为什么在这里而不另立表：它本就是「这一轮说过什么」的一部分，且「问过的不再问」
-- 要求下一轮读得到——反馈流水是从这张表拼的。answer 为空 = 还没答。
CREATE TABLE IF NOT EXISTS learning_request (
  id         INTEGER PRIMARY KEY,
  kind       TEXT    NOT NULL,
  raw_text   TEXT    NOT NULL,
  plan_id    INTEGER,
  clarify    TEXT,
  created_at TEXT    NOT NULL
);

-- 候选清单：每条都带初判（why / depth_target），审批结果留痕以便去重
-- status：proposed / accepted / rejected / expired（过期 = 新一轮「找」落库时上一轮未裁定的自动过期；
-- 过期 ≠ 否决——不进禁区，模型以后还能再推）
-- payload（2026-09-20 T34 加）：路径形状（shape=path）的**步骤草案**，JSON
-- {shape, steps:[{title, deliverable, why}]}——与 `proposal.payload` 同一用法。
-- 步骤不是候选：不单独裁定、不进禁区；伞候选过期时它跟着失效。
-- landing_plan_id（2026-09-21 T37 加）：**采纳时落进了哪个计划**。
-- 「新方向」的候选（`learning_request.plan_id` 为空）落点是采纳那一刻现选的，此前这个
-- 事实只活在当刻响应与 `plan_chat` 里——刷新一次页面，规划对话就不知道自己在哪个计划里，
-- 又让你「先指定注入的计划」（甚至报「没有计划归属」）。落点是候选自己的事实，记在这里。
CREATE TABLE IF NOT EXISTS candidate (
  id            INTEGER PRIMARY KEY,
  request_id    INTEGER NOT NULL,
  title         TEXT    NOT NULL,
  kind          TEXT,                     -- concept / doc / project / course
  why           TEXT,                     -- 对主线或痛点的贡献（初判）
  depth_target  TEXT,                     -- 浅尝 / 够用 / 熟练 / 精通
  rank          INTEGER,
  is_recommended INTEGER NOT NULL DEFAULT 0,
  payload       TEXT,
  landing_plan_id INTEGER,                -- 采纳时落进的计划；未采纳为空
  status        TEXT    NOT NULL DEFAULT 'proposed',  -- proposed / accepted / rejected
  superseded_by INTEGER,
  reject_reason TEXT,
  valid_from    TEXT    NOT NULL,
  created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_request ON candidate (request_id, status);

-- LLM 提案：档案变更 / 计划重排 / 阶段推进。LLM 只提案，写入必须经人裁定。
CREATE TABLE IF NOT EXISTS proposal (
  id           INTEGER PRIMARY KEY,
  kind         TEXT    NOT NULL,   -- profile_change / plan_replan / stage_advance
  payload      TEXT    NOT NULL,   -- JSON
  reason       TEXT,
  status       TEXT    NOT NULL DEFAULT 'pending',   -- pending / accepted / rejected
  superseded_by INTEGER,
  decided_at   TEXT,
  valid_from   TEXT    NOT NULL,
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposal_status ON proposal (status);

-- ========== 计划表（三级：计划 → 阶段=可验证交付物 → { 任务、周检查点 }） ==========
-- 与 `plan_node.level` 一样，status 的取值是自由文本、数据库不加约束（加取值不用迁移）：

CREATE TABLE IF NOT EXISTS plan (
  id         INTEGER PRIMARY KEY,
  goal       TEXT    NOT NULL,
  status     TEXT    NOT NULL DEFAULT 'active',   -- active / paused / closed / void
  superseded_by INTEGER,
  valid_from TEXT    NOT NULL,
  created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_node (
  id           INTEGER PRIMARY KEY,
  plan_id      INTEGER NOT NULL,
  parent_id    INTEGER,                 -- 阶段为空，任务与检查点指向所属阶段
  level        TEXT    NOT NULL,        -- stage / task / checkpoint
  title        TEXT    NOT NULL,
  deliverable  TEXT,                    -- 阶段：可验证的交付物描述
  depth_target TEXT,
  due_date     TEXT,
  status       TEXT    NOT NULL DEFAULT 'not_started',
                                        -- not_started / in_progress / done / stuck / skipped
  sort_order   INTEGER NOT NULL DEFAULT 0,
  superseded_by INTEGER,
  valid_from   TEXT    NOT NULL,
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_plan ON plan_node (plan_id, parent_id, sort_order);

-- 报告：执行世界回到系统的唯一信号
CREATE TABLE IF NOT EXISTS report (
  id                INTEGER PRIMARY KEY,
  node_id           INTEGER NOT NULL,
  status            TEXT    NOT NULL,   -- done / partial / stuck / skipped
  note              TEXT,               -- 一句话说明
  artifact_url      TEXT,               -- 可选：产物（仓库 / URL / 截图）
  material_feedback TEXT,               -- 可选：资料评价，用于修正后续推荐
  created_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_node ON report (node_id, created_at);

-- ========== 触达 ==========

CREATE TABLE IF NOT EXISTS notification_log (
  id       INTEGER PRIMARY KEY,
  channel  TEXT    NOT NULL,   -- email / none
  kind     TEXT,               -- weekly_checkpoint / makeup
  subject  TEXT,
  body     TEXT,
  ok       INTEGER NOT NULL DEFAULT 1,
  error    TEXT,
  sent_at  TEXT    NOT NULL
);

-- ========== LLM 接入（多家提供商 + 调用记账） ==========

CREATE TABLE IF NOT EXISTS llm_provider (
  id            INTEGER PRIMARY KEY,
  name          TEXT    NOT NULL UNIQUE,
  base_url      TEXT,
  api_key       TEXT,                     -- 只存本地库；界面只显示掩码，永不回传明文
  default_model TEXT,
  is_default    INTEGER NOT NULL DEFAULT 0,
  enabled       INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT    NOT NULL
);

-- 调用记账：不设预算上限，改为可查可汇总 + 循环保护
CREATE TABLE IF NOT EXISTS llm_call (
  id            INTEGER PRIMARY KEY,
  provider_id   INTEGER,
  model         TEXT,
  task          TEXT,                     -- judge / candidates / split / compress / other
  input_tokens  INTEGER,
  output_tokens INTEGER,
  duration_ms   INTEGER,
  ok            INTEGER NOT NULL DEFAULT 1,
  error         TEXT,
  created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_call_time ON llm_call (created_at);

-- 任务 → 模型映射：轻活走便宜模型、重活走强模型
CREATE TABLE IF NOT EXISTS task_model_map (
  task        TEXT PRIMARY KEY,           -- judge / candidates / split / compress
  provider_id INTEGER,
  model       TEXT,
  updated_at  TEXT
);

-- ========== 对话式规划（SPEC 决策 36） ==========

-- 采纳一条候选之后、生成蓝图之前的那段对话。**追加式**：一行一句话，不写台账
-- （同 learning_request 的先例——它记的是「我说过什么」，不是有状态的对象）。
-- 每行的归属同时记住 plan_id 与 candidate_id：这段对话是「围绕这条候选、进这个计划」的。
CREATE TABLE IF NOT EXISTS plan_chat (
  id           INTEGER PRIMARY KEY,
  plan_id      INTEGER NOT NULL,
  candidate_id INTEGER NOT NULL,
  role         TEXT    NOT NULL,   -- user / assistant
  content      TEXT    NOT NULL,   -- 用户原话；助手那侧存它输出的 JSON 原文
  created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_chat_thread ON plan_chat (candidate_id, plan_id, id);

-- 交付物提交：阶段上的独立动作（决策 32）。每次提交落一行——重提交 = 新行，
-- 旧值天然留痕；「当前交付物」= 最新那一行。不进 report 表：报告说的是
-- 「这一周怎么样」，交付物说的是「这个阶段交出了什么」，两件事。
CREATE TABLE IF NOT EXISTS deliverable_submission (
  id         INTEGER PRIMARY KEY,
  node_id    INTEGER NOT NULL,
  url        TEXT    NOT NULL,
  note       TEXT    NOT NULL,
  created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliverable_node ON deliverable_submission (node_id, created_at);

-- ========== 计划级对话（T28：蓝图落地之后接着聊） ==========

-- 一段跟着计划走的长期对话（决策 37）。与 `plan_chat` 的分工：
-- `plan_chat` 是「定方向」的短程对话（绑候选、有 6 轮上限、终点是出一棵蓝图），
-- 这张表是「执行期」的长期对话（绑计划、不限轮数，只用历史字符上限控成本）。
-- 两者生命周期、上限、能产出的东西都不同，所以不塞进同一张表——分开之后，
-- 每条查询不必先判断「这是哪种对话」。
--
-- 同样是**追加式日志**、不经台账（同 learning_request 的先例）：它记的是「我们说过
-- 什么」，不是有状态的对象。
CREATE TABLE IF NOT EXISTS plan_dialogue (
  id         INTEGER PRIMARY KEY,
  plan_id    INTEGER NOT NULL,
  role       TEXT    NOT NULL,   -- user / assistant
  content    TEXT    NOT NULL,   -- 用户原话；助手那侧存它的原话（人话，不是 JSON）
  created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_dialogue_thread ON plan_dialogue (plan_id, id);

-- ========== Agent 运行记录（2026-09-20：受控工具循环的解释账） ==========

-- 用户每说一句、Agent 跑一轮，落一行。**只记「这一轮它做了什么」**：调了几次模型、
-- 读了哪几样资料、为什么停下——不重复保存计划与档案正文（那些是真表，要看看原表）。
-- 模型成本仍归 `llm_call` 管，这张表管的是「解释」（SPEC 决策 40）。
--
-- 它**不是记忆**：Agent 下一轮不会读它，它只是给人排错与对账用的运行审计。
-- dialogue_id 指向 `plan_dialogue` 的那一行：答出来了就挂在那条助手回话上（界面据此
-- 在消息下面显示「本轮依据」），失败时挂在你的那一句上（那一轮没有助手回话）。
CREATE TABLE IF NOT EXISTS agent_run (
  id          INTEGER PRIMARY KEY,
  plan_id     INTEGER NOT NULL,
  dialogue_id INTEGER,           -- 挂在哪条对话消息上；关联靠应用层，不加外键
  status      TEXT    NOT NULL,  -- ok 答完 / limit 达到上限 / failed 没给出合格输出
  stop_reason TEXT    NOT NULL,  -- 中文一句话：为什么停下（界面直接显示）
  model_calls INTEGER NOT NULL DEFAULT 0,
  tool_calls  INTEGER NOT NULL DEFAULT 0,
  tools       TEXT,              -- JSON 数组：[{name, args, ok, summary, duration_ms}]
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_run_dialogue ON agent_run (dialogue_id, id);

-- ========== 记忆系统（2026-09-21，方案见 docs/记忆系统.md） ==========
--
-- 三层分工：**工作记忆**沿用 `agent_runtime` 本轮目标与工具结果（运行结束清空，不落表）；
-- **经历记忆**不复制数据，统一从既有的六类记录里现查（见 `app/memory.py`）；
-- **长期记忆**分两级——全局的沿用 `profile_item`，计划内的存下面这张 `plan_memory`。
-- `agent_run` 与 `ledger_event` 仍是**运行审计**，不作为记忆内容交给模型。

-- 计划内记忆：这个计划里的约束 / 决定 / 偏好（不是全局档案，也不污染别的计划与四问判断）。
-- 与 `profile_item` 同样是**有状态的对象**：active / superseded / void，状态变更一律走台账
-- （`ledger.SPECS` 里注册了它，supports_lifecycle=True）——「取代」在这里语义是成立的：
-- 它没有表达否决的业务终态，旧值该被标 superseded 而不是删掉。
-- kind：constraint（约束）/ decision（决定）/ preference（偏好）。
CREATE TABLE IF NOT EXISTS plan_memory (
  id            INTEGER PRIMARY KEY,
  plan_id       INTEGER NOT NULL,
  kind          TEXT    NOT NULL,
  content       TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'active',   -- active / superseded / void
  superseded_by INTEGER,
  fact_time     TEXT,
  review_at     TEXT,
  source_kind   TEXT,                                -- user_stated / agent_inferred
  valid_from    TEXT    NOT NULL,
  created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_memory ON plan_memory (plan_id, status);

-- 来源证据：一条长期记忆可以有多条来源。**摘录只存原话片段**，不复制整段经历。
-- scope 说明 memory_id 指向哪张表（global → profile_item / plan → plan_memory）——
-- 两张记忆表各自的主键空间，所以关联靠应用层，不加外键（同 `agent_run.dialogue_id` 的先例）。
-- source_type 六取值与「经历记忆」的六类一一对应：plan_dialogue / plan_chat / report /
-- candidate / proposal / field_change。
CREATE TABLE IF NOT EXISTS memory_evidence (
  id          INTEGER PRIMARY KEY,
  scope       TEXT    NOT NULL,   -- global / plan
  memory_id   INTEGER NOT NULL,
  source_type TEXT    NOT NULL,
  source_id   INTEGER NOT NULL,
  excerpt     TEXT    NOT NULL,
  source_time TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_evidence ON memory_evidence (scope, memory_id);

-- 记忆扫描：手动 / 每周 / 计划收尾三种触发各记一行。
-- `cursor` 是**成功之后**才推进的游标——JSON `{来源类型: 已处理到的最大 id}`，下一批从它之后接着取；
-- 「没有候选」也是成功（游标照样推进），失败则**不动游标、不落任何候选**（下一批重来）。
-- plan_id 为空 = 扫的是全局（跨计划）那一批。
CREATE TABLE IF NOT EXISTS memory_scan (
  id          INTEGER PRIMARY KEY,
  trigger     TEXT    NOT NULL,   -- manual / weekly / plan_close
  plan_id     INTEGER,
  status      TEXT    NOT NULL,   -- ok / failed（pending 只出现在「计划收尾登记待扫描」那一步）
  cursor      TEXT,               -- JSON：这次扫到哪了（成功才写）
  scanned     INTEGER NOT NULL DEFAULT 0,
  candidates  INTEGER NOT NULL DEFAULT 0,
  error       TEXT,
  created_at  TEXT    NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_scan ON memory_scan (plan_id, status, id);

-- 彻底删除的墓碑：**只记对象类型、时间和影响范围，不含任何原文**——
-- 它的用途是「证明这件事发生过」，不是「让你日后还能拼回来」。
-- plan_id（2026-09-21 走查整改加，可空）：计划内记忆被彻底删除后，记忆行已经没了，
-- 只有墓碑知道「这件事是哪个计划里的」——没有它就算不出「某个计划里刚删过一条」。
-- 老行留空（那些删除发生在加列之前，当时的计划归属已不可考）。
CREATE TABLE IF NOT EXISTS memory_deletion (
  id           INTEGER PRIMARY KEY,
  scope        TEXT    NOT NULL,   -- global / plan
  memory_id    INTEGER NOT NULL,
  plan_id      INTEGER,            -- 计划内记忆是哪个计划；全局记忆为空
  reason       TEXT,
  affected     INTEGER NOT NULL DEFAULT 0,   -- 这次一共清掉了几处正文（记忆 + 证据 + 候选 + 来源原文）
  deleted_at   TEXT    NOT NULL
);
