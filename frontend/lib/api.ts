/**
 * 前端跟后端说话的**唯一出口**。
 *
 * 为什么要有这个文件：页面里直接写 `fetch("http://localhost:8000/...")` 的话，
 * 地址、错误处理、返回类型会在每个页面各写一遍，改一处就会漏一处。这里收成一处：
 * 改后端地址只改 `API_BASE_URL`，改错误处理只改 `request()`。
 *
 * 边界（SPEC 第 10 节三条铁律）：
 * - 这里**不算业务规则**：什么算落后、阶段算不算完成，都是后端算好给的。
 * - 这里**不存业务状态**：只负责搬运，不缓存成"前端自己的一份真相"。
 *
 * 契约来源是后端的 `/openapi.json`，下面的类型是它的手抄本——后端改了契约，这里要跟着改。
 */

// 后端地址：只在这一个地方出现。
// `NEXT_PUBLIC_` 前缀是 Next.js 的约定：只有带这个前缀的环境变量才会被注入浏览器端代码
// （不带前缀的只在服务器端可用）。没配置就回落到本机的默认端口。
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// ---------- 后端返回的数据形状（照抄 GET /api/plan） ----------

/** 节点状态：与后端 NODE_STATUSES 一致。 */
export type NodeStatus = "not_started" | "in_progress" | "done" | "stuck" | "skipped";

/** 阶段进度：后端 stage_completion() 的返回。 */
export type StageProgress = {
  stage_id: number;
  total: number;
  settled: number;
  done: number;
  skipped: number;
  complete: boolean;
  open_titles: string[];
};

export type Checkpoint = {
  id: number;
  title: string;
  status: NodeStatus;
  due_date: string | null;
  sort_order: number;
  /** 落后天数；null = 无从判断（没定完成日，或已跳过）。 */
  lag_days: number | null;
};

export type Stage = {
  id: number;
  title: string;
  deliverable: string | null;
  due_date: string | null;
  status: NodeStatus;
  sort_order: number;
  lag_days: number | null;
  progress: StageProgress;
  checkpoints: Checkpoint[];
};

export type PlanTree = {
  /** 没有计划时后端返回 null（不是错误）。 */
  plan: { id: number; goal: string; status: string; valid_from: string } | null;
  current_stage: {
    id: number;
    title: string;
    deliverable: string | null;
    status: NodeStatus;
    progress: StageProgress;
  } | null;
  lag: {
    lag_days: number;
    behind: boolean;
    worst: { id: number; title: string; due_date: string | null; lag_days: number } | null;
  };
  stages: Stage[];
};

// ---------- 错误：按后端「方案 C」的统一形状 ----------

/** 字段级错误明细。`loc` 形如 `["body", "due_date"]`，可用来定位到具体输入框。 */
export type ApiFieldError = {
  loc: (string | number)[];
  msg: string;
  type: string;
};

/**
 * 后端返回的错误。
 *
 * 为什么单独一个类：后端所有错误出口都是 `{detail, errors}` —— `detail` 是给人看的中文
 * 一句话，`errors` 是给程序用的字段级明细。页面拿到 `ApiError` 就能直接显示 `message`，
 * 将来表单要标红某个输入框时再用 `errors[].loc`。
 */
export class ApiError extends Error {
  readonly status: number;
  readonly errors: ApiFieldError[];

  constructor(status: number, detail: string, errors: ApiFieldError[] = []) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.errors = errors;
  }
}

