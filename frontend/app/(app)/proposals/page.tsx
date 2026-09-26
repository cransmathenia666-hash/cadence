"use client";

import { useEffect, useState } from "react";
import { Inbox } from "lucide-react";
import {
  ApiError,
  decideProposal,
  listProposals,
  type Proposal,
} from "@/lib/api";
import { selectionOf } from "@/components/blueprint-body";
import { ProposalCard } from "@/components/proposals/proposal-card";

export default function ProposalsPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [selections, setSelections] = useState<Record<number, string[]>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");

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

      setNotes((previous) => [
        approved
          ? done.effect === "blueprint_built"
            ? `已批准：计划 #${done.built?.plan_id ?? ""} 里建了 ` +
              `${done.built?.stages.length ?? 0} 个新阶段、${done.built?.tasks.length ?? 0} 件任务` +
              `${done.built?.notes.length ? `。${done.built.notes.join("；")}` : "。"}`
            : done.effect === "profile_written"
              ? `已批准：把「${done.written?.content ?? ""}」写进了长期档案（${done.written?.category ?? ""}）` +
                "——去「长期档案」页能看到它。"
              : done.effect === "node_updated" && done.updated !== null
                ? `已批准并原地修改：#${done.updated.node_id} 的 ${done.updated.changed.join("、")} 改为了 ` +
                  `${done.updated.changed.map((k) => done.updated?.after[k] ?? "（清空）").join("、")}（编号保持不变，台账已留痕）。`
                : done.effect === "node_added" && done.added !== null
                  ? `已批准并新建：${done.added.nodes
                      .map((node) => `${node.level === "stage" ? "阶段" : "任务"} #${node.id}「${node.title}」`)
                      .join("、")} 已进计划。`
                  : done.effect === "memory_added"
                    ? `已批准：已新增记忆「${done.remembered?.memory?.content ?? ""}」`
                    : done.effect === "memory_superseded"
                      ? `已批准：已用新记忆「${done.remembered?.memory?.content ?? ""}」取代旧记忆「${done.remembered?.before ?? ""}」`
                      : done.effect === "memory_renewed"
                        ? `已批准：已续期记忆「${done.remembered?.memory?.content ?? ""}」`
                        : done.effect === "memory_voided"
                          ? `已批准：已作废记忆「${done.remembered?.memory?.content ?? done.remembered?.before ?? ""}」`
                          : "已批准，只记账：这项不会改计划或档案。"
          : "已驳回，理由已记入台账。",
        ...previous,
      ]);

      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
      setRejectingId(null);
      setRejectReason("");
      refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
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
                  {pendingCount} Pending
                </span>
              )}
            </div>
          </div>
          <p className="text-[12px] text-white/40">
            所有可能改动实际数据的 AI 输出必须经过你在此拍板。勾选即生效，驳回必留理由。
          </p>
        </div>

        {/* 失败提示条 */}
        {error !== null && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
            <strong>操作失败：</strong>
            {error}
          </div>
        )}

        {/* 成功回执堆叠 */}
        {notes.length > 0 && (
          <div className="bg-emerald-500/10 border border-emerald-500/20 text-emerald-400/90 rounded-xl p-4 mb-8 text-[13px] space-y-1.5">
            {notes.map((text, index) => (
              <div key={index} className="flex items-start gap-2">
                <span className="text-emerald-400 shrink-0">✓</span>
                <span>{text}</span>
              </div>
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
            <Inbox className="w-10 h-10 text-white/20 mb-3 stroke-[1.5]" />
            <div className="text-[14px] text-white/60 mb-2 font-medium">当前没有待裁定的提案</div>
            <div className="text-[12px] text-white/40">
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
    </div>
  );
}
