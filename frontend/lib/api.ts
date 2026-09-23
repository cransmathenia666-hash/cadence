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

/** 阶段进度：后端 stage_completion() 的返回。只数任务（周打卡不进分母）。 */
export type StageProgress = {
  stage_id: number;
  total: number;
  settled: number;
  done: number;
  skipped: number;
  /** 有任务且全收尾（界面显示用；空阶段为 false）。 */
  complete: boolean;
  /** 任务全部收尾——空集也算（完成判定的那一半）。 */
  all_tasks_settled: boolean;
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

/** 任务与检查点同形状（都是阶段下的子节点）。 */
export type TaskNode = Checkpoint;

/** 交付物提交（阶段上的独立动作，可重新提交）。 */
export type DeliverableSubmission = {
  url: string;
  note: string;
  created_at: string;
};

export type Stage = {
  id: number;
  title: string;
  /** 计划时写下的交付物描述（不是提交物本身）。 */
  deliverable: string | null;
  due_date: string | null;
  status: NodeStatus;
  sort_order: number;
  lag_days: number | null;
  progress: StageProgress;
  /** 2026-09-17 起的新判定：任务全收尾 + 交付物已提交。 */
  finished: boolean;
  /** 最新一次交付物提交；没交过就是 null。 */
  deliverable_submission: DeliverableSubmission | null;
  tasks: TaskNode[];
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
  /** 落后时的一句话提醒（T29 起不再产「重排」提案等人裁定）。没落后时为 null。 */
  behind_reason: string | null;
  /** 三个方向（减量 / 顺延 / 换交付物）——只是**建议**，不再有裁定入口。 */
  advice: { kind: string; label: string; detail: string }[];
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

// ---------- 计划的列表与生命周期（T24） ----------

/**
 * 计划列表里的一项。
 *
 * `status` 四态（T27）：`active` 进行中 / `paused` 暂时不做 / `closed` 做完了 /
 * `void` 这件事根本不该做。分界线是**能不能回到进行中**——paused 与 closed 能
 * （走同一个 `reopenPlan`），void 不能（单向门，要重新做就新建一个计划）。
 */
export type PlanSummary = {
  id: number;
  goal: string;
  status: string;
  valid_from: string;
  /** 当前阶段（第一个没收尾的阶段）；都收尾了或还没建阶段时为 null。 */
  current_stage: { id: number; title: string } | null;
  stages: number;
  stages_finished: number;
  /** 离开进行中那一刻的时间；进行中时为 null。暂停 → 继续 → 再收尾后是最后那次。 */
  ended_at: string | null;
  /** 离开进行中那一刻的理由（默认「暂时不做了」/「计划收尾」）；进行中时为 null。 */
  ended_reason: string | null;
};

/** 列出计划。默认只给进行中的；`includeInactive` 连暂停 / 收尾 / 作废的一起给。 */
export async function listPlans(includeInactive = false): Promise<PlanSummary[]> {
  const data = await request<{ plans: PlanSummary[] }>(
    `/api/plans?include_inactive=${includeInactive}`,
  );
  return data.plans;
}

/** 收尾一个计划（做完了）。可重复调用。 */
export async function closePlan(
  planId: number,
  reason?: string,
): Promise<{ plan_id: number; status: string; changed: boolean }> {
  return request(`/api/plans/${planId}/close`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

/** 作废一个计划（这件事根本不该做）。**理由必填**、**单向门**——它进台账。 */
export async function voidPlan(
  planId: number,
  reason: string,
): Promise<{ plan_id: number; status: string }> {
  return request(`/api/plans/${planId}/void`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

/** 暂停一个计划（暂时不做）。可逆的搁置，随时能 `reopenPlan` 回来。理由可选。 */
export async function pausePlan(
  planId: number,
  reason?: string,
): Promise<{ plan_id: number; status: string; changed: boolean }> {
  return request(`/api/plans/${planId}/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

/** 把一个暂停 / 收尾的计划放回进行中（继续做 / 重开）。作废的不给重开。 */
export async function reopenPlan(
  planId: number,
  reason?: string,
): Promise<{ plan_id: number; status: string; changed: boolean }> {
  return request(`/api/plans/${planId}/reopen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

// ---------- 建计划与建节点（写接口） ----------

/** 节点层级：阶段=可验证交付物，检查点=周打卡，任务=要干的活。与后端 NodeIn.level 一致。 */
export type NodeLevel = "stage" | "checkpoint" | "task";

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
 * 建一个阶段、检查点（周打卡）或任务。
 *
 * 三级结构的一致性（阶段不能带 parentId；检查点与任务必须指向同一计划里的阶段）
 * 由后端把关，这里只管把参数送过去——前端不重复实现业务规则。
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

/**
 * 一条路上的一个先后步骤（T34：SPEC 决策 41）。
 *
 * 形状取蓝图阶段的子集（去掉 `tasks`）——「这一步下面拆几件活」是规划对话与蓝图勾选
 * 时的事，「找」只说到「有这一步、它交什么」这一层。
 *
 * **它不是候选**：不单独裁定、不进禁区。要收「这一步先不做」，去蓝图勾选（不建）
 * 或计划里跳过（建了之后收回）。
 */
export type PathStep = {
  title: string;
  /** 这一步交出什么（可空）。 */
  deliverable: string;
  /** 为什么它必须排在这个位置。 */
  why: string;
};

/**
 * 这一轮「找」给的**形状**（T34）：
 * - `directions`：3–5 条**互相竞争**的方向，逐条采纳 / 否决（否决＝永久禁区）；
 * - `path`：**一条路**——1 条伞候选 + 2–8 个先后步骤，整条裁定一次。
 */
export type CandidateShape = "directions" | "path";

export type FindResult = {
  request_id: number;
  kind: "search";
  /** 这一轮针对哪个计划；null = 「新方向（不属于任何计划）」（SPEC 决策 33）。 */
  plan_id: number | null;
  /** 这一轮给的是一条路还是几个方向（T34）。 */
  shape: CandidateShape;
  /** 步骤草案：只有 `shape === "path"` 时非空。 */
  steps: PathStep[];
  /** 与 `candidates` 一一对应、同顺序：裁决时要用它们。 */
  candidate_ids: number[];
  /** 顺序即优先级（后端已按 rank 排好，前端不再自己排）。 */
  candidates: FoundCandidate[];
  /** 「建议先从哪条开始」的那条标题（一字不差复制自 candidates）。 */
  recommended_start: string;
  start_reason: string;
  /**
   * 追问槽位（SPEC 决策 35 ②）：模型觉得档案里缺了某类信息时先问的一句。
   * 它是**关于你的事实**（一句话能答）——「这条路怎么走」是采纳之后规划对话的事。
   * 回答走 `findCandidates` 的第三个参数，后端会把它与这句原问题拼成下一轮输入。
   */
  clarify: { question: string; missing: string } | null;
  /** 候选来源自述。`networked: false` = 甲档（不联网，只给路线建议）。 */
  source: { name: string; networked: boolean };
  profile_basis: ProfileBasis;
  /** 这次被当成禁区的标题（你以前否决过的）。 */
  banned_titles: string[];
  /** 发进 prompt 的反馈流水（最近几轮「找」与你的表态），已按上限截好。 */
  feedback_lines: string[];
  calls: number;
};

/**
 * 提交「我不知道该学什么」，拿回候选（`directions` 时 3–5 条，`path` 时 1 条伞候选 + 步骤）。
 *
 * 走的是同一个 `POST /api/requests`，只是 `kind` 不同。落成的是 `proposed` 候选，
 * 等你在界面上采纳 / 否决——**否决过的标题会成为下次的禁区**。
 *
 * `clarifyAnswer` 是回答上一轮追问的那句话（T36）：后端把它与**原问题**拼成这一轮的输入
 * （不拼上原问题，下一轮模型就丢了前提），并把那条追问标成「已答」进反馈流水，
 * 后续轮次不再重复问同一件事。
 */
export async function findCandidates(
  rawText: string,
  planId?: number | null,
  clarifyAnswer?: string | null,
): Promise<FindResult> {
  return request<FindResult>("/api/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "search",
      raw_text: rawText,
      plan_id: planId ?? null,
      clarify_answer: clarifyAnswer ?? null,
    }),
  });
}

/** 已落库的一条候选。`status` 是 proposed / accepted / rejected / expired（过期≠否决）。 */
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
  /** 这一轮针对的计划；null = 「新方向」。采纳时落点看它（没有就得显式选）。 */
  plan_id: number | null;
  /**
   * **采纳时落进了哪个计划**（T37）。未采纳为 null。
   *
   * 为什么单独一条：`plan_id` 是「这一轮提问针对哪个计划」，而「新方向」的候选它是空的；
   * 落点是采纳那一刻现选的，不在这一列里。规划对话认的就是这个落点——不带上它，刷新一次
   * 页面就会又让你「先指定注入的计划」（甚至报「没有计划归属」）。
   */
  landing_plan_id: number | null;
  /** 形状与步骤草案（T34）。2026-09-20 之前落的老候选是 `directions` + 空步骤。 */
  shape: CandidateShape;
  steps: PathStep[];
};

export type CandidateList = {
  /** 还没问过时是 null（不是错误）。 */
  request_id: number | null;
  raw_text: string | null;
  /** 这一轮的所属计划；null = 「新方向（不属于任何计划）」。 */
  plan_id: number | null;
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
  /** 这条候选的形状（T34）。 */
  shape: CandidateShape;
  /** 采纳一条**路径**候选时带回来的步骤草案；否决时是空数组。 */
  steps: PathStep[];
};

/**
 * 采纳 / 否决一条候选。
 *
 * 否决**必须写理由**（缺理由后端回 400）：理由进台账，并成为下次「找」的禁区——
 * 这是「你否决过的候选不再出现」的入口。
 * 采纳（2026-09-17 起）会在最新 active 计划里自动建一个同名阶段；落不了阶段
 * （没有 active 计划、有同名未收尾阶段）回 409，候选保持 proposed 可重试。
 * 采纳一条路径候选（T34）时回执带 `steps` —— 那是这条路的分步草案，进规划对话当底稿。
 */
export async function verdictCandidate(
  candidateId: number,
  accept: boolean,
  reason?: string,
  planId?: number | null,
): Promise<VerdictResult> {
  return request<VerdictResult>(`/api/candidates/${candidateId}/verdict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // plan_id 只在候选没有归属（「新方向」）时需要——决定采纳落到哪个计划
    body: JSON.stringify({ accept, reason: reason ?? null, plan_id: planId ?? null }),
  });
}

// ---------- 任务与交付物（T23 三级结构） ----------

/** 打勾 / 跳过的回执。`proposal_id` 恒为 `null`：T29 起阶段完成不再顺产推进提案（键留着不动形状）。 */
export type TaskActionResult = {
  node_id: number;
  node_status_before: string;
  node_status: string;
  proposal_id: number | null;
};

/** 任务打勾：一步到完成（不写理由）。只对任务层有效（对阶段/周打卡回 400）。 */
export async function checkTask(nodeId: number): Promise<TaskActionResult> {
  return request<TaskActionResult>(`/api/plan/nodes/${nodeId}/check`, { method: "POST" });
}

/**
 * 跳过：跳过算完成的一种，但**必须写一句理由**（缺理由回 400）。
 *
 * **阶段与任务都能跳**（T35：SPEC 决策 41）——「不要了」在三层各有出口：方向层否决＝
 * 永久拉黑（太重）、蓝图不勾＝本版不建、这里＝**已经建出来的这一步不做了**（留一句理由，
 * 答「当时为什么没做」）。周打卡不给跳（它是节奏节点，报告里本来就有「跳过」这个状态）。
 * 阶段跳过时其下的任务原样留着（它们是痕迹），落后量不再算它，阶段完成判定直接满足。
 */
export async function skipNode(nodeId: number, reason: string): Promise<TaskActionResult> {
  return request<TaskActionResult>(`/api/plan/nodes/${nodeId}/skip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

/** 提交交付物的回执。`submission_id` 每次提交都不同（重提交 = 新行，旧值留痕）。 */
export type DeliverableResult = {
  submission_id: number;
  node_id: number;
  url: string;
  note: string;
  created_at: string;
  proposal_id: number | null;
};

/** 提交阶段的交付物：独立动作，可重新提交。只对阶段有效。 */
export async function submitDeliverable(
  nodeId: number,
  url: string,
  note: string,
): Promise<DeliverableResult> {
  return request<DeliverableResult>(`/api/plan/nodes/${nodeId}/deliverable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, note }),
  });
}

/**
 * 改一个已经建好的节点的字段（T30，SPEC 决策 38）。
 *
 * 只传要改的字段：没传 = 不动，`due_date: ""` = 清掉日期（清了就不进落后量），
 * `deliverable: ""` = 清掉交付物。**理由必填**——它进台账，回答「为什么改」。
 * 原地改、**id 不变**，报告与交付物引用都不受影响。
 */
export type NodeFieldsInput = {
  title?: string;
  deliverable?: string;
  due_date?: string;
  reason: string;
};

/** 改字段的回执：`changed` 是这次真动了的字段名，`before` / `after` 只有这些字段。 */
export type NodeFieldsResult = {
  node_id: number;
  changed: string[];
  before: Record<string, string | null>;
  after: Record<string, string | null>;
};

export async function updateNodeFields(
  nodeId: number,
  input: NodeFieldsInput,
): Promise<NodeFieldsResult> {
  return request<NodeFieldsResult>(`/api/plan/nodes/${nodeId}/fields`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: input.title ?? null,
      deliverable: input.deliverable ?? null,
      due_date: input.due_date ?? null,
      reason: input.reason,
    }),
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

/**
 * 一条提案现有的三类，payload 形状随 `kind` 变（页面里按 kind 转成下面几个类型之一）：
 * `plan_blueprint`（蓝图待批）、`material_judgment`（资料判断）、`profile_change`（档案变更）。
 *
 * `stage_advance` 与 `plan_replan` 的 payload 类型已在 T29 随那两类一起删掉：
 * 它们不再有生产者，批准也不再改任何东西（SPEC 决策 28）。
 */

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
  /**
   * `blueprint_built` = 按勾选把树建进了计划（`built` 里列出建了哪些）；
   * `profile_written` = 真把一条写进了长期档案（`written` 里是哪一条）；
   * `node_updated` = **原地改**了一个已有节点的字段（`updated` 里是改前改后，**id 不变**）；
   * `node_added` = 往计划里加了节点（`added.nodes` 里按建的顺序列出每一条——加阶段时
   * 第一条是阶段、后面跟着它下面的任务；一批任务也走它）；
   * `memory_added` / `memory_superseded` / `memory_renewed` / `memory_voided` = 批准一条
   * **记忆候选**（2026-09-21 记忆系统）：新增 / 取代 / 复核续期 / 复核作废，详见 `remembered`；
   * `recorded_only` = 纯记账（驳回也是它）。
   */
  effect:
    | "blueprint_built"
    | "profile_written"
    | "node_updated"
    | "node_added"
    | "memory_added"
    | "memory_superseded"
    | "memory_renewed"
    | "memory_voided"
    | "recorded_only";
  /** 只在批准记忆候选时有值：这次真写进去的那条记忆（取代时另带 `before`）。 */
  remembered: { effect: string; memory?: MemoryItem; before?: string } | null;
  /** 只在批准档案变更时有值：真写进档案的那一条。 */
  written: { id: number; category: string; content: string } | null;
  /** 只在批准蓝图时有值：这次真建了哪些节点，外加「没能写进去」那类实话。 */
  built: {
    plan_id: number;
    stages: { id: number; title: string; deliverable: string | null }[];
    tasks: { id: number; title: string; stage_id: number }[];
    notes: string[];
  } | null;
  /** 只在批准「加节点」时有值：新建的那些节点，按建的顺序（阶段在前、它的任务跟着）。 */
  added: {
    nodes: { id: number; level: string; title: string; parent_id: number | null }[];
  } | null;
  /** 只在批准「改一个已有节点」时有值：改了哪几项、改前改后（id 在一个没动）。 */
  updated: {
    node_id: number;
    changed: string[];
    before: Record<string, string | null>;
    after: Record<string, string | null>;
  } | null;
};

/**
 * 批准 / 驳回一条提案。
 *
 * 驳回**必须写理由**（缺理由后端回 400）；已裁定过回 409、不存在回 404。
 * （T29 起没有 `option`：需要选方向的那一类已随 `plan_replan` 整类删除。）
 * 批准 `plan_blueprint` 时用 `selected` 给勾中的阶段 / 任务下标（见 `Selection`）。
 */
export async function decideProposal(
  proposalId: number,
  input: { approved: boolean; reason?: string; selected?: string[] },
): Promise<ProposalDecision> {
  return request<ProposalDecision>(`/api/proposals/${proposalId}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approved: input.approved,
      reason: input.reason ?? null,
      selected: input.selected ?? null,
    }),
  });
}

// ---------- 对话式规划与蓝图（T26：SPEC 决策 36） ----------

/** 蓝图里的一个任务。`due_date` 为空 = 没定日期（带了才进落后量）。 */
export type BlueprintTask = {
  title: string;
  due_date: string | null;
};

/** 蓝图里的一个阶段：名字 + 可验收的交付物 + 为什么先做它 + 任务。 */
export type BlueprintStage = {
  title: string;
  deliverable: string;
  why: string;
  tasks: BlueprintTask[];
};

/**
 * 一棵蓝图的 payload（`proposal.kind === "plan_blueprint"` 时）。
 *
 * **树 = 版本**：同一计划同时只有一份待裁定蓝图，新版落库时旧的变成 `superseded`。
 */
export type BlueprintPayload = {
  plan_id: number;
  candidate_id: number;
  goal: string;
  stages: BlueprintStage[];
};

/** 对话里的一条消息。助手那侧 `content` 是它输出的 JSON 原文。 */
export type ChatMessage = {
  role: string;
  content: string;
  created_at: string;
};

export type PlanChatView = {
  candidate_id: number;
  /** 这段对话属于哪个计划；null = 还没定下来（「新方向」的候选，得先指明落点）。 */
  plan_id: number | null;
  messages: ChatMessage[];
  turns_used: number;
  max_turns: number;
  /** 聊过至少一轮才能出方案（决策 36：不是一次性静默生成）。 */
  can_generate: boolean;
  /**
   * 这条候选自带的**步骤草案**（T34，只有路径候选有）：对话区顶部把它列出来，
   * 让你看得见它在照哪份底稿聊。刷新页面也还在（后端从候选 payload 解，不靠当刻响应）。
   */
  steps: PathStep[];
  /** 这个计划当前待裁定的蓝图（同一计划同时只有一份）。 */
  blueprint: { id: number; created_at: string } | null;
};

/** 看这段对话（历史 + 聊了几轮 + 能不能出方案）。 */
export async function getPlanChat(
  candidateId: number,
  planId?: number | null,
): Promise<PlanChatView> {
  const plan = planId === undefined || planId === null ? "" : `&plan_id=${planId}`;
  return request<PlanChatView>(`/api/plan-chat?candidate_id=${candidateId}${plan}`);
}

/** 聊一轮的回执。`reply.question` 是模型这一轮问你的（最多 3 个）。 */
export type PlanChatTurn = {
  candidate_id: number;
  plan_id: number;
  reply: { questions: string[]; ready: boolean; note: string };
  turns_used: number;
  max_turns: number;
  calls: number;
};

/** 聊一轮：每轮 1 次调用、整段上限 6 轮，输出不合格不重试（SPEC 决策 6 修订）。 */
export async function sayPlanChat(
  candidateId: number,
  message: string,
  planId?: number | null,
): Promise<PlanChatTurn> {
  return request<PlanChatTurn>("/api/plan-chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_id: candidateId,
      message,
      plan_id: planId ?? null,
    }),
  });
}

