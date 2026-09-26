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
    <div className="py-2 space-y-3">
      {/* 动作徽章 + 作用域 + 类别 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium text-purple-400 bg-purple-500/10 px-2.5 py-0.5 rounded-full border border-purple-500/20 uppercase tracking-wider">
          {actionLabel}
        </span>
        <span className="text-[12px] text-white/50">
          {payload.scope === "global" ? "全局记忆" : `计划 #${payload.plan_id ?? ""} 记忆`}
        </span>
        {(payload.category_label || payload.category) && (
          <span className="px-2.5 py-0.5 bg-white/[0.04] text-white/60 text-[11px] rounded-full border border-white/[0.06]">
            {payload.category_label ?? payload.category}
          </span>
        )}
        {(payload.kind_label || payload.kind) && (
          <span className="px-2.5 py-0.5 bg-white/[0.04] text-white/60 text-[11px] rounded-full border border-white/[0.06]">
            {payload.kind_label ?? payload.kind}
          </span>
        )}
      </div>

      {/* 近似重复提示（琥珀提示，不是错误） */}
      {payload.duplicate_hint && (
        <div className="bg-amber-500/10 border border-amber-500/20 text-amber-400/90 rounded-xl px-3.5 py-2 text-[12px] flex items-center gap-2">
          <span className="font-medium shrink-0">近似重复提示：</span>
          <span>{payload.duplicate_hint}（仅供参考，不是错误）</span>
        </div>
      )}

      {/* 记忆主体：取代类加「原记忆 → 新记忆」两段式 */}
      {isSupersede ? (
        <div className="space-y-1.5 pl-3 border-l-2 border-purple-500/30">
          {payload.target_content && (
            <div className="text-[13px] text-white/40 line-through">
              原记忆：{payload.target_content}
            </div>
          )}
          <div className="text-[15px] font-semibold text-white/90">
            新记忆：{payload.content}
          </div>
        </div>
      ) : isVoid ? (
        <div className="text-[14px] text-white/60 line-through">
          作废记忆：{payload.target_content || payload.content}
        </div>
      ) : (
        <div className="text-[15px] font-semibold text-white/90 leading-relaxed">
          {payload.content || payload.target_content}
        </div>
      )}

      {/* 事实时间 / 复核时间 / 来源 */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-white/40 pt-1">
        {payload.fact_time && (
          <div>
            事实时间：<span className="text-white/60">{payload.fact_time}</span>
          </div>
        )}
        {payload.review_at && (
          <div>
            复核时间：<span className="text-white/60">{payload.review_at}</span>
          </div>
        )}
        {(payload.source_kind_label || payload.source_kind) && (
          <div>
            来源：<span className="text-white/60">{payload.source_kind_label ?? payload.source_kind}</span>
          </div>
        )}
      </div>

      {payload.reason && (
        <div className="text-[12px] text-white/40">
          <span className="text-white/30">提炼理由：</span>
          {payload.reason}
        </div>
      )}
    </div>
  );
}
