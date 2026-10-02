import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Check, X, ChevronDown } from "lucide-react";
import { PathStep } from "@/lib/api";
import { HoverSelect } from "@/components/ui/hover-select";
import { ActionSwapRollText } from "@/components/motion/action-swap-roll";
import { SPRING_PRESS } from "@/lib/ease";

const KIND_LABELS: Record<string, string> = {
  concept: "概念",
  doc: "资料",
  project: "项目",
  course: "课程",
};

// 四枚中文状态徽标（Animated Badge）：轻量入场（缩放+淡入），reduced-motion 时直接静止。
const STATUS_BADGES: Record<string, { label: string; className: string }> = {
  accepted: { label: "已采纳", className: "text-green bg-green/10 border-green/20" },
  rejected: { label: "已否决", className: "text-red-400 bg-red-500/10 border-red-500/20" },
  proposed: { label: "待裁定", className: "text-amber-500 bg-amber-500/10 border-amber-500/20" },
  expired: { label: "已过期", className: "text-white/50 bg-white/5 border-white/10" },
};

function StatusBadge({ status }: { status: string }) {
  const reduce = useReducedMotion();
  const badge = STATUS_BADGES[status];
  if (!badge) return null;
  const cls = `text-[11px] font-medium px-2.5 py-1 rounded-full border hidden sm:block ${badge.className}`;
  if (reduce) {
    return <span className={cls}>{badge.label}</span>;
  }
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.85, y: 2 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={SPRING_PRESS}
      className={cls}
    >
      {badge.label}
    </motion.span>
  );
}

// Bouncy Accordion（beui.dev 参考）弹簧组：行位移动画刻意不带 y 过冲（免得压到下一行）；
// 内容开合不对称——合比开快一拍；chevron 用独立小弹簧。
const ACCORDION_MOVE = { type: "spring", duration: 0.55, bounce: 0.38 } as const;
const ACCORDION_OPEN = { type: "spring", duration: 0.58, bounce: 0.32 } as const;
const ACCORDION_CLOSE = { type: "spring", duration: 0.46, bounce: 0.26 } as const;
const ACCORDION_CHEVRON = { type: "spring", duration: 0.42, bounce: 0.28 } as const;
/** 手风琴组端点的圆角；相邻收起行共用直角，连成一整块。 */
const GROUP_RADIUS = 14;