/** 出方案的回执。`superseded_ids` = 被这一版顶掉的旧蓝图。 */
export type BlueprintCreated = {
  proposal_id: number;
  plan_id: number;
  candidate_id: number;
  version: number;
  goal: string;
  stages: BlueprintStage[];
  superseded_ids: number[];
  calls: number;
};

/** 沿对话出一版蓝图，落成一条待裁定提案。必须先聊过一轮。 */
export async function generateBlueprint(
  candidateId: number,
  planId?: number | null,
): Promise<BlueprintCreated> {
  return request<BlueprintCreated>("/api/plan-chat/blueprint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_id: candidateId, plan_id: planId ?? null }),
  });
}

/**
 * 勾选的样子：`"2"` = 第 3 个阶段整段；`"2.1"` = 第 3 个阶段里的第 2 个任务（下标从 0 起）。
 * 一个都没勾（空数组）在后端会被拒——「整份都要」是不传这个参数。
 */
export type Selection = string[];

// ---------- 计划级对话（T28：蓝图落地之后接着聊；T31 起能提一条可执行建议） ----------
//
// 与上面那段 plan-chat（绑候选、6 轮、出蓝图）分工不同：这一段跟着**计划**走，
// 不限轮数（成本闸是历史字符上限），聊的是执行期的事。助手那一侧存的是人话，不是 JSON。
//
// **2026-09-20 起资料由它自己读**（决策 40）：后端跑受控工具循环，模型先说要读什么、
// 系统去取，读来的才算它这一轮的依据——所以每条消息下面带 `run`（读了哪几样、为什么停下）。

