import { useRef, useState, KeyboardEvent } from "react";
import { Loader2, ArrowUp, Check, Play, CornerDownRight } from "lucide-react";
import { PlanChatView, ChatMessage } from "@/lib/api";

function ChatMessageView({ item }: { item: ChatMessage }) {
  const isUser = item.role === "user";
  
  if (isUser) {
    return (
      <div className="flex justify-end w-full">
        <div className="max-w-[80%] bg-white rounded-full px-5 py-3 text-[15px] leading-relaxed text-black shadow-[0_4px_24px_rgba(255,255,255,0.07)]">
          {item.content}
        </div>
      </div>
    );
  }

  let data: { questions?: string[]; ready?: boolean; note?: string } | null = null;
  try {
    data = JSON.parse(item.content);
  } catch {
    data = null;
  }

  return (
    <div className="flex flex-col gap-3 w-full max-w-[90%]">
      <div className="flex items-center gap-3 mb-1">
        <div className="w-7 h-7 rounded-full bg-white/[0.08] border border-white/10 flex items-center justify-center shadow-[0_0_16px_rgba(255,255,255,0.12)] backdrop-blur-sm">
          <Play className="w-3.5 h-3.5 text-white/90 ml-0.5" />
        </div>
        <span className="text-[14px] font-semibold text-primary/90 tracking-wide">
          AI 规划助手
        </span>
      </div>

      {data === null || typeof data !== "object" ? (
        <div className="text-[15px] leading-[1.7] text-muted/90 pl-10 space-y-4">
          <p className="text-primary/80 whitespace-pre-wrap">{item.content}</p>
        </div>
      ) : (
        <div className="text-[15px] leading-[1.7] text-muted/90 pl-10 space-y-4">
          {data.note && <p className="text-primary/80">{data.note}</p>}
          
          {(data.questions ?? []).length > 0 && (
            <div className="bg-white/[0.02] border border-white/[0.04] rounded-2xl p-4 mt-2">
              <ul className="space-y-3">
                {(data.questions ?? []).map((q, i) => (
                  <li key={i} className="flex gap-3 text-[14px] text-primary/80">
                    <span className="text-muted/40 mt-1 shrink-0"><CornerDownRight className="w-3.5 h-3.5" /></span>
                    <span>{q}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.ready === true && (
            <div className="flex items-center gap-2 mt-2 py-2 px-3 bg-emerald-500/10 text-emerald-400 rounded-xl w-fit text-[13px] font-medium border border-emerald-500/20">
              <Check className="w-4 h-4" />
              意向信息已充分，可以随时生成阶段蓝图
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ChatBox({
  view,
  effectivePlanId,
  plans,
  chosenPlan,
  setChosenPlan,
  onSend,
  onGenerate,
  busy,
}: {
  view: PlanChatView | null;
  effectivePlanId: number | null;
  plans: { id: number; goal: string }[];
  chosenPlan: string;
  setChosenPlan: (val: string) => void;
  onSend: (msg: string) => Promise<void>;
  onGenerate: () => Promise<void>;
  busy: boolean;
}) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = async () => {
    if (!text.trim() || busy || effectivePlanId === null) return;
    const content = text;
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    await onSend(content);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="mt-4 bg-surface2/60 rounded-3xl border border-white/[0.04] p-2 flex flex-col h-[500px] shadow-sm overflow-hidden relative group/chatbox">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.04] bg-white/[0.01]">
        <div className="flex items-center gap-3">
          <div className="text-[13px] font-medium text-primary/90">意向规划对话</div>
          {view && (
            <div className="text-[11px] text-muted/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04]">
              {view.turns_used}/{view.max_turns} 轮
            </div>
          )}
          {effectivePlanId && (
            <div className="text-[11px] text-muted/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04]">
              归属计划 #{effectivePlanId}
            </div>
          )}
        </div>
        
        {view?.blueprint != null && (
          <a href="/proposals" className="text-[12px] font-medium text-amber-500 hover:text-amber-400 bg-amber-500/10 px-3 py-1.5 rounded-full transition-colors flex items-center gap-1.5">
            待批蓝图 #{view.blueprint.id}
            <ArrowUp className="w-3.5 h-3.5 rotate-45" />
          </a>
        )}
      </div>

      {/* Warning if no plan chosen yet for new direction */}
      {view !== null && effectivePlanId === null && (
        <div className="m-4 p-4 rounded-2xl bg-amber-500/5 border border-amber-500/10 flex flex-col gap-3">
          <div className="text-[13px] text-amber-500/90 font-medium">
            该候选为「新方向」，请先指定注入的计划：
          </div>
          <select
            value={chosenPlan}
            onChange={(e) => setChosenPlan(e.target.value)}
            className="w-full max-w-sm appearance-none bg-black/40 border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-primary/90 hover:border-white/[0.15] outline-none cursor-pointer"
          >
            <option value="">请选择目标计划…</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>
                #{item.id} {item.goal}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 scrollbar-hide">
        {view !== null && view.steps.length > 0 && (
          <div className="bg-white/[0.02] border border-white/[0.04] rounded-2xl p-5 mb-8">
            <div className="text-[13px] font-semibold text-primary/80 mb-1">
              这一步的底稿：它当时给的先后步骤
            </div>
            <div className="text-[12px] text-muted/50 mb-4">
              （草案，不是成品——要在哪儿加、合并、砍掉，直接跟它说）
            </div>
            <ul className="space-y-3 pl-2 border-l-2 border-white/[0.06]">
              {view.steps.map((step, index) => (
                <li key={index} className="pl-4 text-[13px] text-primary/90 leading-relaxed">
                  <span className="font-medium mr-2">{step.title}</span>
                  {step.deliverable && (
                    <span className="text-muted/60">｜ 要交：{step.deliverable}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {view !== null && view.messages.length === 0 && (
          <div className="h-full flex items-center justify-center text-center px-4">
            <p className="text-[13px] text-muted/40 max-w-sm leading-relaxed">
              先聊清楚你的时间投入、首选切入点与验收目标，聊透后再出蓝图。
            </p>
          </div>
        )}

        {(view?.messages ?? []).map((item, index) => (
          <ChatMessageView key={index} item={item} />
        ))}
        
        {/* Extra spacing at bottom for input area */}
        <div className="h-10"></div>
      </div>

      {/* Input Area */}
      <div className="absolute bottom-4 left-4 right-4 bg-[#121214]/90 backdrop-blur-md rounded-2xl border border-white/[0.06] p-1.5 flex items-end shadow-[0_8px_32px_rgba(0,0,0,0.4)] group-focus-within/chatbox:border-white/[0.15] transition-colors z-10">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            e.target.style.height = "";
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          onKeyDown={handleKeyDown}
          disabled={busy || effectivePlanId === null}
          className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[14px] text-primary placeholder-muted/30 py-2.5 px-3 max-h-[160px] min-h-[44px] overflow-hidden leading-relaxed disabled:opacity-50"
          rows={1}
          placeholder="补充要求，例如：每周 5 小时..."
        />
        
        <div className="flex items-center gap-1.5 pl-2">
          {view?.can_generate && (
            <button
              onClick={onGenerate}
              disabled={busy || effectivePlanId === null}
              className="px-4 py-2.5 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 rounded-xl transition-colors text-[13px] font-medium shrink-0 disabled:opacity-50 disabled:bg-transparent"
              title="意向达成，生成蓝图"
            >
              生成蓝图
            </button>
          )}
          <button
            onClick={handleSend}
            disabled={!text.trim() || busy || effectivePlanId === null}
            className="p-2.5 bg-white text-black rounded-xl hover:bg-gray-200 transition-colors shrink-0 shadow-sm disabled:opacity-50 disabled:bg-white/10 disabled:text-white/40"
          >
            {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <ArrowUp className="w-5 h-5" />}
          </button>
        </div>
      </div>
    </div>
  );
}