export function CandidateCard({
  row,
  plans,
  plansLoading,
  plansError,
  onRetryPlans,
  notes,
  isDecided,
  verdicting,
  rejectingId,
  setRejectingId,
  rejectReason,
  setRejectReason,
  adoptingId,
  setAdoptingId,
  onVerdict,
  setChattingId,
  clarifyNote = false,
  open,
  onToggle,
  isFirst,
  isLast,
  prevOpen,
  nextOpen,
  presentation = "accordion",
}: {
  row: {
    id: number;
    title: string;
    kind: string;
    why: string;
    depthTarget: string;
    isRecommended: boolean;
    status: string;
    rejectReason: string | null;
    planId: number | null;
    /** 非候选轮没有形态（null）——只影响 path 样式判断，不推断假形态。 */
    shape: string | null;
    steps: PathStep[];
  };
  plans: { id: number; goal: string }[];
  plansLoading: boolean;
  plansError: string | null;
  onRetryPlans: () => void;
  notes: Record<number, string>;
  isDecided: boolean;
  verdicting: boolean;
  rejectingId: number | null;
  setRejectingId: (id: number | null) => void;
  rejectReason: string;
  setRejectReason: (val: string) => void;
  adoptingId: number | null;
  setAdoptingId: (id: number | null) => void;
  onVerdict: (id: number, accept: boolean, reason?: string, planId?: number) => void;
  setChattingId: (id: number | null) => void;
  /** 这轮「找」还挂着未答的追问：卡片上提示可先采纳（决策 44 ④）。 */
  clarifyNote?: boolean;
  open: boolean;
  onToggle: () => void;
  isFirst: boolean;
  isLast: boolean;
  prevOpen: boolean;
  nextOpen: boolean;
  /** 桌面评审席：详情直接占据主区，不再作为列表行向下展开。 */
  presentation?: "accordion" | "dossier";
}) {
  const reduce = useReducedMotion() ?? false;
  const isRowPath = row.shape === "path";
  const isDossier = presentation === "dossier";
  // 采纳落点两档（OC-05）：「新方向」不落在任何现有计划上（正式计划等蓝图批准时才建），
  // 或者明确延续一个现有计划。不再提供「先新建一个空计划」——那会绕过蓝图批准建树的门槛。
  const [adoptMode, setAdoptMode] = useState<"fresh" | "existing">("fresh");
  const [adoptPlanChoice, setAdoptPlanChoice] = useState("");
  // 本卡是否正在出裁定（页面级 verdicting 对所有卡都为真，这里标记是哪张卡在处理，
  // 让「采纳 → 处理中」的 Action Swap 只出现在被点的那张卡上）。
  const [pendingAction, setPendingAction] = useState<"adopt" | "reject" | null>(null);

  // 渲染期重置（React 官方模式）：裁定结束就把这张卡的 pending 标记清掉。
  if (!verdicting && pendingAction !== null) {
    setPendingAction(null);
  }

  const handleAdoptClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setPendingAction("adopt");
    if (row.planId) {
      // 候选自带归属：落点就是它所属的计划，不用现选
      onVerdict(row.id, true);
      return;
    }
    setAdoptMode("fresh");
    setAdoptPlanChoice("");
    setAdoptingId(row.id);
  };

  const handleRejectClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setRejectingId(row.id);
    setRejectReason("");
    setAdoptingId(null);
  };

  // Action Swap 三态：待裁定 → 处理中 → 已采纳/已否决（成功后由回执区接棒展示）。
  const adoptSwapValue =
    verdicting && pendingAction === "adopt" ? "busy" : row.status === "accepted" ? "done" : "idle";
  const adoptLabel =
    verdicting && pendingAction === "adopt"
      ? "处理中…"
      : row.status === "accepted"
        ? "已采纳"
        : row.planId
          ? "采纳"
          : isRowPath
            ? "采纳整条路"
            : "采纳";
  const rejectSwapValue =
    verdicting && pendingAction === "reject" ? "busy" : row.status === "rejected" ? "done" : "idle";
  const rejectLabel =
    verdicting && pendingAction === "reject" ? "处理中…" : row.status === "rejected" ? "已否决" : "确认否决";

  // 连块圆角：收起行与相邻行共用直角；某行展开时上下让出 12px 空隙并恢复整圆角。
  const radius = open
    ? {
        borderTopLeftRadius: GROUP_RADIUS,
        borderTopRightRadius: GROUP_RADIUS,
        borderBottomLeftRadius: GROUP_RADIUS,
        borderBottomRightRadius: GROUP_RADIUS,
      }
    : {
        borderTopLeftRadius: isFirst || prevOpen ? GROUP_RADIUS : 0,
        borderTopRightRadius: isFirst || prevOpen ? GROUP_RADIUS : 0,
        borderBottomLeftRadius: isLast || nextOpen ? GROUP_RADIUS : 0,
        borderBottomRightRadius: isLast || nextOpen ? GROUP_RADIUS : 0,
      };
  const showDivider = !isFirst && !prevOpen && !open;

  return (
    <motion.div
      initial={false}
      animate={isDossier ? undefined : {
        ...radius,
        marginTop: open && !isFirst ? 12 : 0,
        marginBottom: open && !isLast ? 12 : 0,
      }}
      transition={reduce ? { duration: 0 } : ACCORDION_MOVE}
      className={isDossier ? "min-w-0" : `relative overflow-hidden ${
        row.isRecommended
          ? "bg-surface2/60 before:absolute before:left-0 before:top-3 before:bottom-3 before:w-[3px] before:bg-white before:rounded-r-full"
          : "bg-surface2/30 hover:bg-surface2/40"
      } transition-colors`}
    >
      {!isDossier && showDivider && (
        <div aria-hidden="true" className="absolute inset-x-5 top-0 h-px bg-white/[0.05]" />
      )}

      {/* Row Trigger —— 真按钮：可聚焦、Enter/空格开合 */}
      {!isDossier && (
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`candidate-details-${row.id}`}
        onClick={onToggle}
        className={`flex w-full min-h-[52px] items-center justify-between gap-3 px-5 py-3 cursor-pointer select-none text-left outline-none focus-visible:outline focus-visible:outline-1 focus-visible:-outline-offset-1 focus-visible:outline-white/25 rounded-[20px] ${row.isRecommended ? 'pl-5' : ''}`}
      >
        <div className="flex items-center gap-3 pr-2 min-w-0">
          {/* Status Dot */}
          {row.status === "accepted" ? (
            <div className="w-5 h-5 rounded-full bg-green/15 flex items-center justify-center shrink-0">
              <Check className="w-3 h-3 text-green" />
            </div>
          ) : row.status === "rejected" ? (
            <div className="w-5 h-5 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
              <X className="w-3 h-3 text-red-500" />
            </div>
          ) : row.status === "expired" ? (
            <div className="w-5 h-5 rounded-full bg-white/10 flex items-center justify-center shrink-0">
              <div className="w-1.5 h-1.5 rounded-full bg-white/30" />
            </div>
          ) : (
            <div className="w-5 h-5 rounded-full border border-amber-500/30 flex items-center justify-center shrink-0">
              <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            </div>
          )}
          <span className="text-[12px] font-medium text-white/45 tracking-wider hidden sm:block shrink-0">
            {KIND_LABELS[row.kind] ?? row.kind}
          </span>
          <div className="text-[16px] font-semibold text-primary/90 truncate">
            {row.title}
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="text-[12px] text-white/50 hidden md:block">
            深度 <span className="text-white/70 ml-1">{row.depthTarget}</span>
          </div>

          {/* Status Badge */}
          <StatusBadge status={row.status} />

          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={reduce ? { duration: 0 } : ACCORDION_CHEVRON}
            className="shrink-0 text-white/50 flex"
          >
            <ChevronDown className="w-4 h-4" />
          </motion.span>
        </div>
      </button>
      )}

      {/* Accordion Details —— 高密度展开区 */}
      <motion.div
        id={`candidate-details-${row.id}`}
        role="region"
        aria-label={isDossier ? `候选评审：${row.title}` : undefined}
        aria-hidden={!isDossier && !open}
        inert={!isDossier && !open}
        initial={false}
        animate={{ height: isDossier || open ? "auto" : 0, opacity: isDossier || open ? 1 : 0 }}
        transition={reduce ? { duration: 0 } : open ? ACCORDION_OPEN : ACCORDION_CLOSE}
        className={isDossier ? "min-w-0" : "overflow-hidden"}
      >
        <div className={isDossier ? "px-8 pb-10 pt-5 xl:px-12" : "px-5 pb-4 pt-1"}>
          <h2 className={isDossier ? "max-w-[24ch] break-words text-[30px] font-medium leading-[1.2] tracking-tight text-primary" : "mb-2 break-words text-[18px] font-semibold leading-snug tracking-tight text-primary/95"}>
            {row.title}
          </h2>
          {isDossier && (
            <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/[0.09] pb-6 text-[12px] text-white/55">
              <StatusBadge status={row.status} />
              <span>{isRowPath ? `完整路径 · ${row.steps.length} 个步骤` : KIND_LABELS[row.kind] ?? row.kind}</span>
              <span>目标深度 · {row.depthTarget}</span>
              {row.isRecommended && <span className="text-primary/80">优先推荐</span>}
            </div>
          )}
          <p className={isDossier ? "max-w-[62ch] py-8 text-[16px] leading-[1.75] text-primary/75" : "text-[13px] leading-[1.7] text-white/65"}>
            {row.why}
          </p>

          {isRowPath && row.steps.length > 0 && (
            <div className={isDossier ? "border-t border-white/[0.09] pt-7" : "mt-4"}>
              <div className={isDossier ? "mb-5 text-[13px] font-medium text-primary/75" : "mb-2.5 text-[11px] font-semibold uppercase tracking-widest text-white/[0.35]"}>路径草案</div>
              <ol className={isDossier ? "flex gap-0 overflow-x-auto pb-5" : "space-y-2"}>
                {row.steps.map((step, idx) => (
                  <li key={idx} className={isDossier ? "relative w-[210px] min-w-[210px] border-t border-white/30 pb-2 pr-6 pt-6 last:flex-1 last:pr-0" : "flex gap-2.5 text-[13px] leading-relaxed"}>
                    <span className={isDossier ? "absolute -top-1 left-0 size-2 rounded-full bg-white" : "w-4 shrink-0 font-medium tabular-nums text-white/40"}>{isDossier ? "" : `${idx + 1}.`}</span>
                    <div className="min-w-0">
                      {isDossier && <span className="mb-4 block font-mono text-[12px] tabular-nums text-white/40">{String(idx + 1).padStart(2, "0")}</span>}
                      <span className={isDossier ? "block text-[15px] leading-snug text-primary/90" : "text-primary/85"}>{step.title}</span>
                      {step.deliverable && <span className={isDossier ? "mt-5 block border-l border-white/20 pl-3 text-[12px] leading-relaxed text-white/55" : "ml-2 rounded border border-white/[0.05] bg-white/[0.04] px-1.5 py-0.5 align-middle text-[11px] text-white/50"}>交付 · {step.deliverable}</span>}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* 带未答追问的候选可先采纳（决策 44 ④）；回答后推荐可能更新，已采纳的不受影响。 */}
          {clarifyNote && row.status === "proposed" && (
            <p className="mt-3 border-l-2 border-white/[0.08] pl-3 text-[12px] leading-relaxed text-white/50">
              这条候选还带着未答的追问——可以先采纳；回答后推荐可能更新，已采纳的不受影响。
            </p>
          )}

          {/* Actions / Verdicts */}
          <div className={isDossier ? "mt-8 border-t border-white/[0.09] pt-6" : "mt-4"}>
            {isDecided ? (
              <div className="flex items-center gap-3 flex-wrap">
                <div className={isDossier ? "border-l border-white/30 py-2 pl-4 text-[12px] text-white/70" : "rounded-xl border border-white/[0.04] bg-white/[0.02] px-3 py-2 text-[12px] text-white/70"}>
                  {notes[row.id] ?? "该候选已有最终结论"}
                </div>
                {row.status === "accepted" && (
                  <button
                    onClick={() => setChattingId(row.id)}
                    data-rec="enter-planning"
                    className={`border border-white/[0.12] px-4 py-2 text-[13px] text-white/80 transition-colors hover:border-white/[0.3] hover:text-white ${isDossier ? "rounded-sm" : "rounded-full"}`}
                  >
                    进入规划对话
                  </button>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {!adoptingId && !rejectingId && (
                  <div className="flex items-center gap-3">
                    <button
                      onClick={handleAdoptClick}
                      disabled={verdicting}
                      className={`bg-white px-5 py-2 text-[13px] font-medium text-black transition-colors hover:bg-gray-200 disabled:opacity-50 ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    >
                      <ActionSwapRollText value={adoptSwapValue}>{adoptLabel}</ActionSwapRollText>
                    </button>
                    <button
                      onClick={handleRejectClick}
                      disabled={verdicting}
                      className={`border border-white/[0.08] px-5 py-2 text-[13px] font-medium text-white/60 transition-colors hover:bg-white/[0.02] hover:text-white disabled:opacity-50 ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    >
                      否决
                    </button>
                  </div>
                )}

                {/* Inline Approval Bar —— 采纳 = 进入规划（OC-05）：先定规划落点，
                    「新方向」不落任何现有计划，正式计划等蓝图批准时才创建。 */}
                {adoptingId === row.id && (
                  <div className={isDossier ? "flex min-w-0 flex-wrap items-center gap-4 border-t border-white/[0.09] py-4" : "flex min-w-0 flex-wrap items-center gap-4 rounded-xl border border-white/[0.06] bg-surface2/80 p-3"} onClick={e => e.stopPropagation()}>
                    <div className="text-[13px] text-white/60 font-medium whitespace-nowrap pl-2">规划落点</div>

                    <div
                      role="radiogroup"
                      aria-label="规划落点"
                      className={`flex shrink-0 gap-1 bg-white/[0.04] p-1 ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    >
                      <button
                        type="button"
                        role="radio"
                        aria-checked={adoptMode === "fresh"}
                        onClick={() => setAdoptMode("fresh")}
                        className={`px-4 py-1.5 text-[12px] font-medium transition-colors ${isDossier ? "rounded-sm" : "rounded-full"} ${adoptMode === "fresh" ? "bg-white/[0.08] text-white" : "text-white/50 hover:text-white/80"}`}
                      >
                        新方向
                      </button>
                      <button
                        type="button"
                        role="radio"
                        aria-checked={adoptMode === "existing"}
                        onClick={() => setAdoptMode("existing")}
                        className={`px-4 py-1.5 text-[12px] font-medium transition-colors ${isDossier ? "rounded-sm" : "rounded-full"} ${adoptMode === "existing" ? "bg-white/[0.08] text-white" : "text-white/50 hover:text-white/80"}`}
                      >
                        现有计划
                      </button>
                    </div>

                    {adoptMode === "existing" ? (
                      plansLoading ? (
                        <span className="min-w-[180px] text-[12px] text-white/55" role="status">
                          正在读取现有计划…
                        </span>
                      ) : plansError ? (
                        <span className="flex min-w-0 items-center gap-2 text-[12px] text-red-300" role="alert">
                          <span>计划读取失败：{plansError}</span>
                          <button
                            type="button"
                            onClick={onRetryPlans}
                            className="shrink-0 text-white/80 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                          >
                            重试
                          </button>
                        </span>
                      ) : plans.length === 0 ? (
                        <span className="min-w-0 text-[12px] text-white/55" role="status">
                          暂无进行中的现有计划，请改选「新方向」
                        </span>
                      ) : (
                        <HoverSelect
                          value={adoptPlanChoice}
                          onChange={setAdoptPlanChoice}
                          placeholder="选择现有计划..."
                          className="min-w-0 w-full flex-1 sm:min-w-[160px]"
                          searchable
                          options={[
                            { value: "", label: "选择现有计划..." },
                            ...plans.map(p => ({ value: String(p.id), label: p.goal })),
                          ]}
                        />
                      )
                    ) : (
                      <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-white/50">
                        不落进任何现有计划——先把成果和验收标准聊清楚，蓝图批准时才创建正式计划与阶段。
                      </p>
                    )}

                    <button
                      type="button"
                      onClick={() => {
                        setPendingAction("adopt");
                        if (adoptMode === "existing") onVerdict(row.id, true, undefined, Number(adoptPlanChoice));
                        else onVerdict(row.id, true);
                      }}
                      disabled={
                        verdicting ||
                        (adoptMode === "existing" &&
                          (plansLoading || plansError !== null || plans.length === 0 || !adoptPlanChoice))
                      }
                      className={`shrink-0 bg-white px-5 py-2 text-[13px] font-medium text-black transition-colors hover:bg-gray-200 disabled:opacity-50 ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    >
                      <ActionSwapRollText value={verdicting && pendingAction === "adopt" ? "busy" : "idle"}>
                        {verdicting && pendingAction === "adopt" ? "处理中…" : adoptMode === "fresh" ? "采纳为新方向" : "采纳进所选计划"}
                      </ActionSwapRollText>
                    </button>
                    <button
                      type="button"
                      onClick={() => setAdoptingId(null)}
                      aria-label="取消采纳"
                      className="p-2 text-white/50 hover:text-white/80 transition-colors ml-auto mr-1"
                    >
                      <X className="w-4 h-4"/>
                    </button>
                  </div>
                )}

                {rejectingId === row.id && (
                  <div className={isDossier ? "flex min-w-0 flex-wrap items-center gap-3 border-t border-white/[0.09] py-4" : "flex min-w-0 flex-wrap items-center gap-3 rounded-xl border border-white/[0.06] bg-surface2/80 p-3"} onClick={e => e.stopPropagation()}>
                    <div className="text-[13px] text-white/60 font-medium pl-2">否决理由:</div>
                    <input
                      value={rejectReason}
                      onChange={e => setRejectReason(e.target.value)}
                      placeholder="必填，这段探索里不再推它"
                      aria-label="否决理由（必填，这段探索里不再推它）"
                      className={`w-[240px] border border-white/[0.08] bg-black/40 px-4 py-2 text-[13px] text-primary/90 outline-none focus:border-white/[0.15] ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    />
                    <button
                      onClick={() => {
                        setPendingAction("reject");
                        onVerdict(row.id, false, rejectReason);
                      }}
                      disabled={verdicting || !rejectReason.trim()}
                      className={`bg-red-500/20 px-5 py-2 text-[13px] font-medium text-red-400 transition-colors hover:bg-red-500/30 disabled:opacity-50 ${isDossier ? "rounded-sm" : "rounded-full"}`}
                    >
                      <ActionSwapRollText value={rejectSwapValue}>{rejectLabel}</ActionSwapRollText>
                    </button>
                    <button
                      onClick={() => setRejectingId(null)}
                      aria-label="取消否决"
                      className="p-2 text-white/50 hover:text-white/80 transition-colors"
                    >
                      <X className="w-4 h-4"/>
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