/**
 * 它某一轮提的一条建议，以及那条待裁定提案（T31：SPEC 决策 39）。
 *
 * 提案是随那一轮**自动**落的，界面上的「确认」＝当场批准（改的原地改、加的建节点），
 * 「忽略」＝当场驳回（台账记「聊天里先不动」）——所以每条建议都有归宿，
 * **不会在 `/proposals` 页堆着**。已裁定的消息也带这个字段（界面就不给按钮了）。
 */
export type DialogueSuggestion = {
  /** 那条 `plan_change` 提案的 id——「确认」就是对它调 `decideProposal`。 */
  proposal_id: number;
  /** 后端拼好的人话一行，直接当确认条显示：「改『读 MDN』的截止日：10-01 → 10-08」。 */
  summary: string;
  /** `pending` 时界面给「确认 / 忽略」两个按钮，其余只显示结果。 */
  status: string;
};

/** 一次只读工具调用：读了哪一样、成没成、读到了什么（决策 40）。 */
export type AgentToolUse = {
  /** 工具名：`read_current_plan` / `read_recent_reports` / `read_profile` / `read_plan_origin`。 */
  name: string;
  /** 参数摘要（多半是空——这几个工具基本不接参数），排错时看。 */
  args: string;
  /** 读成了没有：名字不认识、参数不合法时为 `false`。 */
  ok: boolean;
  /** 后端拼好的一句人话摘要，界面直接显示——不让前端拿原始字段拼中文。 */
  summary: string;
  duration_ms: number;
};

