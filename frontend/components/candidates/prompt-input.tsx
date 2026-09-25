import { FormEvent } from "react";
import { Loader2, ArrowUp, Sparkles } from "lucide-react";

export function PromptInput({
  plans,
  planChoice,
  setPlanChoice,
  rawText,
  setRawText,
  onSubmit,
  asking,
  bannedCount,
}: {
  plans: { id: number; goal: string }[];
  planChoice: string;
  setPlanChoice: (val: string) => void;
  rawText: string;
  setRawText: (val: string) => void;
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  asking: boolean;
  bannedCount: number;
}) {
  return (
    <div className="mb-6 mt-2 relative w-full">
      <form onSubmit={onSubmit} className="relative group/prompt bg-surface2/60 rounded-[24px] shadow-[0_8px_32px_rgba(0,0,0,0.2)] border border-white/[0.04] p-1.5 flex flex-col md:flex-row items-center transition-all focus-within:border-white/[0.15] focus-within:bg-surface2/80">
        <div className="shrink-0 relative w-full md:w-auto border-b border-white/[0.04] md:border-b-0 md:border-r md:border-white/[0.08] mr-2">
          <select
            value={planChoice}
            onChange={(e) => setPlanChoice(e.target.value)}
            className="w-full md:w-auto appearance-none bg-transparent border-none text-[13px] text-white/60 pl-4 pr-10 py-3 outline-none cursor-pointer hover:text-primary transition-colors focus:ring-0"
            disabled={asking}
          >
            <option value="" className="bg-surface2 text-primary">新方向 (不从属现有计划)</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id} className="bg-surface2 text-primary">
                #{item.id} {item.goal}
              </option>
            ))}
          </select>
          <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-40">
            <svg width="10" height="6" viewBox="0 0 10 6" fill="none">
              <path d="M1 1L5 5L9 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
        </div>

        <textarea
          id="raw"
          value={rawText}
          onChange={(e) => {
            setRawText(e.target.value);
            e.target.style.height = "";
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          disabled={asking}
          className="flex-1 w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[15px] text-primary placeholder-white/30 py-3 md:py-3.5 px-3 max-h-[160px] min-h-[48px] overflow-hidden leading-relaxed disabled:opacity-50"
          rows={1}
          placeholder="处境与困惑，例如：不知道该学什么..."
          required
        />
        
        <div className="shrink-0 p-1 w-full md:w-auto flex justify-end">
          <button
            type="submit"
            disabled={asking || rawText.trim() === ""}
            className="p-3 bg-white text-black rounded-full hover:bg-gray-200 transition-colors shadow-sm disabled:opacity-50 disabled:bg-white/10 disabled:text-white/40"
          >
            {asking ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <ArrowUp className="w-5 h-5" />
            )}
          </button>
        </div>
      </form>
      <div className="flex justify-between items-center mt-3 px-3">
        <span className="text-[11px] text-white/[0.35] tracking-wide">
          {bannedCount > 0 
            ? `已自动排除 ${bannedCount} 条历史否决项`
            : "将根据个人档案自动避开历史否决禁区"}
        </span>
      </div>
    </div>
  );
}
