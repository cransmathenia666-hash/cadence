import { FormEvent, KeyboardEvent } from "react";
import { Loader2, ArrowUp } from "lucide-react";
import { HoverSelect } from "@/components/ui/hover-select";

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
        aria-label="描述你的处境与困惑"
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
      
      <div className="flex items-center justify-between gap-3 mt-2 px-1">
        <HoverSelect
          value={planChoice}
          onChange={setPlanChoice}
          disabled={asking}
          placeholder="新方向 (不从属现有计划)"
          className="min-w-[220px]"
          options={[
            { value: "", label: "新方向 (不从属现有计划)" },
            ...plans.map((item) => ({ value: String(item.id), label: `#${item.id} ${item.goal}` })),
          ]}
        />
        
        <div className="flex items-center gap-4">
          {bannedCount > 0 && (
            <span className="text-[11px] text-white/50 hidden md:inline-block tracking-wide">
              已避开 {bannedCount} 条禁区
            </span>
          )}
          <button
            type="submit"
            aria-label="开始找候选"
            disabled={asking || rawText.trim() === ""}
            className="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center hover:bg-white/90 disabled:opacity-50 disabled:bg-white/20 disabled:text-white/50 transition-colors shrink-0"
          >
            {asking ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUp className="w-4 h-4" />}
          </button>
        </div>
      </div>
    </form>
  );
}