/**
 * 一轮 Agent 运行的账（`agent_run` 表那一行）：调了几次模型、读了哪几样、为什么停下。
 *
 * 它是**运行审计**，不是记忆：下一轮不会读它，只是让你（和排错的人）看得见这一步做了什么。
 */
export type AgentRun = {
  /** `ok` 正常答完 / `limit` 撞了调用上限 / `failed` 一直没给出合格输出。 */
  status: string;
  /** 中文一句话：为什么停下（正常答完时也在，写着调了几次、读了几次）。 */
  stop_reason: string;
  model_calls: number;
  tool_calls: number;
  /** 这一轮读成的工具名，按读的顺序去重。 */
  tool_names: string[];
  /** 每一次工具调用的明细（含被拒的）。 */
  tools: AgentToolUse[];
};

/** 对话里的一条消息。 */
export type DialogueMessage = {
  role: string;
  content: string;
  created_at: string;
  /** 助手这一轮提的建议（没有就是 `null`）。 */
  suggestion: DialogueSuggestion | null;
  /** 这一轮的运行账（决策 40）；失败的那一轮挂在**你**那条消息上，其余在 `null`。 */
  run: AgentRun | null;
};

export type PlanDialogueView = {
  plan_id: number;
  messages: DialogueMessage[];
  /** 已经聊了几句（不限轮数，这个数只用来显示）。 */
  turns_used: number;
  /** 历史累计字符上限；超出从最早截断（后端做的，前端只显示）。 */
  char_limit: number;
  /** 至少聊过一句才有东西可提炼档案提案。 */
  can_extract: boolean;
};

