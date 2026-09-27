import { ChevronRight } from "lucide-react";
import { type ProfileChangePayload, type Proposal } from "@/lib/api";
import { CATEGORY_LABELS } from "./types";

export function ProfileChangeSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as ProfileChangePayload;

  return (
    <div className="py-2 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium text-sky-400 bg-sky-500/10 px-2.5 py-0.5 rounded-full border border-sky-500/20 uppercase tracking-wider">
          {payload.category}
        </span>
        <span className="text-[13px] text-white/60 font-medium">
          {CATEGORY_LABELS[payload.category] ?? payload.category}
        </span>
      </div>

      <div className="text-[15px] font-bold text-white/90 leading-relaxed">
        {payload.content}
      </div>

      {payload.why && (
        <details className="group cursor-pointer">
          <summary className="inline-flex items-center gap-1.5 text-[12px] text-white/50 hover:text-white/60 transition-colors select-none">
            <ChevronRight className="w-3 h-3 transition-transform group-open:rotate-90" />
            提炼依据
          </summary>
          <div className="mt-1.5 text-[12px] text-white/50">{payload.why}</div>
        </details>
      )}
    </div>
  );
}
