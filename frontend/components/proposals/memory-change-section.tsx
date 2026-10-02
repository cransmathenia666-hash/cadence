import { ChevronRight } from "lucide-react";
import { type Proposal } from "@/lib/api";
import { type MemoryChangePayload } from "./types";

export function MemoryChangeSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as MemoryChangePayload;
  const isSupersede = payload.action === "supersede";
  const isVoid = payload.action === "void" || payload.decision === "void";
  const isRenew = payload.action === "renew" || payload.decision === "renew";
  const actionLabel =
    payload.action === "add"
      ? "新增记忆"
      : isSupersede
        ? "取代记忆"
        : isVoid
          ? "作废记忆"
          : isRenew
            ? "续期记忆"
            : payload.action === "review"
              ? "记忆复核"
              : payload.action;

  return (
    <section aria-label="记忆变更内容" className="max-w-[82ch]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-white/15 pb-5 text-[13px]">
        <h3 className="text-[16px] font-semibold text-white">{actionLabel}</h3>
        <p className="text-white/65">{payload.scope === "global" ? "全局记忆" : `计划 #${payload.plan_id ?? "?"} 记忆`}</p>
      </div>

      {payload.duplicate_hint && (
        <p className="mt-6 border-l border-amber-400/60 pl-4 text-[13px] leading-6 text-amber-100">
          <span className="font-medium">可能与已有记忆近似：</span>{payload.duplicate_hint}。这只是提示，不阻止你裁定。
        </p>
      )}

      {isSupersede ? (
        <div className="grid gap-7 py-9 sm:grid-cols-2 sm:gap-10">
          <div className="min-w-0 border-t border-white/15 pt-4">
            <p className="text-[12px] text-white/60">将被取代</p>
            <p className="mt-3 text-[16px] leading-7 text-white/65 [overflow-wrap:anywhere]">{payload.target_content || "原记忆内容未提供"}</p>
          </div>
          <div className="min-w-0 border-t border-white/50 pt-4">
            <p className="text-[12px] text-white/75">替换为</p>
            <p className="mt-3 text-[clamp(1.2rem,2vw,1.6rem)] font-medium leading-[1.45] text-white [overflow-wrap:anywhere]">{payload.content || "新记忆内容未提供"}</p>
          </div>
        </div>
      ) : isVoid ? (
        <div className="py-9">
          <p className="mb-3 text-[12px] text-white/65">将停止作为有效记忆</p>
          <p className="text-[clamp(1.2rem,2vw,1.6rem)] leading-[1.45] text-white/70 line-through decoration-white/50 [overflow-wrap:anywhere]">{payload.target_content || payload.content || "记忆内容未提供"}</p>
        </div>
      ) : (
        <div className="py-9">
          <p className="mb-3 text-[12px] text-white/65">{isRenew ? "保持有效的内容" : "拟保留的内容"}</p>
          <p className="text-[clamp(1.2rem,2vw,1.6rem)] font-medium leading-[1.45] text-white [overflow-wrap:anywhere]">{payload.content || payload.target_content || "记忆内容未提供"}</p>
        </div>
      )}

      {(payload.category_label || payload.category || payload.kind_label || payload.kind || payload.fact_time || payload.review_at || payload.source_kind_label || payload.source_kind) && (
        <dl className="grid gap-x-8 gap-y-4 border-y border-white/10 py-5 text-[13px] sm:grid-cols-2">
          {(payload.category_label || payload.category) && <div><dt className="text-white/55">分类</dt><dd className="mt-1 text-white/80">{payload.category_label ?? payload.category}</dd></div>}
          {(payload.kind_label || payload.kind) && <div><dt className="text-white/55">性质</dt><dd className="mt-1 text-white/80">{payload.kind_label ?? payload.kind}</dd></div>}
          {payload.fact_time && <div><dt className="text-white/55">事实时间</dt><dd className="mt-1 text-white/80">{payload.fact_time}</dd></div>}
          {payload.review_at && <div><dt className="text-white/55">复核时间</dt><dd className="mt-1 text-white/80">{payload.review_at}</dd></div>}
          {(payload.source_kind_label || payload.source_kind) && <div><dt className="text-white/55">来源</dt><dd className="mt-1 text-white/80">{payload.source_kind_label ?? payload.source_kind}</dd></div>}
        </dl>
      )}

      {payload.reason && (
        <details className="group mt-5">
          <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
            查看提炼依据
          </summary>
          <p className="mt-4 text-[14px] leading-7 text-white/75">{payload.reason}</p>
        </details>
      )}
    </section>
  );
}