/** 看这个计划的对话（历史 + 聊了几句）。 */
export async function getPlanDialogue(planId: number): Promise<PlanDialogueView> {
  return request<PlanDialogueView>(`/api/plan-dialogue?plan_id=${planId}`);
}

/** 聊一句的回执。`reply` 是它的人话回复；`suggestion` 是那一条建议（没有就是 `null`）。 */
export type DialogueTurn = {
  plan_id: number;
  reply: string;
  suggestion: DialogueSuggestion | null;
  /** 建议落成的那条提案 id（没有建议时为 `null`）。 */
  proposal_id: number | null;
  turns_used: number;
  calls: number;
  /** 这一轮的运行账（决策 40）。 */
  run: AgentRun;
  /** 这一轮读了哪几样（`run.tool_names` 的同义字段，取用方便）。 */
  tools_used: string[];
  /** 为什么停下（`run.stop_reason` 的同义字段）。 */
  stop_reason: string;
};

/**
 * 聊一句：人话 + 最多一条可执行建议。
 *
 * 后端跑的是**受控工具循环**（决策 40）：它自己决定读哪几样资料（最多 6 次），最多 3 次
 * 模型调用（工具轮与「输出不合格重说一次」共用这个额度）；撞上限或一直不合格就报错，
 * 并说清读了什么、还缺什么，你这句话仍留在对话里。
 */