/** 从错误响应体里取出人话与字段明细；形状不对时给一句兜底，不抛新的异常。 */
function readErrorBody(body: unknown): { detail: string; errors: ApiFieldError[] } {
  if (body && typeof body === "object" && "detail" in body) {
    const { detail, errors } = body as { detail: unknown; errors?: unknown };
    return {
      detail: typeof detail === "string" ? detail : JSON.stringify(detail),
      errors: Array.isArray(errors) ? (errors as ApiFieldError[]) : [],
    };
  }
  return { detail: "后端返回了无法识别的错误格式", errors: [] };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    // 网络层就失败了。浏览器出于安全考虑**不会**告诉脚本具体原因，所以这里把
    // 三种可能都列出来——否则页面上只会看到一句 "Failed to fetch"，无从下手。
    throw new ApiError(
      0,
      `连不上后端（${API_BASE_URL}）。可能原因：后端没在跑、地址写错了、` +
        `或者浏览器的跨源被拦（后端只放行本机 3000 端口的前端）。`,
    );
  }

  const raw = await response.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = null; // 非 JSON（例如代理返回的 HTML 错误页），交给下面的分支处理
    }
  }

  if (!response.ok) {
    const { detail, errors } = readErrorBody(body);
    throw new ApiError(response.status, detail, errors);
  }
  return body as T;
}

// ---------- 接口 ----------

/**
 * 取计划全貌：计划 + 节点树 + 当前阶段 + 落后量。
 *
 * 不传 `planId` 时后端取最新建的那个有效计划，所以 P2 页面不需要先知道 id。
 */
export async function getPlan(planId?: number): Promise<PlanTree> {
  const query = planId === undefined ? "" : `?plan_id=${planId}`;
  return request<PlanTree>(`/api/plan${query}`);
}

// ---------- 报告（写接口，闭环的第一段） ----------

/** 你提交报告时的四选一，与后端 REPORT_STATUSES 一致。 */
export type ReportStatus = "done" | "partial" | "stuck" | "skipped";

/** 提交成功后的回执：后端把「状态从什么变成什么」一并返回，前端不必自己推断。 */
export type ReportResult = {
  report_id: number;
  node_id: number;
  report_status: ReportStatus;
  node_status_before: NodeStatus;
  node_status: NodeStatus;
  /** 阶段因此收尾时，后端会产出一条推进提案，这里给它的 id。 */
  proposal_id: number | null;
};

/**
 * 提交一条报告：状态四选一 + 一句话（必填），产物与资料评价可选。
 *
 * 这是闭环的第一段——你在别处学完，回来告诉系统结果。
 */
export async function submitReport(input: {
  nodeId: number;
  status: ReportStatus;
  note: string;
  artifactUrl?: string;
  materialFeedback?: string;
}): Promise<ReportResult> {
  return request<ReportResult>("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      node_id: input.nodeId,
      status: input.status,
      note: input.note,
      artifact_url: input.artifactUrl ?? null,
      material_feedback: input.materialFeedback ?? null,
    }),
  });
}

// ---------- 建计划与建节点（写接口） ----------

/** 节点层级：阶段=可验证交付物，检查点=周检查点。与后端 NodeIn.level 一致。 */
export type NodeLevel = "stage" | "checkpoint";

/** 建计划的回执。 */
export type CreatedPlan = { id: number; goal: string };

/** 建节点的回执。 */
export type CreatedNode = { id: number; level: NodeLevel; title: string };

/** 建一个计划。`goal` 是唯一必填项。 */
export async function createPlan(goal: string): Promise<CreatedPlan> {
  return request<CreatedPlan>("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal }),
  });
}

/**
 * 建一个阶段或检查点。
 *
 * 两级结构的一致性（阶段不能带 parentId；检查点必须指向同一计划里的阶段）由后端把关，
 * 这里只管把参数送过去——前端不重复实现业务规则。
 *
 * `dueDate` 必须是**零填充的 ISO 日期**（如 `2026-09-30`）：后端只认这种写法，
 * `2026-9-3` 会被 `422` 拒掉。HTML 的 `input type="date"` 正好产出这个格式。
 */
export async function createNode(input: {
  planId: number;
  level: NodeLevel;
  title: string;
  parentId?: number | null;
  deliverable?: string | null;
  dueDate?: string | null;
}): Promise<CreatedNode> {
  return request<CreatedNode>("/api/plan/nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plan_id: input.planId,
      level: input.level,
      title: input.title,
      parent_id: input.parentId ?? null,
      deliverable: input.deliverable ?? null,
      due_date: input.dueDate ?? null,
      sort_order: 0,
    }),
  });
}

