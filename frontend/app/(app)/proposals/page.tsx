"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Inbox } from "lucide-react";
import {
  ApiError,
  decideProposal,
  listProposals,
  type Proposal,
  type ProposalDecision,
} from "@/lib/api";
import { selectionOf } from "@/components/blueprint-body";
import { ProposalCard, getProposalTitle } from "@/components/proposals/proposal-card";
import { KIND_CONFIG } from "@/components/proposals/types";
import { ActionResultCard } from "@/components/ui/action-result-card";
import { ToastStack, type ToastData } from "@/components/ui/toast";
import { useWorkspace } from "@/components/shell/workspace-context";

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
  const [activeId, setActiveId] = useState<number | null>(null);
  const [resultId, setResultId] = useState<number | null>(null);
  const toastSeq = useRef(0);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const readingRef = useRef<HTMLDivElement | null>(null);

  const dismissToast = useCallback((id: number) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== id));
  }, []);

  // 失败留在页内：错误条出现时把它带进视野（nearest 只做最小滚动）
  useEffect(() => {
    if (error !== null) errorRef.current?.scrollIntoView({ block: "nearest" });
  }, [error]);

  function refresh() {
    listProposals()
      .then((items) => { setProposals(items); setError(null); })
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
  async function onDecide(
    proposal: Proposal,
    approved: boolean,
    reason?: string,
    extra?: { confirmContract?: boolean; contractOverrides?: Record<string, unknown> | null },
  ) {
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
        // v2 蓝图批准（OC-07）：显式确认 + 可选的契约文字微调；landing_mode 不传，
        // 由后端按规划会话落点自动分流（新方向 → 新建正式计划；已有落点 → 延续该计划）。
        confirmContract: extra?.confirmContract,
        contractOverrides: extra?.contractOverrides,
      });

      const id = ++toastSeq.current;
      setToasts((previous) => [...previous, { id, text: decisionNote(approved) }].slice(-4));
      if (approved) {
        setSettledApprovals((previous) => [
          ...previous.filter((item) => item.proposal.id !== proposal.id),
          { proposal: { ...proposal, payload: originalPayload }, decision: done },
        ]);
        setResultId(proposal.id);
      }

      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
      setRejectingId((current) => (current === proposal.id ? null : current));
      setRejectReason("");
      setActiveId((current) => (current === proposal.id ? null : current));
      refresh();
      window.requestAnimationFrame(() => readingRef.current?.focus());
      return true;
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const pendingCount = proposals?.length ?? 0;
  const activeProposal = proposals?.find((item) => item.id === activeId) ?? proposals?.[0] ?? null;
  const activeResult = settledApprovals.find((item) => item.proposal.id === resultId);

  function selectProposal(id: number) {
    if (busy) return;
    setActiveId(id);
    setResultId(null);
    setRejectingId(null);
    setRejectReason("");
    setError(null);
    window.requestAnimationFrame(() => readingRef.current?.focus());
  }

  return (
    <main className="min-h-screen bg-surface px-4 pb-24 pt-24 text-primary md:px-8 xl:px-12">
      <div className="mx-auto max-w-[1500px]">
        <header className="mb-10 border-b border-white/10 pb-7 md:mb-0 md:pb-9">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <h1 className="text-[clamp(2.5rem,5vw,4.5rem)] font-semibold leading-none tracking-[-0.035em]">提案裁定</h1>
            <p className="max-w-[40ch] text-[13px] leading-6 text-white/65">
              批准才会写入实际数据；驳回需要理由，并留下台账记录。
            </p>
          </div>
        </header>

        {/* 失败提示条：失败不进 Toast，留在页内直到下次操作 */}
        {error !== null && (
          <div
            ref={errorRef}
            role="alert"
            className="mt-6 border-l border-red-400 bg-red-500/10 px-4 py-3 text-[13px] text-red-200"
          >
            <strong>操作失败：</strong>
            {error}
          </div>
        )}

        <div className="grid gap-10 md:grid-cols-[minmax(230px,300px)_minmax(0,1fr)] md:gap-0">
          <nav className="md:sticky md:top-20 md:self-start md:pr-8" aria-label="待裁定提案">
            <div className="flex items-baseline justify-between border-b border-white/10 py-5">
              <h2 className="text-[15px] font-semibold">待处理</h2>
              <span className="text-[13px] tabular-nums text-amber-300">{proposals === null ? "—" : pendingCount}</span>
            </div>
            {proposals === null && error !== null ? (
              <button type="button" onClick={refresh} className="py-6 text-left text-[13px] text-white/80 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">重新读取提案</button>
            ) : proposals === null ? (
              <p className="py-6 text-[13px] text-white/60" role="status">正在读取提案…</p>
            ) : proposals.length === 0 ? (
              <p className="py-6 text-[13px] leading-6 text-white/60">目前没有待裁定项。</p>
            ) : (
              <ol className="max-h-[calc(100vh-15rem)] overflow-y-auto">
                {proposals.map((proposal) => {
                  const selected = !activeResult && activeProposal?.id === proposal.id;
                  return (
                    <li key={proposal.id} className="border-b border-white/[0.07]">
                      <button
                        type="button"
                        onClick={() => selectProposal(proposal.id)}
                        disabled={busy}
                        aria-current={selected ? "true" : undefined}
                        aria-controls="proposal-reading"
                        className={`group w-full py-5 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70 disabled:cursor-wait ${selected ? "text-white" : "text-white/65 hover:text-white"}`}
                      >
                        <span className="mb-2 flex items-center justify-between text-[11px]">
                          <span className={selected ? "text-amber-300" : "text-white/50"}>{KIND_CONFIG[proposal.kind]?.label ?? "提案"}</span>
                          <span className="tabular-nums text-white/50">#{proposal.id}</span>
                        </span>
                        <span className="flex items-start justify-between gap-3">
                          <span className={`line-clamp-2 text-[15px] leading-6 ${selected ? "font-semibold" : "font-medium"}`}>{getProposalTitle(proposal)}</span>
                          <ArrowRight className={`mt-1 h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none ${selected ? "text-white" : "text-white/40"}`} />
                        </span>
                        <time className="mt-2 block text-[11px] tabular-nums text-white/50" dateTime={proposal.created_at}>{proposal.created_at.slice(0, 16).replace("T", " ")}</time>
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}
            {settledApprovals.length > 0 && (
              <div className="mt-8">
                <h2 className="border-b border-white/10 pb-3 text-[13px] font-medium text-white/60">本次已批准</h2>
                <ol>
                  {settledApprovals.map(({ proposal }) => (
                    <li key={proposal.id} className="border-b border-white/[0.07]">
                      <button
                        type="button"
                        onClick={() => {
                          setResultId(proposal.id);
                          setError(null);
                          window.requestAnimationFrame(() => readingRef.current?.focus());
                        }}
                        aria-current={resultId === proposal.id ? "true" : undefined}
                        aria-controls="proposal-reading"
                        className={`w-full py-3 text-left text-[13px] leading-5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70 ${resultId === proposal.id ? "text-green" : "text-white/60 hover:text-white"}`}
                      >
                        <span className="mr-2 tabular-nums">#{proposal.id}</span>{getProposalTitle(proposal)}
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </nav>

          <div id="proposal-reading" ref={readingRef} tabIndex={-1} className="min-w-0 focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/30 md:min-h-[calc(100vh-14rem)] md:border-l md:border-white/10 md:pl-10 xl:pl-16">
            {activeResult ? (
              <section className="pt-5" aria-label="已完成的提案结果">
                <div className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-white/10 pb-7">
                  <div>
                    <span className="text-[12px] text-green">裁定已生效</span>
                    <h2 className="mt-3 text-[clamp(1.75rem,3vw,2.75rem)] font-semibold tracking-[-0.025em]">这次实际改动</h2>
                  </div>
                  {activeProposal && <button type="button" onClick={() => selectProposal(activeProposal.id)} className="text-[13px] text-white/75 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">继续处理待裁定项</button>}
                </div>
                <ActionResultCard proposal={activeResult.proposal} decision={activeResult.decision} onEnterWorkbench={enterWorkbench} />
              </section>
            ) : activeProposal ? (
              <ProposalCard
                key={activeProposal.id}
                proposal={activeProposal}
                planName={planName}
                selection={selectionOf(activeProposal, selections)}
                onSelection={(value) => setSelections((previous) => ({ ...previous, [activeProposal.id]: value }))}
                busy={busy}
                onDecide={(approved, reason, extra) =>
                  onDecide(activeProposal, approved, reason, extra)
                }
                isRejecting={rejectingId === activeProposal.id}
                onStartReject={() => { setRejectingId(activeProposal.id); setRejectReason(""); }}
                onCancelReject={() => { setRejectingId(null); setRejectReason(""); }}
                rejectReason={rejectingId === activeProposal.id ? rejectReason : ""}
                setRejectReason={setRejectReason}
              />
            ) : proposals !== null ? (
              <div className="flex min-h-[52vh] flex-col items-start justify-center border-b border-white/10 py-16">
                <Inbox className="mb-8 h-9 w-9 text-white/45 stroke-[1.4]" aria-hidden="true" />
                <h2 className="text-[clamp(1.75rem,3vw,2.5rem)] font-medium tracking-[-0.025em]">目前没有待裁定的提案</h2>
                <p className="mt-4 max-w-[48ch] text-[14px] leading-7 text-white/65">候选清单生成蓝图、对话提炼档案变更或记忆扫描后，待你裁定的内容会出现在左侧。</p>
              </div>
            ) : error !== null ? (
              <div className="pt-9 text-[14px] leading-7 text-white/70">提案尚未读取成功。检查连接后，从左侧重新读取。</div>
            ) : (
              <div className="pt-9" role="status" aria-busy="true">
                <div className="h-8 w-2/3 animate-pulse bg-white/[0.06] motion-reduce:animate-none" />
                <div className="mt-8 h-4 w-1/2 animate-pulse bg-white/[0.04] motion-reduce:animate-none" />
                <span className="sr-only">正在读取待裁定提案</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 成功回执：右下角短 Toast，约 6 秒自动消失，可手动关 */}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}