export async function sayPlanDialogue(planId: number, message: string): Promise<DialogueTurn> {
  return request<DialogueTurn>("/api/plan-dialogue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_id: planId, message }),
  });
}

/** 提炼的回执：落了哪几条待裁定的档案变更提案（允许 0 条）。 */
export type DialogueExtraction = {
  plan_id: number;
  items: { proposal_id: number; category: string; content: string }[];
  calls: number;
};

/**
 * 把这段对话里聊出的变化提炼成待裁定的**档案变更提案**（你点按钮才发生）。
 * 批准那条提案才会真的改档案——去「待裁定提案」页裁定。
 */
export async function extractProfileProposals(planId: number): Promise<DialogueExtraction> {
  return request<DialogueExtraction>("/api/plan-dialogue/profile-proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_id: planId }),
  });
}

/**
 * 「档案变更」提案的 payload（T28 的计划对话产的）。
 *
 * 批准 = 真的写进长期档案（新增一条；同类别一字不差的重复会被拒）。
 */
export type ProfileChangePayload = {
  /** 五个约定令牌之一：life_habit / life_log / current_state / short_term_goal / long_axis。 */
  category: string;
  content: string;
  /** 为什么该这么记——进台账，回答「当时为什么这么写」。 */
  why?: string;
  /** 这条是从哪个计划的对话里聊出来的。 */
  plan_id?: number;
};

/**
 * 「计划改动」提案的 payload（T31：计划对话里那条可执行建议，SPEC 决策 39）。
 *
 * 三类动作共用一个 payload 形状，按 `action` 取用各自那几项：
 * - `update_node`：`node_id` + `fields`（只含真改了的字段）+ `before`；
 * - `add_task`：`stage_id` + `stage_title` + `task`；
 * - `add_stage`：`stage`（新阶段排最后）。
 *
 * 批准＝改的走原地改字段（id 不变）、加的走建节点；界面上的「确认 / 忽略」就是对
 * 这条提案的批准 / 驳回。`summary` 是后端拼好的人话一行，界面直接显示。
 */
export type PlanChangePayload = {
  plan_id: number;
  action: string;
  /** 给人看的一行（后端拼的），也是台账理由里那一句。 */
  summary?: string;
  /** 为什么该这么改（模型给的）。 */
  why?: string;
  node_id?: number;
  node_title?: string;
  level?: string;
  fields?: Record<string, string | null>;
  before?: Record<string, string | null>;
  stage_id?: number;
  stage_title?: string;
  /** 老形状：单件任务（T31 第一版落的提案可能还是它——界面按 `tasks` 优先读）。 */
  task?: { title: string; due_date?: string | null };
  /** 一次要加的一批任务（最多 5 件）：加阶段时是它下面要建的任务，加任务时是这一批。 */
  tasks?: { title: string; due_date?: string | null }[];
  stage?: { title: string; deliverable: string; why?: string };
};

/**
 * 一条计划改动里要加的任务：`tasks` 优先，老形状 `task` 兜底——两边都认。
 *
 * 为什么在前端也做这一层：库里可能还躺着按第一版形状（`task`）落的待裁定提案，
 * 它们还没被裁定，界面得照样画得出来。
 */
export function planChangeTasks(payload: PlanChangePayload): {
  title: string;
  due_date?: string | null;
}[] {
  if (payload.tasks !== undefined && payload.tasks.length > 0) return payload.tasks;
  return payload.task !== undefined ? [payload.task] : [];
}

// ---------- 判资料（T29：`/judge` 页） ----------

/** 判过的一份资料（只读历史里的一条）。`judgment` 是当时的四问答案。 */
export type JudgmentRecord = {
  id: number;
  source_text: string;
  judgment: Judgment | undefined;
  /** `accepted` / `rejected`——历史里只有已裁定过的（待裁定的那份还在 `/proposals` 页）。 */
  status: string;
  decided_at: string | null;
  created_at: string;
};