// ---------- LLM 提供商（T10 的接口，T11 页面用） ----------

/**
 * 一家已配置的提供商。
 *
 * **这里没有明文密钥**——后端有条铁律叫"密钥只写不读"：新增或修改时你把明文交上去，
 * 之后任何响应里只剩 `api_key_masked`（形如 `****abcd`；短于 8 位的直接全掩成 `****`）。
 * 结果是界面上**永远回填不了密钥**，想换密钥只能整把换新的——这不是缺陷，是设计。
 */
export type Provider = {
  id: number;
  name: string;
  base_url: string | null;
  default_model: string | null;
  /** 默认那家全库只有一家：不指定用哪家时，后端挑的就是它。 */
  is_default: boolean;
  /** 停用后后端会拒绝拿它干活（`resolve_provider` 抛错），但配置还留着。 */
  enabled: boolean;
  has_api_key: boolean;
  api_key_masked: string | null;
  created_at: string;
};

/** 列出所有提供商。密钥只有掩码。 */
export async function listProviders(): Promise<Provider[]> {
  const data = await request<{ providers: Provider[] }>("/api/providers");
  return data.providers;
}

/** 新增一家的入参。`name` 全库唯一，重名后端回 409。 */
export type ProviderInput = {
  name: string;
  baseUrl?: string;
  apiKey?: string;
  defaultModel?: string;
  setAsDefault?: boolean;
};

/** 新增一家。明文密钥从这里交出去，回来的已经只剩掩码。 */
export async function createProvider(input: ProviderInput): Promise<Provider> {
  return request<Provider>("/api/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      base_url: input.baseUrl ?? null,
      api_key: input.apiKey ?? null,
      default_model: input.defaultModel ?? null,
      set_as_default: input.setAsDefault ?? false,
    }),
  });
}

/** 改一家的入参：字段都可选，只改传了的。 */
export type ProviderPatch = {
  name?: string;
  baseUrl?: string;
  apiKey?: string;
  defaultModel?: string;
  enabled?: boolean;
  setAsDefault?: boolean;
};

/**
 * 改一家的部分字段（改名 / 地址 / 密钥 / 模型 / 启用停用 / 设为默认）。
 *
 * **空值一律不放请求体**，因为后端把"没传这个字段"当成"别动它"。为什么非要这么较真：
 * 界面拿不到明文密钥，如果空着就当清空，你只改个名字就会顺手把钥匙擦掉。
 * 空串对 `base_url` 也不送——否则会把地址改成空字符串，那不是你的本意。
 */
