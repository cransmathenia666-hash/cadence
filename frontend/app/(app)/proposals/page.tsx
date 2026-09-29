"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Inbox } from "lucide-react";
import {
  ApiError,
  decideProposal,
  listProposals,
  type Proposal,
  type ProposalDecision,
} from "@/lib/api";
import { selectionOf } from "@/components/blueprint-body";
import { ProposalCard } from "@/components/proposals/proposal-card";
import { ActionResultCard } from "@/components/ui/action-result-card";
import { ToastStack, type ToastData } from "@/components/ui/toast";
import { useWorkspace } from "@/components/shell/workspace-context";

/** 成功后先让按钮的「已批准/已驳回」变形被看见，再把卡摘走。 */
const SETTLED_CARD_LINGER_MS = 900;

type SettledApproval = { proposal: Proposal; decision: ProposalDecision };

/** 驳回提示仍用短 Toast；批准后的结果会留在页面交接卡中（卡上带着标题与去向）。 */
function decisionNote(approved: boolean): string {
  return approved ? "已批准，已按提案写入实际数据。" : "已驳回，理由已记入台账。";
}

export default function ProposalsPage() {
  const router = useRouter();
  const { plans, refreshPlans, setSelectedPlanId } = useWorkspace();
  // 提案正文优先给计划名；清单里查不到（已收尾/不在当前列表）才退回编号。
  const planName = useCallback(
    (planId: number) => plans.find((plan) => plan.id === planId)?.goal ?? null,
    [plans],
  );
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<ToastData[]>([]);
  const [settledApprovals, setSettledApprovals] = useState<SettledApproval[]>([]);
  const [selections, setSelections] = useState<Record<number, string[]>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const toastSeq = useRef(0);
  const errorRef = useRef<HTMLDivElement | null>(null);

  const dismissToast = useCallback((id: number) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== id));
  }, []);

  // 失败留在页内：错误条出现时把它带进视野（nearest 只做最小滚动）
  useEffect(() => {
    if (error !== null) errorRef.current?.scrollIntoView({ block: "nearest" });
  }, [error]);

  function refresh() {
    listProposals()
      .then(setProposals)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取提案时出了意外错误"),
      );
  }

  useEffect(() => {
    refresh();
  }, []);

  function enterWorkbench(planId: number) {
    setSelectedPlanId(planId);
    void refreshPlans();
    router.push(`/workbench?plan_id=${encodeURIComponent(String(planId))}`);
  }

  /** 返回是否成功（成功=true）；失败只落页内错误条，卡上的按钮必须回到可点。 */
  async function onDecide(proposal: Proposal, approved: boolean, reason?: string) {
    setBusy(true);
    setError(null);
    // payload 必须在裁定前保留：后端裁定回执不会重复返回计划归属等原始上下文。
    const originalPayload = proposal.payload;
    const selected = selectionOf(proposal, selections);
    try {
      const done = await decideProposal(proposal.id, {
        approved,
        reason,
        selected: proposal.kind === "plan_blueprint" ? selected : undefined,
      });

      const id = ++toastSeq.current;
      setToasts((previous) => [...previous, { id, text: decisionNote(approved) }].slice(-4));
      if (approved) {
        setSettledApprovals((previous) => [
          ...previous.filter((item) => item.proposal.id !== proposal.id),
          { proposal: { ...proposal, payload: originalPayload }, decision: done },
        ]);
      }

      // 先让卡上的「已批准/已驳回」变形停留一瞬（驳回态还依赖 rejectingId 保持输入行），
      // 再收掉驳回输入、摘卡并对账。
      window.setTimeout(() => {
        setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
        setRejectingId((current) => (current === proposal.id ? null : current));
        setRejectReason("");
        refresh();
      }, SETTLED_CARD_LINGER_MS);
      return true;
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const pendingCount = proposals?.length ?? 0;

  return (
    <div className="flex flex-col h-full min-h-screen pt-20 pb-24">
      <div className="mx-auto w-full max-w-[1120px] px-4 md:px-8">
        {/* 页头 */}
        <div className="mb-10">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-4">
              <h1 className="text-[26px] font-semibold text-primary/90 tracking-tight">
                提案裁定
              </h1>
              {proposals !== null && (
                <span className="shrink-0 rounded-full border border-amber-500/20 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-400">
                  {pendingCount} 条待裁定
                </span>
              )}
            </div>
          </div>
          <p className="text-[13px] text-white/60 mt-2">
            批准后才会写入实际数据；驳回必须填写理由并记入台账。
          </p>
        </div>

        {/* 失败提示条：失败不进 Toast，留在页内直到下次操作 */}
        {error !== null && (
          <div
            ref={errorRef}
            className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]"
          >
            <strong>操作失败：</strong>
            {error}
          </div>
        )}

        {settledApprovals.length > 0 && (
          <div className="mb-8 space-y-4" aria-label="已完成的提案结果">
            {settledApprovals.map(({ proposal, decision }) => (
              <ActionResultCard
                key={proposal.id}
                proposal={proposal}
                decision={decision}
                onEnterWorkbench={enterWorkbench}
              />
            ))}
          </div>
        )}

        {/* 加载态 */}
        {proposals === null && (
          <div className="flex flex-col items-center justify-center py-24 text-center border border-white/[0.04] rounded-2xl bg-surface2/30">
            <div className="w-5 h-5 border-2 border-white/20 border-t-white/80 rounded-full animate-spin mb-3" />
            <div className="text-[13px] text-white/50">正在读取待裁定提案…</div>
          </div>
        )}

        {/* 空态 */}
        {proposals !== null && proposals.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 text-center border border-white/[0.04] rounded-2xl bg-surface2/30">
            <Inbox className="w-12 h-12 text-white/50 mb-4 stroke-[1.5]" />
            <div className="text-[16px] text-white/70 mb-2 font-semibold">当前没有待裁定的提案</div>
            <div className="text-[12px] text-white/50">
              候选清单生成蓝图、对话提炼档案变更或记忆扫描后，会出现在这里
            </div>
          </div>
        )}

        {/* 提案胶囊行列表 */}
        {proposals !== null && proposals.length > 0 && (
          <div className="flex flex-col gap-4">
            {proposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                planName={planName}
                selection={selectionOf(proposal, selections)}
                onSelection={(value) =>
                  setSelections((previous) => ({ ...previous, [proposal.id]: value }))
                }
                busy={busy}
                onDecide={(approved, reason) => onDecide(proposal, approved, reason)}
                isRejecting={rejectingId === proposal.id}
                onStartReject={() => {
                  setRejectingId(proposal.id);
                  setRejectReason("");
                }}
                onCancelReject={() => {
                  setRejectingId(null);
                  setRejectReason("");
                }}
                rejectReason={rejectingId === proposal.id ? rejectReason : ""}
                setRejectReason={setRejectReason}
              />
            ))}
          </div>
        )}
      </div>

      {/* 成功回执：右下角短 Toast，约 6 秒自动消失，可手动关 */}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