/**
 * 判资料的**只读历史**：裁定过的四问判断，最近的在前。
 *
 * 为什么会有这条接口：裁定环节搬去 `/proposals` 之后，过去判过的资料也得查得到——
 * 这一页只回看，不能改也不能重裁。
 */
export async function listJudgments(limit = 20): Promise<{ items: JudgmentRecord[] }> {
  const data = await request<{
    items: {
      id: number;
      status: string;
      decided_at: string | null;
      created_at: string;
      payload: { source_text?: string; judgment?: Judgment };
    }[];
  }>(`/api/judgments?limit=${limit}`);
  return {
    items: data.items.map((item) => ({
      id: item.id,
      source_text: item.payload.source_text ?? "（没记原文）",
      judgment: item.payload.judgment,
      status: item.status,
      decided_at: item.decided_at,
      created_at: item.created_at,
    })),
  };
}

// ---------- 记忆系统（2026-09-21，方案 docs/记忆系统.md） ----------
//
// 三层记忆里，前端只管**长期记忆**这一层（全局 + 计划内）与它的收件箱：
// 工作记忆在对话那一轮里、经历记忆由 Agent 自己检索，都不从这里走。
// 两条口径写在类型注释里，页面照着渲染就行：候选**只有满足批量规则**的才允许勾选、
// 彻底删除**先预览再确认**（预览是只读的）。

/** 记忆分两级：全局长期记忆（沿用档案五类）与计划内记忆（约束 / 决定 / 偏好）。 */
export type MemoryScope = "global" | "plan";

/** 这条是谁说的。`legacy_manual` 是记忆系统落地之前的历史手工条目，只读不写。 */
export type MemorySourceKind = "user_stated" | "agent_inferred" | "legacy_manual";

/** 计划内记忆的三种内容；全局那一级仍用档案的五个类别（PROFILE_CATEGORIES）。 */
export const MEMORY_KINDS: Record<string, string> = {
  constraint: "约束",
  decision: "决定",
  preference: "偏好",
};

/** 记忆候选的三种动作：新增 / 取代 / 复核处理。 */
export type MemoryAction = "add" | "supersede" | "review";

/** 一条来源证据：哪类经历的哪一条、原话摘录、发生时间。 */
export type MemoryEvidence = {
  source_type: string;
  /** 来源类别中文名，后端给（前端不自己查表）。 */
  source_label: string;
  source_id: number;
  excerpt: string;
  source_time: string | null;
};

export type MemoryItem = {
  id: number;
  scope: MemoryScope;
  scope_label: string;
  plan_id: number | null;
  category: string | null;
  category_label: string | null;
  kind: string | null;
  kind_label: string | null;
  content: string;
  source_kind: MemorySourceKind | null;
  source_kind_label: string;
  fact_time: string | null;
  review_at: string | null;
  /** 到了复核时间：还在「当前记忆」里看得见，但**默认不再当依据**。 */
  review_due: boolean;
  status: string;
  valid_from: string;
  created_at: string;
  evidence: MemoryEvidence[];
};

export type MemoryListing = {
  today: string;
  plan_id: number | null;
  global: MemoryItem[];
  plan: MemoryItem[];
  /** 两级里所有到了复核时间的，页面「待复核」标签页用它。 */
  due: MemoryItem[];
  counts: { global: number; plan: number; due: number };
};

/** 收件箱里的一条候选。`batch_eligible` 为真才允许勾选批量批准。 */
export type MemoryCandidate = {
  proposal_id: number;
  action: MemoryAction;
  action_label: string;
  scope: MemoryScope;
  scope_label: string;
  plan_id: number | null;
  category: string | null;
  category_label: string | null;
  kind: string | null;
  kind_label: string | null;
  content: string | null;
  target_id: number | null;
  target_content: string | null;
  decision: string | null;
  decision_label: string | null;
  review_at: string | null;
  fact_time: string | null;
  source_kind: MemorySourceKind | null;
  source_kind_label: string;
  reason: string;
  /** 疑似与某条重复时的提示（**只提示、不自动合并**）。 */
  duplicate_hint: string | null;
  evidence: MemoryEvidence[];
  batch_eligible: boolean;
  purged: boolean;
  created_at: string;
};

export type MemoryInbox = { candidates: MemoryCandidate[]; count: number };

