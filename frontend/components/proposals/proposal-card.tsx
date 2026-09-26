import { useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { type Proposal } from "@/lib/api";
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
    return goal || (payload.plan_id ? `计划 #${payload.plan_id} 方案` : "蓝图方案");
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
    return summary || (payload.plan_id ? `计划 #${payload.plan_id} 改动建议` : "计划改动");
  }
  if (proposal.kind === "memory_change") {
    const content = typeof payload.content === "string" ? payload.content : "";
    const target = typeof payload.target_content === "string" ? payload.target_content : "";
    return content || target || "记忆候选";
  }
  return proposal.reason || `提案 #${proposal.id}`;
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
}: {
  proposal: Proposal;
  selection: string[];
  onSelection: (value: string[]) => void;
  busy: boolean;
  onDecide: (approved: boolean, reason?: string) => Promise<void>;
  isRejecting: boolean;
  onStartReject: () => void;
  onCancelReject: () => void;
  rejectReason: string;
  setRejectReason: (value: string) => void;
}) {
  // pending 提案默认全部展开——裁定动作不许藏在点击后面
  const [expanded, setExpanded] = useState(true);

  const config = KIND_CONFIG[proposal.kind] ?? {
    label: "未知提案",
    badge: "UNKNOWN",
    badgeClass: "text-white/40 bg-white/5 border-white/10",
  };

  const isBlueprint = proposal.kind === "plan_blueprint";
  const nothingTicked = isBlueprint && selection.length === 0;
  const title = getProposalTitle(proposal);

  function renderBody() {
    switch (proposal.kind) {
      case "plan_blueprint":
        return (
          <BlueprintSection
            proposal={proposal}
            selection={selection}
            onSelection={onSelection}
          />
        );
      case "material_judgment":
        return <JudgmentSection proposal={proposal} />;
      case "profile_change":
        return <ProfileChangeSection proposal={proposal} />;
      case "plan_change":
        return <PlanChangeSection proposal={proposal} />;
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
      <div
        className="flex items-center justify-between px-4 py-4 cursor-pointer select-none"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3 pr-4 min-w-0">
          {/* 左侧状态圆片（待裁定=琥珀点+外圈 ping） */}
          <div className="w-5 h-5 rounded-full border border-amber-500/30 flex items-center justify-center shrink-0 relative">
            <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            <div className="absolute inset-0 rounded-full border border-amber-500 animate-ping opacity-20" />
          </div>

          {/* 类型标签（11-12px 白/40） */}
          <span className="text-[12px] font-medium text-white/40 tracking-wider hidden sm:block shrink-0">
            {config.label}
          </span>

          {/* 15px 标题行 */}
          <div className="text-[15px] font-medium text-primary/90 truncate">
            {title}
          </div>
        </div>

        {/* 行尾：元数据 + 类型徽章 + chevron */}
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-[12px] text-white/40 hidden md:block">
            #{proposal.id} · {proposal.created_at.slice(0, 16).replace("T", " ")}
          </div>

          <span
            className={`px-2.5 py-1 rounded-full border text-[11px] font-medium uppercase tracking-widest hidden sm:block ${config.badgeClass}`}
          >
            {config.badge}
          </span>

          <ChevronDown
            className={`w-4 h-4 text-white/40 transition-transform duration-200 ${
              expanded ? "rotate-180" : ""
            }`}
          />
        </div>
      </div>

      {/* 展开区（手风琴 200ms） */}
      <div
        className="grid transition-all duration-200 ease-in-out"
        style={{ gridTemplateRows: expanded ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-5 pt-3 border-t border-white/[0.04] mx-4">
            {/* 背景说明（13px 白/60） */}
            {proposal.reason && (
              <p className="text-[13px] text-white/60 leading-relaxed mb-4">
                背景说明：{proposal.reason}
              </p>
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
                    <button
                      type="button"
                      onClick={() => onDecide(true)}
                      disabled={busy || nothingTicked}
                      className="px-6 py-2.5 bg-white text-black hover:bg-gray-200 rounded-full text-[13px] font-medium transition-colors disabled:opacity-50"
                      title={nothingTicked ? "蓝图必须勾选至少一项阶段或任务才能批准" : undefined}
                    >
                      批准生效
                    </button>
                    <button
                      type="button"
                      onClick={onStartReject}
                      disabled={busy}
                      className="px-6 py-2.5 border border-white/[0.08] text-white/60 hover:text-white rounded-full text-[13px] font-medium transition-colors hover:bg-white/[0.02] disabled:opacity-50"
                    >
                      驳回
                    </button>
                  </>
                ) : (
                  <div className="flex flex-wrap items-center gap-3 bg-[#141416] p-3 rounded-2xl border border-white/[0.06] w-fit">
                    <div className="text-[13px] text-white/60 font-medium pl-2">
                      驳回理由：
                    </div>
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && rejectReason.trim() && !busy) {
                          e.preventDefault();
                          onDecide(false, rejectReason);
                        }
                      }}
                      placeholder="必填，将进不可逆台账"
                      className="bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none w-[260px] focus:border-white/[0.15]"
                      disabled={busy}
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => onDecide(false, rejectReason)}
                      disabled={busy || !rejectReason.trim()}
                      className="px-5 py-2 bg-red-500/20 text-red-400 rounded-full text-[13px] font-medium disabled:opacity-50 hover:bg-red-500/30 transition-colors"
                    >
                      确认驳回
                    </button>
                    <button
                      type="button"
                      onClick={onCancelReject}
                      disabled={busy}
                      className="p-2 text-white/40 hover:text-white/80 transition-colors"
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
