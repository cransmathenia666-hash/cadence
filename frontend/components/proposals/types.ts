export const CATEGORY_LABELS: Record<string, string> = {
  life_habit: "生活习惯（睡眠/运动/作息）",
  life_log: "生活记录（日程/课程/近况）",
  current_state: "当前状态（精力/时间/压力）",
  short_term_goal: "短期目标 + 当下痛点",
  long_axis: "长期主线（职业方向）",
};

export const KIND_CONFIG: Record<
  string,
  { label: string; badge: string; badgeClass: string }
> = {
  plan_blueprint: {
    label: "蓝图待批",
    badge: "BLUEPRINT",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  },
  profile_change: {
    label: "档案变更",
    badge: "PROFILE",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  },
  material_judgment: {
    label: "资料判断",
    badge: "JUDGMENT",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  },
  plan_change: {
    label: "计划改动",
    badge: "PLAN",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  },
  contract_change: {
    label: "契约修正",
    badge: "CONTRACT",
    badgeClass: "text-amber-200/80 bg-amber-500/[0.08] border-amber-500/25",
  },
  memory_change: {
    label: "记忆候选",
    badge: "MEMORY",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  },
};

export type MemoryChangePayload = {
  action: "add" | "supersede" | "renew" | "void" | "review" | string;
  scope: "global" | "plan" | string;
  plan_id?: number | null;
  content?: string | null;
  category?: string | null;
  category_label?: string | null;
  kind?: string | null;
  kind_label?: string | null;
  source_kind?: string | null;
  source_kind_label?: string | null;
  fact_time?: string | null;
  review_at?: string | null;
  target_id?: number | null;
  target_content?: string | null;
  decision?: string | null;
  duplicate_hint?: string | null;
  reason?: string | null;
};