/** 一行扫描记录：扫了几条、落了几条候选、失败为什么。 */
export type MemoryScanRecord = {
  scan_id: number;
  trigger: string;
  trigger_label: string;
  plan_id: number | null;
  status: string;
  scanned: number;
  candidates: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type MemoryScanReport = MemoryScanRecord & {
  landed: { proposal_id: number; content: string | null }[];
  /** 当批里被判不合格、因此没落库的候选（写明为什么）。 */
  dropped: { content: string; why: string }[];
  /** 还有没扫完的经历：再点一次「扫描」接着扫。 */
  has_more: boolean;
};

/** 彻底删除的影响预览：要动哪些地方、以及**清不掉的残留**（这一步只读）。 */
export type PurgePreview = {
  scope: MemoryScope;
  id: number;
  content: string;
  evidence: MemoryEvidence[];
  candidate_proposals: number[];
  copies: { table: string; column: string; id: number }[];
  irreversible: boolean;
  note: string;
};

export type PurgeResult = {
  scope: MemoryScope;
  id: number;
  affected: number;
  /** 清干净了才是 true；false 时 `leftover` 列出残留位置——**不宣称成功**。 */
  complete: boolean;
  leftover: { table: string; column: string; id: number }[];
  note: string;
};

export async function getMemory(planId?: number): Promise<MemoryListing> {
  const query = planId === undefined ? "" : `?plan_id=${planId}`;
  return request<MemoryListing>(`/api/memory${query}`);
}

export async function getMemoryInbox(): Promise<MemoryInbox> {
  return request<MemoryInbox>("/api/memory/inbox");
}

export async function listMemoryScans(limit = 20): Promise<{ scans: MemoryScanRecord[] }> {
  return request<{ scans: MemoryScanRecord[] }>(`/api/memory/scans?limit=${limit}`);
}

/**
 * 扫一批新经历，**只产候选**（不写任何长期记忆）。
 *
 * 不传 `planId` 就是全局那一批；`has_more` 为真说明还有没扫完的，再点一次接着扫。
 */
export async function scanMemories(input: {
  planId?: number;
  trigger?: "manual" | "weekly" | "plan_close";
}): Promise<{ scans: MemoryScanReport[] }> {
  return request<{ scans: MemoryScanReport[] }>("/api/memory/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plan_id: input.planId ?? null,
      trigger: input.trigger ?? "manual",
    }),
  });
}

/** 手工补一条长期记忆。**你自己敲的这条不需要来源证据**——你就是来源。 */
export async function createMemory(input: {
  scope: MemoryScope;
  content: string;
  planId?: number;
  category?: string;
  kind?: string;
  sourceKind?: MemorySourceKind;
  factTime?: string;
  reviewAt?: string;
  reason?: string;
}): Promise<MemoryItem> {
  return request<MemoryItem>("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope: input.scope,
      content: input.content,
      plan_id: input.planId ?? null,
      category: input.category ?? null,
      kind: input.kind ?? null,
      source_kind: input.sourceKind ?? "user_stated",
      fact_time: input.factTime || null,
      review_at: input.reviewAt || null,
      reason: input.reason || null,
    }),
  });
}

/** 改一条记忆：走台账「取代」，旧值留痕、理由必填。 */
export async function updateMemory(input: {
  memoryId: number;
  scope: MemoryScope;
  content: string;
  reason: string;
  factTime?: string;
  reviewAt?: string;
}): Promise<MemoryItem> {
  return request<MemoryItem>(`/api/memory/${input.memoryId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope: input.scope,
      content: input.content,
      reason: input.reason,
      fact_time: input.factTime ?? null,
      review_at: input.reviewAt ?? null,
    }),
  });
}

/** 作废一条记忆（普通纠错走这条，留痕）。 */
export async function voidMemory(input: {
  memoryId: number;
  scope: MemoryScope;
  reason: string;
}): Promise<{ id: number; voided: boolean }> {
  return request<{ id: number; voided: boolean }>(`/api/memory/${input.memoryId}/void`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: input.scope, reason: input.reason }),
  });
}

/** 处理「待复核」：还作数就往后推（renew），不再作数就作废（void）。 */
export async function reviewMemory(input: {
  memoryId: number;
  scope: MemoryScope;
  decision: "renew" | "void";
  reviewAt?: string;
  reason?: string;
}): Promise<{ decision: string; memory?: MemoryItem; voided?: boolean }> {
  return request<{ decision: string; memory?: MemoryItem; voided?: boolean }>(
    `/api/memory/${input.memoryId}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: input.scope,
        decision: input.decision,
        review_at: input.reviewAt || null,
        reason: input.reason || null,
      }),
    },
  );
}

/** 批量批准：只收「新增 + 用户明确陈述」；其余进 skipped 并写明原因。 */
export async function batchApproveMemories(
  proposalIds: number[],
  reason?: string,
): Promise<{
  approved: { proposal_id: number; memory?: MemoryItem }[];
  skipped: { proposal_id: number; why: string }[];
}> {
  return request("/api/memory/batch-approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposal_ids: proposalIds, reason: reason ?? null }),
  });
}

/** 彻底删除的**影响预览**：这一步不改任何数据。 */
/** 彻底删除的**影响预览**：这一步不改任何数据。 */
export async function previewMemoryPurge(
  memoryId: number,
  scope: MemoryScope,
): Promise<PurgePreview> {
  return request<PurgePreview>(`/api/memory/${memoryId}/purge-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
}

/** 执行彻底删除（不可恢复）。清不干净时 `complete` 为 false——那不是报错，是实话。 */
export async function purgeMemory(input: {
  memoryId: number;
  scope: MemoryScope;
  reason: string;
}): Promise<PurgeResult> {
  return request<PurgeResult>(`/api/memory/${input.memoryId}/purge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: input.scope, reason: input.reason }),
  });
}
