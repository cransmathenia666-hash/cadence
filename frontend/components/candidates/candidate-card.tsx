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
const GROUP_RADIUS = 20;

export function CandidateCard({
  row,
  plans,
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
  onAdoptIntoNewPlan,
  setChattingId,
  open,
  onToggle,
  isFirst,
  isLast,
  prevOpen,
  nextOpen,
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
    shape: string;
    steps: PathStep[];
  };
  plans: { id: number; goal: string }[];
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
  onAdoptIntoNewPlan: (id: number, title: string, goal: string) => void;
  setChattingId: (id: number | null) => void;
  open: boolean;
  onToggle: () => void;
  isFirst: boolean;
  isLast: boolean;
  prevOpen: boolean;
  nextOpen: boolean;
}) {
  const reduce = useReducedMotion() ?? false;
  const isRowPath = row.shape === "path";
  const [adoptMode, setAdoptMode] = useState<"existing" | "new">("existing");
  const [adoptPlanChoice, setAdoptPlanChoice] = useState("");
  const [newPlanGoal, setNewPlanGoal] = useState("");
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
      onVerdict(row.id, true);
      return;
    }
    setAdoptMode("existing");
    setAdoptPlanChoice("");
    setNewPlanGoal(row.title);
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
      animate={{
        ...radius,
        marginTop: open && !isFirst ? 12 : 0,
        marginBottom: open && !isLast ? 12 : 0,
      }}
      transition={reduce ? { duration: 0 } : ACCORDION_MOVE}
      className={`relative overflow-hidden ${
        row.isRecommended
          ? "bg-surface2/60 before:absolute before:left-0 before:top-3 before:bottom-3 before:w-[3px] before:bg-white before:rounded-r-full"
          : "bg-surface2/30 hover:bg-surface2/40"
      } transition-colors`}
    >
      {showDivider && (
        <div aria-hidden="true" className="absolute inset-x-5 top-0 h-px bg-white/[0.05]" />
      )}

      {/* Row Trigger —— 真按钮：可聚焦、Enter/空格开合 */}
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
          <div className="text-[14px] font-medium text-primary/90 truncate">
            {row.title}
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="text-[12px] text-white/50 hidden md:block">
            深度 <span className="text-white/70 ml-1">{row.depthTarget}</span> <span className="mx-1.5 opacity-30">|</span> #{row.id}
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

      {/* Accordion Details —— 高密度展开区 */}
      <motion.div
        id={`candidate-details-${row.id}`}
        role="region"
        aria-hidden={!open}
        inert={!open}
        initial={false}
        animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
        transition={reduce ? { duration: 0 } : open ? ACCORDION_OPEN : ACCORDION_CLOSE}
        className="overflow-hidden"
      >
        <div className="px-5 pb-4 pt-1">
          <p className="text-[13px] leading-[1.7] text-white/65">
            {row.why}
          </p>

          {isRowPath && row.steps.length > 0 && (
            <div className="mt-4">
              <div className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35] mb-2.5">路径草案</div>
              <ol className="space-y-2">
                {row.steps.map((step, idx) => (
                  <li key={idx} className="flex gap-2.5 text-[13px] leading-relaxed">
                    <span className="text-white/40 font-medium shrink-0 w-4 tabular-nums">{idx + 1}.</span>
                    <div className="min-w-0">
                      <span className="text-primary/85">{step.title}</span>
                      {step.deliverable && <span className="ml-2 px-1.5 py-0.5 bg-white/[0.04] text-white/50 text-[11px] rounded border border-white/[0.05] align-middle">交: {step.deliverable}</span>}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Actions / Verdicts */}
          <div className="mt-4">
            {isDecided ? (
              <div className="flex items-center gap-3 flex-wrap">
                <div className="text-[12px] text-white/70 bg-white/[0.02] px-3 py-2 rounded-xl border border-white/[0.04]">
                  {notes[row.id] ?? "该候选已有最终结论"}
                </div>
                {row.status === "accepted" && (
                  <button
                    onClick={() => setChattingId(row.id)}
                    className="px-4 py-2 rounded-full border border-white/[0.12] text-[13px] text-white/80 hover:border-white/[0.3] hover:text-white transition-colors"
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
                      className="px-5 py-2 bg-white text-black hover:bg-gray-200 rounded-full text-[13px] font-medium transition-colors disabled:opacity-50"
                    >
                      <ActionSwapRollText value={adoptSwapValue}>{adoptLabel}</ActionSwapRollText>
                    </button>
                    <button
                      onClick={handleRejectClick}
                      disabled={verdicting}
                      className="px-5 py-2 border border-white/[0.08] text-white/60 hover:text-white rounded-full text-[13px] font-medium transition-colors hover:bg-white/[0.02] disabled:opacity-50"
                    >
                      否决
                    </button>
                  </div>
                )}

                {/* Inline Approval Bar */}
                {adoptingId === row.id && (
                  <div className="flex flex-wrap items-center gap-4 bg-[#141416] p-3 rounded-2xl border border-white/[0.06]" onClick={e => e.stopPropagation()}>
                    <div className="text-[13px] text-white/60 font-medium whitespace-nowrap pl-2">落入哪个计划？</div>

                    <div className="flex gap-1 bg-white/[0.04] p-1 rounded-full shrink-0">
                      <button
                        onClick={() => setAdoptMode("existing")}
                        className={`px-4 py-1.5 rounded-full text-[12px] font-medium transition-colors ${adoptMode === "existing" ? "bg-white/[0.08] text-white" : "text-white/50 hover:text-white/80"}`}
                      >
                        现有
                      </button>
                      <button
                        onClick={() => setAdoptMode("new")}
                        className={`px-4 py-1.5 rounded-full text-[12px] font-medium transition-colors ${adoptMode === "new" ? "bg-white/[0.08] text-white" : "text-white/50 hover:text-white/80"}`}
                      >
                        新建
                      </button>
                    </div>

                    {adoptMode === "existing" ? (
                      <HoverSelect
                        value={adoptPlanChoice}
                        onChange={setAdoptPlanChoice}
                        placeholder="选择现有计划..."
                        className="flex-1 min-w-[160px]"
                        searchable
                        options={[
                          { value: "", label: "选择现有计划..." },
                          ...plans.map(p => ({ value: String(p.id), label: `#${p.id} ${p.goal}` })),
                        ]}
                      />
                    ) : (
                      <input
                        value={newPlanGoal}
                        onChange={e => setNewPlanGoal(e.target.value)}
                        placeholder="新计划目标 (选填)..."
                        aria-label="新计划目标（选填）"
                        className="bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none flex-1 min-w-[160px] focus:border-white/[0.15]"
                      />
                    )}

                    <button
                      onClick={() => {
                        setPendingAction("adopt");
                        if (adoptMode === "existing") onVerdict(row.id, true, undefined, Number(adoptPlanChoice));
                        else onAdoptIntoNewPlan(row.id, row.title, newPlanGoal);
                      }}
                      disabled={verdicting || (adoptMode === "existing" && !adoptPlanChoice)}
                      className="px-5 py-2 bg-white text-black rounded-full text-[13px] font-medium shrink-0 disabled:opacity-50 hover:bg-gray-200 transition-colors"
                    >
                      <ActionSwapRollText value={verdicting && pendingAction === "adopt" ? "busy" : "idle"}>
                        {verdicting && pendingAction === "adopt" ? "处理中…" : "确认"}
                      </ActionSwapRollText>
                    </button>
                    <button
                      onClick={() => setAdoptingId(null)}
                      aria-label="取消采纳"
                      className="p-2 text-white/50 hover:text-white/80 transition-colors ml-auto mr-1"
                    >
                      <X className="w-4 h-4"/>
                    </button>
                  </div>
                )}

                {rejectingId === row.id && (
                  <div className="flex flex-wrap items-center gap-3 bg-[#141416] p-3 rounded-2xl border border-white/[0.06]" onClick={e => e.stopPropagation()}>
                    <div className="text-[13px] text-white/60 font-medium pl-2">否决理由:</div>
                    <input
                      value={rejectReason}
                      onChange={e => setRejectReason(e.target.value)}
                      placeholder="必填，将进永久禁区"
                      aria-label="否决理由（必填，将进永久禁区）"
                      className="bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none w-[240px] focus:border-white/[0.15]"
                    />
                    <button
                      onClick={() => {
                        setPendingAction("reject");
                        onVerdict(row.id, false, rejectReason);
                      }}
                      disabled={verdicting || !rejectReason.trim()}
                      className="px-5 py-2 bg-red-500/20 text-red-400 rounded-full text-[13px] font-medium disabled:opacity-50 hover:bg-red-500/30 transition-colors"
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
