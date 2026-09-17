-- cadence 表结构（唯一来源）
-- 全部用 IF NOT EXISTS，因此 `python -m app.db init` 可重复执行。
-- 刻意避开 SQLite 专有语法，将来上云换 Postgres 时只需改连接串。

-- ========== 长期档案与状态台账 ==========

-- 长期档案五类：生活习惯 / 生活记录 / 当前状态 / 短期目标+痛点 / 长期主线
CREATE TABLE IF NOT EXISTS profile_item (
  id            INTEGER PRIMARY KEY,
  category      TEXT    NOT NULL,
  content       TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'active',   -- active / superseded / void
  superseded_by INTEGER,
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
CREATE TABLE IF NOT EXISTS learning_request (
  id         INTEGER PRIMARY KEY,
  kind       TEXT    NOT NULL,
  raw_text   TEXT    NOT NULL,
  created_at TEXT    NOT NULL
);

-- 候选清单：每条都带初判（why / depth_target），审批结果留痕以便去重
CREATE TABLE IF NOT EXISTS candidate (
  id            INTEGER PRIMARY KEY,
  request_id    INTEGER NOT NULL,
  title         TEXT    NOT NULL,
  kind          TEXT,                     -- concept / doc / project / course
  why           TEXT,                     -- 对主线或痛点的贡献（初判）
  depth_target  TEXT,                     -- 浅尝 / 够用 / 熟练 / 精通
  rank          INTEGER,
  is_recommended INTEGER NOT NULL DEFAULT 0,
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

-- ========== 计划表（两级：阶段=可验证交付物 + 周检查点） ==========

CREATE TABLE IF NOT EXISTS plan (
  id         INTEGER PRIMARY KEY,
  goal       TEXT    NOT NULL,
  status     TEXT    NOT NULL DEFAULT 'active',   -- active / closed
  superseded_by INTEGER,
  valid_from TEXT    NOT NULL,
  created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_node (
  id           INTEGER PRIMARY KEY,
  plan_id      INTEGER NOT NULL,
  parent_id    INTEGER,                 -- 阶段为空，检查点指向所属阶段
  level        TEXT    NOT NULL,        -- stage / checkpoint
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
