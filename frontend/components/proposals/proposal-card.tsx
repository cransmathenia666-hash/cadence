import { useState } from "react";
import { ArrowLeft, ChevronRight, X } from "lucide-react";
import { isBlueprintV2, type BlueprintPayload, type Proposal } from "@/lib/api";
import { ActionSwapButton } from "@/components/motion/action-swap";
import { KIND_CONFIG } from "./types";
import {
  BlueprintSection,
  contractDraftOf,
  contractDraftErrorOf,
  contractOverridesOf,
  type ContractTextDraft,
} from "./blueprint-section";
import { JudgmentSection } from "./judgment-section";
import { ProfileChangeSection } from "./profile-change-section";
import { PlanChangeSection } from "./plan-change-section";
import { ContractChangeSection } from "./contract-change-section";
import { MemoryChangeSection } from "./memory-change-section";

export function getProposalTitle(proposal: Proposal): string {
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
  if (proposal.kind === "contract_change") {
    const summary = typeof payload.diff_summary === "string" ? payload.diff_summary : "";
    return summary || "成果契约修正";
  }
  if (proposal.kind === "memory_change") {
    const content = typeof payload.content === "string" ? payload.content : "";
    const target = typeof payload.target_content === "string" ? payload.target_content : "";
    return content || target || "记忆候选";
  }
  return proposal.reason || "提案";
}

function getReadingTitle(proposal: Proposal): string {
  switch (proposal.kind) {
    case "material_judgment": return "这一份资料如何判断";
    case "profile_change": return "拟写入长期档案";
    case "plan_change": return "计划将怎样改变";
    case "contract_change": return "成果契约将怎样改变";
    case "memory_change": return "这条记忆将怎样变化";
    default: return getProposalTitle(proposal);
  }
}

