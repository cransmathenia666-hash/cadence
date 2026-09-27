"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { ToastStack, type ToastData } from "@/components/ui/toast";

/** 成功后先让按钮的「已批准/已驳回」变形被看见，再把卡摘走。 */
const SETTLED_CARD_LINGER_MS = 900;

/** 裁定成功的回执文案（只进 Toast；失败永远留在页内错误条）。 */
function decisionNote(done: ProposalDecision, approved: boolean): string {
  if (!approved) return "已驳回，理由已记入台账。";
  switch (done.effect) {
    case "blueprint_built":
      return (
        `已批准：计划 #${done.built?.plan_id ?? ""} 里建了 ` +
        `${done.built?.stages.length ?? 0} 个新阶段、${done.built?.tasks.length ?? 0} 件任务` +
        `${done.built?.notes.length ? `。${done.built.notes.join("；")}` : "。"}`
      );
    case "profile_written":
      return (
        `已批准：把「${done.written?.content ?? ""}」写进了长期档案（${done.written?.category ?? ""}）` +
        "——去「长期档案」页能看到它。"
      );
    case "node_updated":
      return done.updated !== null
        ? `已批准并原地修改：#${done.updated.node_id} 的 ${done.updated.changed.join("、")} 改为了 ` +
          `${done.updated.changed.map((k) => done.updated?.after[k] ?? "（清空）").join("、")}（编号保持不变，台账已留痕）。`
        : "已批准，只记账：这项不会改计划或档案。";
    case "node_added":
      return done.added !== null
        ? `已批准并新建：${done.added.nodes
            .map((node) => `${node.level === "stage" ? "阶段" : "任务"} #${node.id}「${node.title}」`)
            .join("、")} 已进计划。`
        : "已批准，只记账：这项不会改计划或档案。";
    case "memory_added":
      return `已批准：已新增记忆「${done.remembered?.memory?.content ?? ""}」`;
    case "memory_superseded":
      return `已批准：已用新记忆「${done.remembered?.memory?.content ?? ""}」取代旧记忆「${done.remembered?.before ?? ""}」`;
    case "memory_renewed":
      return `已批准：已续期记忆「${done.remembered?.memory?.content ?? ""}」`;
    case "memory_voided":
      return `已批准：已作废记忆「${done.remembered?.memory?.content ?? done.remembered?.before ?? ""}」`;
    default:
      return "已批准，只记账：这项不会改计划或档案。";
  }
}

export default function ProposalsPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<ToastData[]>([]);
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

  /** 返回是否成功（成功=true）；失败只落页内错误条，卡上的按钮必须回到可点。 */
  async function onDecide(proposal: Proposal, approved: boolean, reason?: string) {
    setBusy(true);
    setError(null);
    const selected = selectionOf(proposal, selections);
    try {
      const done = await decideProposal(proposal.id, {
        approved,
        reason,
        selected: proposal.kind === "plan_blueprint" ? selected : undefined,
      });

      const id = ++toastSeq.current;
      setToasts((previous) => [...previous, { id, text: decisionNote(done, approved) }].slice(-4));

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
      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12">
        {/* 页头 */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-4">
              <h1 className="text-[20px] font-semibold text-primary/90 tracking-tight">
                提案裁定
              </h1>
              {proposals !== null && (
                <span className="px-2.5 py-1 bg-amber-500/10 border border-amber-500/20 rounded-full text-[11px] font-medium text-amber-500 uppercase tracking-widest shrink-0">
                  {pendingCount} 条待裁定
                </span>
              )}
            </div>
          </div>
          <p className="text-[12px] text-white/60">
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
            <Inbox className="w-10 h-10 text-white/50 mb-3 stroke-[1.5]" />
            <div className="text-[14px] text-white/60 mb-2 font-medium">当前没有待裁定的提案</div>
            <div className="text-[12px] text-white/50">
              候选清单生成蓝图、对话提炼档案变更或记忆扫描后，会出现在这里
            </div>
          </div>
        )}

        {/* 提案胶囊行列表 */}
        {proposals !== null && proposals.length > 0 && (
          <div className="flex flex-col gap-3">
            {proposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
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
