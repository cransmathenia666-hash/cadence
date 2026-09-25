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
    <div className="card-border-gradient shadow-card-glow relative overflow-hidden group/card rounded-2xl bg-surface2/80 p-5 md:p-6 mb-8 mt-2">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 rounded-full bg-white/[0.08] border border-white/10 flex items-center justify-center shadow-[0_0_16px_rgba(255,255,255,0.12)]">
          <Sparkles className="w-4 h-4 text-white/90" />
        </div>
        <div>
          <h2 className="text-[15px] font-medium text-primary/90">提需求找方向</h2>
          <p className="text-[12px] text-muted/60 mt-0.5">向决策引擎表达你的困惑或目标，它会结合你的档案筛选合适路线。</p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-4">
          <div className="flex flex-col gap-2">
            <label htmlFor="plan" className="text-[12px] font-medium text-muted/80 pl-1">
              针对哪个计划
            </label>
            <div className="relative">
              <select
                id="plan"
                value={planChoice}
                onChange={(e) => setPlanChoice(e.target.value)}
                className="w-full appearance-none bg-black/40 border border-white/[0.08] rounded-xl px-4 py-3 text-[14px] text-primary/90 hover:border-white/[0.15] focus:border-white/[0.2] focus:ring-0 transition-colors outline-none cursor-pointer"
                disabled={asking}
              >
                <option value="">新方向 (不从属现有计划)</option>
                {plans.map((item) => (
                  <option key={item.id} value={item.id}>
                    #{item.id} {item.goal}
                  </option>
                ))}
              </select>
              <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-50">
                <svg width="10" height="6" viewBox="0 0 10 6" fill="none">
                  <path d="M1 1L5 5L9 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
            </div>
          </div>
          
          <div className="flex flex-col gap-2">
            <label htmlFor="raw" className="text-[12px] font-medium text-muted/80 pl-1">
              处境与困惑 <span className="text-red-400/80">*</span>
            </label>
            <div className="relative group input-glow bg-black/40 rounded-xl border border-white/[0.08] flex items-end p-1.5 transition-all focus-within:border-white/[0.2] focus-within:bg-black/60">
              <textarea
                id="raw"
                value={rawText}
                onChange={(e) => {
                  setRawText(e.target.value);
                  e.target.style.height = "";
                  e.target.style.height = `${e.target.scrollHeight}px`;
                }}
                disabled={asking}
                className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[14px] text-primary placeholder-muted/30 py-2.5 px-3 max-h-[160px] min-h-[44px] overflow-hidden leading-relaxed disabled:opacity-50"
                rows={1}
                placeholder="例如：我不知道该学什么，想要提升后端工程实战能力并产出作品"
                required
              />
              <button
                type="submit"
                disabled={asking || rawText.trim() === ""}
                className="p-2.5 bg-white text-black rounded-xl hover:bg-gray-200 transition-colors shrink-0 shadow-sm disabled:opacity-50 disabled:bg-white/10 disabled:text-white/40"
              >
                {asking ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <ArrowUp className="w-5 h-5" />
                )}
              </button>
            </div>
          </div>
        </div>

        <div className="flex justify-between items-center mt-2 px-1">
          <span className="text-[11px] text-muted/50 tracking-wide">
            {bannedCount > 0 
              ? `已自动排除 ${bannedCount} 条历史否决项`
              : "将根据个人档案自动避开历史否决禁区"}
          </span>
        </div>
      </form>
    </div>
  );
}
