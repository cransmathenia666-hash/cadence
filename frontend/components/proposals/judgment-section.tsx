import { ChevronRight } from "lucide-react";
import { type MaterialJudgmentPayload, type Proposal, type Judgment } from "@/lib/api";
import { QUESTION_LABELS } from "@/components/judgment-view";

export function JudgmentSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as MaterialJudgmentPayload;
  const judgment = payload.judgment;
  const keys = judgment ? (Object.keys(QUESTION_LABELS) as (keyof Judgment)[]) : [];
  const answered = judgment
    ? keys.flatMap((key) => {
        const item = judgment[key];
        return item ? [{ key, item }] : [];
      })
    : [];

  return (
    <section aria-label="资料四问判断" className="grid gap-10 xl:grid-cols-[minmax(220px,0.85fr)_minmax(0,1.5fr)]">
      <div className="min-w-0 border-t border-white/15 pt-5">
        <h3 className="text-[14px] font-semibold text-white/85">待判断的资料</h3>
        <blockquote className="mt-5 max-w-[58ch] text-[16px] leading-8 text-white/75 [overflow-wrap:anywhere]">
          {payload.source_text || "未保留资料原文"}
        </blockquote>
      </div>

      <div className="min-w-0">
        <h3 className="border-b border-white/15 pb-5 text-[14px] font-semibold text-white/85">四问结论</h3>
        {answered.length > 0 ? (
          <ol>
            {answered.map(({ key, item }, index) => (
              <li key={key} className="grid gap-3 border-b border-white/10 py-6 sm:grid-cols-[32px_minmax(0,1fr)] sm:gap-5">
                <span className="pt-1 text-[12px] tabular-nums text-white/55">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h4 className="text-[13px] font-medium text-white/65">{QUESTION_LABELS[key] ?? key}</h4>
                  <p className="mt-2 text-[16px] leading-7 text-white/90 [overflow-wrap:anywhere]">{item.answer}</p>
                  {item.profile_item_ids.length === 0 && <p className="mt-2 text-[12px] text-amber-200">这一问的档案依据不足</p>}
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="py-6 text-[14px] text-white/65">这份提案没有可展示的四问结论。</p>}

        {answered.some(({ item }) => item.profile_item_ids.length > 0) && (
          <details className="group mt-5">
            <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
              <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
              查看各问引用的档案
            </summary>
            <dl className="mt-4 border-t border-white/10 text-[13px]">
              {answered.map(({ key, item }) => (
                <div key={key} className="grid gap-2 border-b border-white/10 py-3 sm:grid-cols-[minmax(100px,0.8fr)_minmax(0,1.2fr)]">
                  <dt className="text-white/65">{QUESTION_LABELS[key] ?? key}</dt>
                  <dd className="text-white/80">{item.profile_item_ids.length > 0 ? item.profile_item_ids.map((id) => `档案 #${id}`).join("、") : "依据不足"}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
    </section>
  );
}