export async function updateProvider(providerId: number, patch: ProviderPatch): Promise<Provider> {
  const body: Record<string, unknown> = {};
  if (patch.name) body.name = patch.name;
  if (patch.baseUrl) body.base_url = patch.baseUrl;
  if (patch.apiKey) body.api_key = patch.apiKey;
  if (patch.defaultModel) body.default_model = patch.defaultModel;
  // 布尔值只看"传没传"，所以要用 `!== undefined` 判断——`false` 也是有效意图
  if (patch.enabled !== undefined) body.enabled = patch.enabled;
  if (patch.setAsDefault !== undefined) body.set_as_default = patch.setAsDefault;

  return request<Provider>(`/api/providers/${providerId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** 删一家。它的**调用记账不会跟着删**——账是历史，provider 没了账还得留着。 */
export async function deleteProvider(providerId: number): Promise<{ id: number; deleted: boolean }> {
  return request<{ id: number; deleted: boolean }>(`/api/providers/${providerId}`, {
    method: "DELETE",
  });
}

/** 体检结果。`detail` 是给人看的一句话（通了说用了哪个模型多久，没通说错在哪）。 */
export type ProviderTestResult = { ok: boolean; detail: string };

/**
 * 发一个最小请求测连通性。
 *
 * 注意它**失败也返回 200**：「通不通」是这个接口的返回值，不是 HTTP 层错误。
 * 所以调用方要判 `result.ok`，而不是 try/catch——配错地址、密钥失效都是常事，
 * 你要的是"结果 + 原因"，不是一个异常。
 */
export async function testProvider(providerId: number): Promise<ProviderTestResult> {
  return request<ProviderTestResult>(`/api/providers/${providerId}/test`, { method: "POST" });
}

// ---------- 决策入口：四问判断（T12 的两条接口，T14 之前的临时入口） ----------

/** 长期档案的一条。`category` 是约定词表里的英文令牌（如 `long_axis`）。 */
export type ProfileItem = {
  id: number;
  category: string;
  content: string;
  valid_from: string;
};

/** 五类档案的当前有效值，外加哪几类还空着。 */
export type ProfileView = {
  items: ProfileItem[];
  missing_categories: string[];
};

export async function getProfile(): Promise<ProfileView> {
  return request<ProfileView>("/api/profile");
}

// ---------- 档案写入（录入 / 取代 / 作废） ----------

/** 五类档案的约定令牌与中文名。令牌必须用这五个——否则后端的「缺失类别」判断会不准。 */
export const PROFILE_CATEGORIES = {
  life_habit: "生活习惯（睡眠/运动/作息）",
  life_log: "生活记录（日程/课程/近况）",
  current_state: "当前状态（精力/时间/压力）",
  short_term_goal: "短期目标 + 当下痛点",
  long_axis: "长期主线（职业方向）",
} as const;

export type ProfileCategory = keyof typeof PROFILE_CATEGORIES;

/** 补一条档案的回执。 */
export type CreatedProfileItem = { id: number; category: ProfileCategory; content: string };

/**
 * 往档案里补一条。
 *
 * 档案**不是键值对**：同一类别允许多条并存（比如两条短期目标），互不挤掉；
 * 想换掉旧的，用 `updateProfileItem`（取代）或 `voidProfileItem`（作废），别靠重新添加。
 */
export async function createProfileItem(input: {
  category: ProfileCategory;
  content: string;
}): Promise<CreatedProfileItem> {
  return request<CreatedProfileItem>("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category: input.category, content: input.content }),
  });
}

/**
 * 改一条档案的内容的回执。
 *
 * 后端实现是台账「取代」：旧值标记 superseded 留痕，返回的 `id` 是**新条目**的 id
 * （`superseded` 是被换下的旧 id）——四问答案里引用过的旧 id 从此指向历史，这是有意的。
 */
export type UpdatedProfileItem = {
  id: number;
  superseded: number;
  category: string;
  content: string;
};

/** 改一条档案的内容。`reason` 必填：台账要能回答「为什么改」。 */
export async function updateProfileItem(input: {
  itemId: number;
  content: string;
  reason: string;
}): Promise<UpdatedProfileItem> {
  return request<UpdatedProfileItem>(`/api/profile/${input.itemId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: input.content, reason: input.reason }),
  });
}