export function ProposalCard({
  proposal,
  selection,
  onSelection,
  busy,
  onDecide,
  onReturnBlueprint,
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
  onDecide: (
    approved: boolean,
    reason?: string,
    extra?: { confirmContract?: boolean; contractOverrides?: Record<string, unknown> | null },
  ) => Promise<boolean>;
  onReturnBlueprint?: (reason: string) => Promise<boolean>;
  isRejecting: boolean;
  onStartReject: () => void;
  onCancelReject: () => void;
  rejectReason: string;
  setRejectReason: (value: string) => void;
}) {
  // 请求期间显示处理中；成功由页面切换到回执或下一条，失败保持当前提案与输入。
  const [inFlight, setInFlight] = useState<null | "approve" | "reject">(null);
  // v2 蓝图批准的显式确认（OC-07）：没勾就不发批准请求——后端也会拒绝，不半猜。
  const [confirmContract, setConfirmContract] = useState(false);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnReason, setReturnReason] = useState("");
  const [returnPending, setReturnPending] = useState(false);
  // 契约文字草稿（可选的简单编辑）：页面用 proposal.id 做 key，切提案即重置。
  const [contractDraft, setContractDraft] = useState<ContractTextDraft | null>(() =>
    contractDraftOf(proposal.payload as unknown as BlueprintPayload),
  );

  const config = KIND_CONFIG[proposal.kind] ?? {
    label: "未知提案",
    badge: "UNKNOWN",
    badgeClass: "text-white/65 bg-white/[0.05] border-white/[0.1]",
  };

  const isBlueprint = proposal.kind === "plan_blueprint";
  const blueprintPayload = proposal.payload as unknown as BlueprintPayload;
  const blueprintV2 = isBlueprint && isBlueprintV2(proposal.payload);
  const blueprintV1 = isBlueprint && !blueprintV2;
  const canReturnToPlanning =
    blueprintV2 &&
    typeof blueprintPayload.candidate_id === "number" &&
    typeof blueprintPayload.planning_session_id === "number" &&
    onReturnBlueprint !== undefined;
  const nothingTicked = isBlueprint && selection.length === 0;
  const confirmMissing = blueprintV2 && !confirmContract;
  const contractDraftError = blueprintV2 ? contractDraftErrorOf(contractDraft) : null;
  const approveBlocked = nothingTicked || blueprintV1 || confirmMissing || contractDraftError !== null;
  const approveBlockedReason = blueprintV1
    ? "旧版蓝图（没有成果契约）不能直接批准——请在规划对话里重新生成 v2 蓝图后再来"
    : confirmMissing
      ? "批准前必须先勾选「确认成果契约」"
      : contractDraftError
        ? contractDraftError
        : nothingTicked
          ? "蓝图必须勾选至少一项阶段或任务才能批准"
          : undefined;
  const title = getReadingTitle(proposal);

  async function runDecide(approved: boolean, reason?: string) {
    if (inFlight !== null || returnPending) return;
    // 不满足批准前提就不发请求（就地说明，见页脚提示），也不进「处理中」状态。
    if (approved && approveBlocked) return;
    setInFlight(approved ? "approve" : "reject");
    const extra =
      approved && blueprintV2
        ? {
            confirmContract: true,
            contractOverrides: contractOverridesOf(
              proposal.payload as unknown as BlueprintPayload,
              contractDraft,
            ),
          }
        : undefined;
    await onDecide(approved, reason, extra);
    setInFlight(null);
  }

  async function runReturn() {
    const reason = returnReason.trim();
    if (!reason || !onReturnBlueprint || inFlight !== null || returnPending) return;
    setReturnPending(true);
    const ok = await onReturnBlueprint(reason);
    setReturnPending(false);
    if (ok) {
      setReturnOpen(false);
      setReturnReason("");
    }
  }

  // 按钮只承担请求中的状态反馈；成功后切到真实结果，而不是停在一颗按钮上。
  const approveItems = [
    { id: "idle", label: "批准生效" },
    { id: "processing", label: "处理中" },
  ];
  const rejectItems = [
    { id: "idle", label: "确认驳回" },
    { id: "processing", label: "处理中" },
  ];
  const approveValue = inFlight === "approve" ? "processing" : "idle";
  const rejectValue = inFlight === "reject" ? "processing" : "idle";

  function renderBody() {
    switch (proposal.kind) {
      case "plan_blueprint":
        return (
          <BlueprintSection
            proposal={proposal}
            selection={selection}
            onSelection={onSelection}
            planName={planName}
            contractDraft={contractDraft}
            onContractDraftChange={setContractDraft}
          />
        );
      case "material_judgment":
        return <JudgmentSection proposal={proposal} />;
      case "profile_change":
        return <ProfileChangeSection proposal={proposal} />;
      case "plan_change":
        return <PlanChangeSection proposal={proposal} planName={planName} />;
      case "contract_change":
        return <ContractChangeSection proposal={proposal} planName={planName} />;
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
    <article className="min-w-0" aria-labelledby={`proposal-title-${proposal.id}`}>
      <header className="border-b border-white/10 pb-8">
        <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-white/60">
          <span className="font-medium text-amber-300">待裁定</span>
          <span>{config.label}</span>
          <time dateTime={proposal.created_at}>{proposal.created_at.slice(0, 16).replace("T", " ")}</time>
          <span className="ml-auto tabular-nums">提案 #{proposal.id}</span>
        </div>
        <h2 id={`proposal-title-${proposal.id}`} className="max-w-[22ch] text-[clamp(1.75rem,3vw,2.75rem)] font-semibold leading-[1.18] tracking-[-0.025em] text-primary [overflow-wrap:anywhere]">
          {title}
        </h2>
        {proposal.reason && (
          <details className="group mt-6 max-w-[70ch]">
            <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/70">
              <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
              查看提案背景
            </summary>
            <p className="mt-3 border-l border-white/20 pl-4 text-[14px] leading-7 text-white/70">{proposal.reason}</p>
          </details>
        )}
      </header>

      <div className="py-9">{renderBody()}</div>

      <div className="sticky bottom-0 z-10 -mx-4 border-t border-white/10 bg-surface px-4 py-4 md:mx-0 md:px-0">
        {/* v2 蓝图的显式确认（OC-07）：勾了才发批准请求；旧 v1 只读，给后端同款理由。 */}
        {isBlueprint && !isRejecting && (
          <div className="mb-3">
            {blueprintV2 ? (
              <div className="flex flex-col gap-1">
                <label
                  htmlFor={`confirm-contract-${proposal.id}`}
                  className="flex w-fit cursor-pointer items-start gap-2.5 text-[13px] leading-5 text-white/85"
                >
                  <input
                    id={`confirm-contract-${proposal.id}`}
                    type="checkbox"
                    checked={confirmContract}
                    onChange={(event) => setConfirmContract(event.target.checked)}
                    disabled={busy}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-white disabled:opacity-50"
                  />
                  确认成果契约：我认可这个成果定义和验收条件
                </label>
                {!confirmContract && (
                  <p className="pl-[26px] text-[12px] leading-5 text-amber-300/90">
                    批准前必须勾选——没确认契约的批准不会被发出（后端同样会拒绝，提案保持待裁定）。
                  </p>
                )}
              </div>
            ) : (
              <p className="max-w-[80ch] text-[12px] leading-5 text-amber-300/90">
                这是旧版蓝图（没有成果契约），不能直接批准——请在规划对话里重新生成 v2
                蓝图后再来。这里可以先驳回（驳回理由进台账，规划对话可继续）。
              </p>
            )}
          </div>
        )}
        {canReturnToPlanning && returnOpen && (
          <div className="mb-4 border-l border-amber-400/70 bg-amber-500/[0.06] px-4 py-3">
            <label htmlFor={`return-blueprint-${proposal.id}`} className="block text-[13px] font-medium text-white/85">
              为什么先不批准这版蓝图？
            </label>
            <p className="mt-1 text-[12px] leading-5 text-white/55">
              写清缺少的事实或需要调整的地方；提交后会回到同一条规划对话，蓝图本身不再等待裁定。
            </p>
            <textarea
              id={`return-blueprint-${proposal.id}`}
              value={returnReason}
              onChange={(event) => setReturnReason(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void runReturn();
                }
              }}
              rows={2}
              placeholder="例如：还没有确认跟做材料，以及每周可投入的时间。"
              disabled={busy || returnPending}
              className="mt-3 w-full resize-y rounded-md border border-white/[0.12] bg-black/20 px-3 py-2 text-[13px] leading-6 text-primary outline-none placeholder:text-white/35 focus:border-white/35 disabled:opacity-50"
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => { setReturnOpen(false); setReturnReason(""); }}
                disabled={busy || returnPending}
                className="rounded-md border border-white/[0.12] px-4 py-2 text-[12px] text-white/70 hover:border-white/30 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70 disabled:opacity-50"
              >
                先不退回
              </button>
              <button
                type="button"
                onClick={() => void runReturn()}
                disabled={busy || returnPending || !returnReason.trim()}
                className="rounded-md bg-amber-500 px-4 py-2 text-[12px] font-medium text-black hover:bg-amber-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50"
              >
                {returnPending ? "正在回到规划对话…" : "确认退回并补充信息"}
              </button>
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3 flex-wrap">
                {!isRejecting ? (
                  <>
                    {canReturnToPlanning && !returnOpen && (
                      <button
                        type="button"
                        onClick={() => setReturnOpen(true)}
                        disabled={busy || returnPending}
                        className="inline-flex items-center gap-2 rounded-md border border-white/[0.12] px-4 py-2.5 text-[13px] font-medium text-white/75 transition-colors hover:border-white/[0.3] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70 disabled:opacity-50"
                      >
                        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                        补充信息，回规划对话
                      </button>
                    )}
                    <ActionSwapButton
                      items={approveItems}
                      value={approveValue}
                      animation="roll"
                      variant="primary"
                      size="md"
                      onClick={() => runDecide(true)}
                      disabled={busy || approveBlocked}
                      className="px-6 text-[13px] bg-white text-black hover:bg-white/90"
                      title={approveBlockedReason}
                    />
                    <button
                      type="button"
                      onClick={onStartReject}
                      disabled={busy}
                      className="rounded-md border border-white/15 px-6 py-2.5 text-[13px] font-medium text-white/75 transition-colors hover:border-white/35 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70 disabled:opacity-50"
                    >
                      驳回
                    </button>
                  </>
                ) : (
                  <div className="flex w-full flex-wrap items-center gap-3 border-l border-red-400/60 pl-4">
                    <label htmlFor={`reject-reason-${proposal.id}`} className="text-[13px] font-medium text-white/80">驳回理由</label>
                    <input
                      id={`reject-reason-${proposal.id}`}
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && rejectReason.trim() && !busy) {
                          e.preventDefault();
                          runDecide(false, rejectReason);
                        }
                      }}
                      placeholder="必填，将进不可逆台账"
                      className="min-w-0 w-full border-b border-white/30 bg-transparent px-1 py-2 text-[13px] text-primary outline-none placeholder:text-white/50 focus:border-white sm:w-[300px]"
                      disabled={busy}
                      autoFocus
                    />
                    <ActionSwapButton
                      items={rejectItems}
                      value={rejectValue}
                      animation="roll"
                      variant="ghost"
                      size="md"
                      onClick={() => runDecide(false, rejectReason)}
                      disabled={busy || !rejectReason.trim()}
                      className="px-5 text-[13px] bg-red-500/20 text-red-400 hover:bg-red-500/30 hover:text-red-400"
                    />
                    <button
                      type="button"
                      onClick={onCancelReject}
                      disabled={busy}
                      className="p-2 text-white/50 hover:text-white/80 transition-colors"
                      aria-label="取消驳回"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>

              {approveBlocked && approveBlockedReason && (
                <span className="max-w-[46ch] text-[12px] font-medium text-amber-400">
                  {approveBlockedReason}
                </span>
              )}
        </div>
      </div>
    </article>
  );
}
