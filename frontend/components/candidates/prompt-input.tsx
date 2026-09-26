import { FormEvent, KeyboardEvent } from "react";
import { Loader2, ArrowUp } from "lucide-react";

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
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!asking && rawText.trim() !== "") {
        onSubmit(e as unknown as FormEvent<HTMLFormElement>);
      }
    }
  };

  return (
    <form 
      onSubmit={onSubmit} 
      className="w-full bg-[#141416] border border-white/[0.06] rounded-2xl flex flex-col focus-within:border-white/[0.15] transition-colors p-3 shadow-lg"
    >
      <textarea
        value={rawText}
        onChange={(e) => {
          setRawText(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = `${e.target.scrollHeight}px`;
        }}
        onKeyDown={handleKeyDown}
        disabled={asking}
        className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[15px] text-primary placeholder-white/40 py-2 px-1 min-h-[56px] max-h-[240px] leading-relaxed"
        rows={1}
        placeholder="处境与困惑，例如：不知道该学什么..."
        required
      />
      
      <div className="flex items-center justify-between mt-2 px-1">
        <select
          value={planChoice}
          onChange={(e) => setPlanChoice(e.target.value)}
          className="appearance-none bg-white/[0.04] hover:bg-white/[0.08] text-white/70 text-[12px] px-4 py-1.5 rounded-full border border-white/[0.04] outline-none cursor-pointer transition-colors"
          disabled={asking}
        >
          <option value="" className="bg-[#141416] text-primary">新方向 (不从属现有计划)</option>
          {plans.map((item) => (
            <option key={item.id} value={item.id} className="bg-[#141416] text-primary">
              #{item.id} {item.goal}
            </option>
          ))}
        </select>
        
        <div className="flex items-center gap-4">
          {bannedCount > 0 && (
            <span className="text-[11px] text-white/30 hidden md:inline-block tracking-wide">
              已避开 {bannedCount} 条禁区
            </span>
          )}
          <span className="text-[11px] text-white/30 hidden md:inline-block tracking-wide mr-1">
            Enter 发送
          </span>
          <button
            type="submit"
            disabled={asking || rawText.trim() === ""}
            className="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center hover:bg-white/90 disabled:opacity-50 disabled:bg-white/20 disabled:text-white/40 transition-colors shrink-0"
          >
            {asking ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUp className="w-4 h-4" />}
          </button>
        </div>
      </div>
    </form>
  );
}
