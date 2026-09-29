import { useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { type Proposal } from "@/lib/api";
import { ActionSwapButton } from "@/components/motion/action-swap";
import { KIND_CONFIG } from "./types";
import { BlueprintSection } from "./blueprint-section";
import { JudgmentSection } from "./judgment-section";
import { ProfileChangeSection } from "./profile-change-section";
import { PlanChangeSection } from "./plan-change-section";
import { MemoryChangeSection } from "./memory-change-section";

function getProposalTitle(proposal: Proposal): string {
  const payload = proposal.payload as Record<string, unknown>;
    if (proposal.kind === "plan_blueprint") {
    const goal = typeof payload.goal === "string" ? payload.goal : "";
    return goal || "蓝图方案";
  }
  if (proposal.kind === "material_judgment") {
    const text = typeof payload.source_text === "string" ? payload.source_text.trim() : "";
    return text ? `研判「${text.slice(0, 36)}${text.length > 36 ? "…" : ""}」` : "资料研判";
  }
  if (proposal.kind === "profile_change") {
    return typeof payload.content === "string" ? payload.content : "档案变更";
  }
  if (proposal.kind === "plan_change") {
    const summary = typeof payload.summary === "string" ? payload.summary : "";
    return summary || "计划改动建议";
  }
  if (proposal.kind === "memory_change") {
    const content = typeof payload.content === "string" ? payload.content : "";
    const target = typeof payload.target_content === "string" ? payload.target_content : "";
    return content || target || "记忆候选";
  }
  return proposal.reason || "提案";
}

export function ProposalCard({
  proposal,
  selection,
  onSelection,
  busy,
  onDecide,
  isRejecting,
  onStartReject,
  onCancelReject,
  rejectReason,
  setRejectReason,
  planName,
}: {
  proposal: Proposal;
  /** 计划 id → 计划名（工作台已有清单）；解不出名字的地方才退回编号。 */
  planName?: (planId: number) => string | null;
  selection: string[];
  onSelection: (value: string[]) => void;
  busy: boolean;
  onDecide: (approved: boolean, reason?: string) => Promise<boolean>;
  isRejecting: boolean;
  onStartReject: () => void;
  onCancelReject: () => void;
  rejectReason: string;
  setRejectReason: (value: string) => void;
}) {
  // pending 提案默认全部展开——裁定动作不许藏在点击后面
  const [expanded, setExpanded] = useState(true);
  // 按钮变形的状态机：进行中转「处理中」，成功转「已批准/已驳回」（卡随后被页面摘走），
  // 失败必须清空回到可点——错误由页面错误条展示，不许被按钮动画掩盖。
  const [inFlight, setInFlight] = useState<null | "approve" | "reject">(null);
  const [decided, setDecided] = useState<null | "approve" | "reject">(null);

  const config = KIND_CONFIG[proposal.kind] ?? {
    label: "未知提案",
    badge: "UNKNOWN",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  };

  const isBlueprint = proposal.kind === "plan_blueprint";
  const nothingTicked = isBlueprint && selection.length === 0;
  const title = getProposalTitle(proposal);

  async function runDecide(approved: boolean, reason?: string) {
    if (inFlight !== null || decided !== null) return;
    setInFlight(approved ? "approve" : "reject");
    const ok = await onDecide(approved, reason);
    setInFlight(null);
    if (ok) setDecided(approved ? "approve" : "reject");
  }

  // 「批准生效 → 处理中 → 已批准」同一颗按钮的状态变形（roll）；驳回同理。
  const approveItems = [
    { id: "idle", label: "批准生效" },
    { id: "processing", label: "处理中" },
    { id: "approved", label: "已批准" },
  ];
  const rejectItems = [
    { id: "idle", label: "确认驳回" },
    { id: "processing", label: "处理中" },
    { id: "rejected", label: "已驳回" },
  ];
  const approveValue =
    decided === "approve" ? "approved" : inFlight === "approve" ? "processing" : "idle";
  const rejectValue =
    decided === "reject" ? "rejected" : inFlight === "reject" ? "processing" : "idle";

  function renderBody() {
    switch (proposal.kind) {
      case "plan_blueprint":
        return (
          <BlueprintSection
            proposal={proposal}
            selection={selection}
            onSelection={onSelection}
            planName={planName}
          />
        );
      case "material_judgment":
        return <JudgmentSection proposal={proposal} />;
      case "profile_change":
        return <ProfileChangeSection proposal={proposal} />;
      case "plan_change":
        return <PlanChangeSection proposal={proposal} planName={planName} />;
      case "memory_change":
        return <MemoryChangeSection proposal={proposal} />;
      default:
        return (
          <div className="py-2 text-[13px] text-white/50">
            未知类型的提案格式
          </div>
        );
    }
  }

  return (
    <div className="relative overflow-hidden transition-colors border border-white/[0.04] rounded-2xl bg-surface2/30 hover:bg-surface2/50">
      {/* 行头：结构对标 candidate-card */}
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-4 text-left cursor-pointer select-none"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls={`proposal-body-${proposal.id}`}
      >
        <div className="flex items-center gap-3 pr-4 min-w-0">
          {/* 左侧状态圆片：静态琥珀点与外圈，避免持续跳动 */}
          <div className="relative flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-amber-500/30">
            <div className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            <div className="absolute -inset-0.5 rounded-full border border-amber-500/15" />
          </div>

          {/* 类型标签（11-12px 白/40） */}
          <span className="text-[12px] font-medium text-white/50 tracking-wider hidden sm:block shrink-0">
            {config.label}
          </span>

          {/* 16px 标题行，font-semibold 加强 */}
          <div className="text-[16px] font-semibold text-primary/90 truncate">
            {title}
          </div>
        </div>

        {/* 行尾：元数据 + 类型徽章 + chevron */}
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-[11px] text-white/50 hidden md:block">
            {proposal.created_at.slice(0, 16).replace("T", " ")}
          </div>

          <ChevronDown
            className={`w-4 h-4 text-white/50 transition-transform duration-200 ${
              expanded ? "rotate-180" : ""
            }`}
          />
        </div>
      </button>

      {/* 展开区（手风琴 200ms） */}
      <div
        id={`proposal-body-${proposal.id}`}
        className="grid transition-all duration-200 ease-in-out"
        style={{ gridTemplateRows: expanded ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-5 pt-3 border-t border-white/[0.04] mx-4">
            {/* 背景说明（提案背景，按需展开） */}
            {proposal.reason && (
              <details className="group cursor-pointer mb-4">
                <summary className="inline-flex items-center gap-2 text-[12px] font-medium text-white/50 hover:text-white/70 transition-colors select-none">
                  <ChevronRight className="w-3.5 h-3.5 transition-transform group-open:rotate-90" />
                  背景说明
                </summary>
                <p className="text-[13px] text-white/60 leading-relaxed mt-2">
                  {proposal.reason}
                </p>
              </details>
            )}

            {/* 类型专属 body（内容直接坐卡面上，禁止卡套卡） */}
            {renderBody()}

            {/* 裁定动作条 */}
            <div
              className="pt-4 mt-3 border-t border-white/[0.04] flex flex-wrap items-center justify-between gap-4"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-3 flex-wrap">
                {!isRejecting ? (
                  <>
                    <ActionSwapButton
                      items={approveItems}
                      value={approveValue}
                      animation="roll"
                      variant="primary"
                      size="md"
                      onClick={() => runDecide(true)}
                      disabled={busy || nothingTicked || decided !== null}
                      className="px-6 text-[13px] bg-white text-black hover:bg-white/90"
                      title={nothingTicked ? "蓝图必须勾选至少一项阶段或任务才能批准" : undefined}
                    />
                    <button
                      type="button"
                      onClick={onStartReject}
                      disabled={busy || decided !== null}
                      className="px-6 py-2.5 border border-white/[0.08] text-white/60 hover:text-white rounded-full text-[13px] font-medium transition-colors hover:bg-white/[0.02] disabled:opacity-50"
                    >
                      驳回
                    </button>
                  </>
                ) : (
                  <div className="flex w-fit max-w-full flex-wrap items-center gap-3 rounded-xl border border-white/[0.06] bg-surface2/80 p-3">
                    <div className="text-[13px] text-white/70 font-medium pl-2">
                      驳回理由：
                    </div>
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && rejectReason.trim() && !busy) {
                          e.preventDefault();
                          runDecide(false, rejectReason);
                        }
                      }}
                      placeholder="必填，将进不可逆台账"
                      className="min-w-0 w-full bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none sm:w-[260px] focus:border-white/[0.15]"
                      disabled={busy || decided !== null}
                      autoFocus
                    />
                    <ActionSwapButton
                      items={rejectItems}
                      value={rejectValue}
                      animation="roll"
                      variant="ghost"
                      size="md"
                      onClick={() => runDecide(false, rejectReason)}
                      disabled={busy || !rejectReason.trim() || decided !== null}
                      className="px-5 text-[13px] bg-red-500/20 text-red-400 hover:bg-red-500/30 hover:text-red-400"
                    />
                    <button
                      type="button"
                      onClick={onCancelReject}
                      disabled={busy}
                      className="p-2 text-white/50 hover:text-white/80 transition-colors"
                      title="取消"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>

              {nothingTicked && (
                <span className="text-[12px] text-amber-400 font-medium">
                  蓝图必须勾选至少一项阶段或任务才能批准
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