/** 作废一条档案（不物理删除，台账留痕）。`reason` 必填。 */
export async function voidProfileItem(
  itemId: number,
  reason: string,
): Promise<{ id: number; voided: boolean }> {
  return request<{ id: number; voided: boolean }>(`/api/profile/${itemId}/void`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

/** 四问中的一问：一句话答案 + 它依据的档案 id。 */
export type JudgmentAnswer = {
  answer: string;
  profile_item_ids: number[];
};

/** 四问答案。键名与后端 `advisor.QUESTIONS` 一致，**少一问后端就不收**。 */
export type Judgment = {
  worth_learning: JudgmentAnswer;
  depth_target: JudgmentAnswer;
  intensity: JudgmentAnswer;
  time_budget: JudgmentAnswer;
};

/** 这次判断的"粮草"情况：依据了几条档案、哪几类缺席。 */
export type ProfileBasis = {
  total: number;
  missing_categories: string[];
};

export type AskResult = {
  request_id: number;
  proposal_id: number;
  kind: string;
  judgment: Judgment;
  profile_basis: ProfileBasis;
  /** 这次用了几次模型调用（后端上限是 2：一次 + 一次重试）。 */
  calls: number;
};

/**
 * 提交「我发现了某个资料，要不要学」，拿回四问判断。
 *
 * **它只产出提案**：返回的 `proposal_id` 是一条 `pending` 提案，你还没裁定，
 * 档案和计划一个字都没改。
 */
export async function askMaterial(rawText: string): Promise<AskResult> {
  return request<AskResult>("/api/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "evaluate", raw_text: rawText }),
  });
}

// ---------- 「找」：候选清单（T13） ----------

/** 一条候选：标题 + 为什么对你有用 + 建议深度 + 依据的档案 id。 */
export type FoundCandidate = {
  title: string;
  /** concept / doc / project / course（概念 / 资料 / 项目 / 课程）。 */
  kind: string;
  why: string;
  /** 浅尝 / 够用 / 熟练 / 精通。 */
  depth_target: string;
  profile_item_ids: number[];
};

export type FindResult = {
  request_id: number;
  kind: "search";
  /** 与 `candidates` 一一对应、同顺序：裁决时要用它们。 */
  candidate_ids: number[];
  /** 顺序即优先级（后端已按 rank 排好，前端不再自己排）。 */
  candidates: FoundCandidate[];
  /** 「建议先从哪条开始」的那条标题（一字不差复制自 candidates）。 */
  recommended_start: string;
  start_reason: string;
  /** 候选来源自述。`networked: false` = 甲档（不联网，只给路线建议）。 */
  source: { name: string; networked: boolean };
  profile_basis: ProfileBasis;
  /** 这次被当成禁区的标题（你以前否决过的）。 */
  banned_titles: string[];
  calls: number;
};

/**
 * 提交「我不知道该学什么」，拿回 3–5 条带排序的候选。
 *
 * 走的是同一个 `POST /api/requests`，只是 `kind` 不同。落成的是 `proposed` 候选，
 * 等你在界面上采纳 / 否决——**否决过的标题会成为下次的禁区**。
 */
export async function findCandidates(rawText: string): Promise<FindResult> {
  return request<FindResult>("/api/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "search", raw_text: rawText }),
  });
}

/** 已落库的一条候选。`status` 是 proposed / accepted / rejected。 */
export type CandidateRow = {
  id: number;
  title: string;
  kind: string;
  why: string;
  depth_target: string;
  rank: number;
  is_recommended: number;
  status: string;
  reject_reason: string | null;
};

export type CandidateList = {
  /** 还没问过时是 null（不是错误）。 */
  request_id: number | null;
  raw_text: string | null;
  created_at?: string | null;
  candidates: CandidateRow[];
  recommended: CandidateRow | null;
};

/** 取候选清单。不传 `requestId` 就取最近一轮有候选的那次「找」。 */
export async function listCandidates(requestId?: number): Promise<CandidateList> {
  const query = requestId === undefined ? "" : `?request_id=${requestId}`;
  return request<CandidateList>(`/api/candidates${query}`);
}

/** 采纳或否决的回执。 */
export type VerdictResult = {
  id: number;
  status: string;
  reject_reason: string | null;
  /** 采纳时后端自动落的阶段：建进了哪个计划（最新 active 的那个）。否决时为 null。 */
  plan_id: number | null;
  /** 自动建出的阶段节点 id。否决时为 null。 */
  node_id: number | null;
};

/**
 * 采纳 / 否决一条候选。
 *
 * 否决**必须写理由**（缺理由后端回 400）：理由进台账，并成为下次「找」的禁区——
 * 这是「你否决过的候选不再出现」的入口。
 * 采纳（2026-09-17 起）会在最新 active 计划里自动建一个同名阶段；落不了阶段
 * （没有 active 计划、有同名未收尾阶段）回 409，候选保持 proposed 可重试。
 */
