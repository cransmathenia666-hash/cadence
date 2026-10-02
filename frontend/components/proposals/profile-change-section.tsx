import { ChevronRight } from "lucide-react";
import { type ProfileChangePayload, type Proposal } from "@/lib/api";
import { CATEGORY_LABELS } from "./types";

export function ProfileChangeSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as ProfileChangePayload;

  return (
    <section aria-label="拟写入档案的内容" className="max-w-[76ch]">
      <div className="border-t border-white/15 pt-5">
        <p className="text-[13px] text-white/65">
          归入 <span className="font-medium text-white/85">{CATEGORY_LABELS[payload.category] ?? payload.category}</span>
          {payload.plan_id && <span className="ml-3">· 来自计划 #{payload.plan_id}</span>}
        </p>
        <blockquote className="mt-8 border-l border-white/35 pl-5 text-[clamp(1.25rem,2.2vw,1.75rem)] font-medium leading-[1.5] tracking-[-0.015em] text-white [overflow-wrap:anywhere]">
          {payload.content}
        </blockquote>
      </div>

      {payload.why && (
        <details className="group mt-9 border-t border-white/10 pt-4">
          <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
            为什么建议这样记
          </summary>
          <p className="mt-4 max-w-[70ch] text-[14px] leading-7 text-white/75">{payload.why}</p>
        </details>
      )}
    </section>
  );
}
