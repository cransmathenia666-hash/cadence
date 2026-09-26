import { type MaterialJudgmentPayload, type Proposal, type Judgment } from "@/lib/api";
import { QUESTION_LABELS } from "@/components/judgment-view";

export function JudgmentSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as MaterialJudgmentPayload;
  const judgment = payload.judgment;
  const keys = judgment ? (Object.keys(QUESTION_LABELS) as (keyof Judgment)[]) : [];

  return (
    <div className="space-y-4">
      {/* 资料原文 */}
      {payload.source_text && (
        <div className="border-l-[3px] border-white/20 pl-3.5 py-1 text-[13px] text-white/60 leading-relaxed">
          <span className="text-white/40 text-[12px] font-medium mr-1.5">资料原文：</span>
          {payload.source_text}
        </div>
      )}

      {/* 四问结论（无边框无底色，纯留白分隔） */}
      {judgment && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-5 my-2">
          {keys.map((key) => {
            const item = judgment[key];
            if (!item) return null;
            return (
              <div key={key} className="flex flex-col gap-1.5">
                <div className="text-[11px] font-medium uppercase tracking-wider text-white/40">
                  {QUESTION_LABELS[key] ?? key}
                </div>
                <div className="text-[13px] text-white/80 leading-relaxed">
                  {item.answer}
                </div>
                <div className="flex items-center gap-1.5 flex-wrap mt-1 text-[11px] text-white/35">
                  <span>档案依据：</span>
                  {item.profile_item_ids.length === 0 ? (
                    <span className="text-white/25">（依据不足）</span>
                  ) : (
                    item.profile_item_ids.map((id) => (
                      <span
                        key={id}
                        className="px-2 py-0.5 rounded-full text-[10px] bg-white/[0.04] border border-white/[0.06] text-white/60"
                      >
                        档案 #{id}
                      </span>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