export async function verdictCandidate(
  candidateId: number,
  accept: boolean,
  reason?: string,
): Promise<VerdictResult> {
  return request<VerdictResult>(`/api/candidates/${candidateId}/verdict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accept, reason: reason ?? null }),
  });
}

// ---------- 调用记账（T10 的接口，之前只能在 /docs 里看） ----------

/** 一次模型调用的流水。失败时上游常常不给用量，所以 tokens 允许为 null。 */
export type LlmCall = {
  id: number;
  /** 只有 id——provider 删了账还在，所以要展示名字得自己去 providers 列表里对。 */
  provider_id: number;
  model: string;
  /** 任务名，如 `judge`（四问）、`connectivity_test`（体检）。 */
  task: string;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  ok: boolean;
  error: string | null;
  created_at: string;
};

/** 按 ISO 周汇总。`week` 形如 `2026-W38`，与导出的周文件名同一套写法。 */
export type LlmWeekSummary = {
  week: string;
  calls: number;
  ok: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  errors: string[];
};

export type LlmCalls = {
  /** 最近流水，新的在前。 */
  calls: LlmCall[];
  /** 按周汇总，新的在前，最多 8 周。 */
  by_week: LlmWeekSummary[];
};

/**
 * 取调用记账：最近流水 + 按周汇总。
 *
 * **纯读库，不碰模型**——看这个不会额外花钱（不设预算上限的前提就是"看得见花了多少"）。
 */
export async function getLlmCalls(limit = 50): Promise<LlmCalls> {
  return request<LlmCalls>(`/api/llm-calls?limit=${limit}`);
}

// ---------- 待裁定提案（T14） ----------

/** `stage_advance` 的 payload：阶段收尾后的「要不要往前走」。 */
export type StageAdvancePayload = {
  plan_id: number;
  stage_id: number;
  stage_title: string;
  /** null = 后面没有更多阶段了；批准这类提案就是把那个计划收尾。 */
  next_stage_id: number | null;
  next_stage_title: string | null;
  settled?: number;
  done?: number;
  skipped?: number;
  /** 提案当时问你的那句话（与 `reason` 同一句）。 */
  question?: string;
};

/** 重排提案给的一个出路（减量 / 顺延 / 换交付物）。 */
export type ReplanOption = { kind: string; label: string; detail: string };

/** `plan_replan` 的 payload：落后、或整周没报告时的重排建议。 */
export type PlanReplanPayload = {
  week: string;
  plan_id: number;
  stage_id: number | null;
  stage_title: string | null;
  why: string;
  lag_days: number;
  options: ReplanOption[];
};

/** `material_judgment` 的 payload：一次四问判断的结论。 */
export type MaterialJudgmentPayload = {
  source_text: string;
  learning_request_id: number;
  judgment: Judgment;
  profile_basis: ProfileBasis;
};

/**
 * 一条提案。`payload` 的形状随 `kind` 变——页面里按 kind 转成上面三个类型之一
 * （`profile_change` 还没有生产者，没有定型的 payload 形状）。
 */
export type Proposal = {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  /** 提出时的那句话（谁提的、问的是什么）。 */
  reason: string | null;
  created_at: string;
  decided_at: string | null;
};

/** 取待裁定提案，按提出顺序排；`kind` 传了就只取那一类。 */
export async function listProposals(kind?: string): Promise<Proposal[]> {
  const query = kind === undefined ? "" : `?kind=${kind}`;
  const data = await request<{ proposals: Proposal[] }>(`/api/proposals${query}`);
  return data.proposals;
}

/** 裁定的回执。`effect` 说明这次到底动了什么。 */
export type ProposalDecision = {
  id: number;
  kind: string;
  status: string;
  /** `plan_closed` 是唯一的结构性动作；`replan_recorded` 只记下你选的方向；其余纯记账。 */
  effect: "plan_closed" | "replan_recorded" | "recorded_only";
  option: string | null;
};

/**
 * 批准 / 驳回一条提案。
 *
 * 驳回**必须写理由**（缺理由后端回 400）；批准 `plan_replan` 必须从提案给的
 * 三个方向里选一个（`option`）。已裁定过回 409、不存在回 404。
 */
export async function decideProposal(
  proposalId: number,
  input: { approved: boolean; reason?: string; option?: string },
): Promise<ProposalDecision> {
  return request<ProposalDecision>(`/api/proposals/${proposalId}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approved: input.approved,
      reason: input.reason ?? null,
      option: input.option ?? null,
    }),
  });
}
