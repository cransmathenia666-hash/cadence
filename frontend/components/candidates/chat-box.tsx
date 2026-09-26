import { useRef, useState, KeyboardEvent, FormEvent } from "react";
import { Loader2, ArrowUp, Check, Play, CornerDownRight } from "lucide-react";
import { PlanChatView, ChatMessage, FindResult } from "@/lib/api";
import { HoverSelect } from "@/components/ui/hover-select";

function ChatMessageView({ item }: { item: ChatMessage }) {
  const isUser = item.role === "user";
  
  if (isUser) {
    return (
      <div className="flex justify-end w-full">
        <div className="max-w-[80%] bg-white rounded-[20px] px-5 py-3 text-[14px] leading-relaxed text-black shadow-sm">
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
    <div className="flex flex-col gap-3 w-full max-w-[90%] bg-surface2/40 border border-white/[0.04] p-5 rounded-[24px]">
      <div className="flex items-center gap-3 mb-1">
        <div className="w-6 h-6 rounded-full bg-white/[0.08] flex items-center justify-center">
          <Play className="w-3 h-3 text-white/90 ml-0.5" />
        </div>
        <span className="text-[13px] font-semibold text-primary/90 tracking-wide">
          AI 规划助手
        </span>
      </div>

      {data === null || typeof data !== "object" ? (
        <div className="text-[14px] leading-relaxed text-white/80 pl-9 space-y-4">
          <p className="whitespace-pre-wrap">{item.content}</p>
        </div>
      ) : (
        <div className="text-[14px] leading-relaxed text-white/80 pl-9 space-y-4">
          {data.note && <p>{data.note}</p>}
          
          {(data.questions ?? []).length > 0 && (
            <div className="bg-white/[0.02] border border-white/[0.04] rounded-xl p-4 mt-2">
              <ul className="space-y-3">
                {(data.questions ?? []).map((q, i) => (
                  <li key={i} className="flex gap-3 text-[13px] text-white/70">
                    <span className="text-white/30 mt-0.5 shrink-0"><CornerDownRight className="w-3.5 h-3.5" /></span>
                    <span>{q}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.ready === true && (
            <div className="flex items-center gap-2 mt-2 py-2 px-3 bg-emerald-500/10 text-emerald-400 rounded-lg w-fit text-[12px] font-medium border border-emerald-500/20">
              <Check className="w-3.5 h-3.5" />
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
  clarifyData,
  onClarifySubmit,
  clarifyAnswer,
  setClarifyAnswer,
}: {
  view: PlanChatView | null;
  effectivePlanId: number | null;
  plans: { id: number; goal: string }[];
  chosenPlan: string;
  setChosenPlan: (val: string) => void;
  onSend: (msg: string) => Promise<void>;
  onGenerate: () => Promise<void>;
  busy: boolean;
  clarifyData?: FindResult["clarify"] | null;
  onClarifySubmit?: (e: FormEvent) => void;
  clarifyAnswer?: string;
  setClarifyAnswer?: (val: string) => void;
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

  const isClarifyMode = !view && !!clarifyData;

  if (!view && !clarifyData) {
    return (
      <div className="mt-0 bg-surface2/60 rounded-[32px] border border-white/[0.04] flex flex-col h-full shadow-sm overflow-hidden relative">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.04] bg-white/[0.01]">
          <div className="text-[13px] font-medium text-primary/90">意向规划对话</div>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
          <div className="text-[14px] text-white/[0.35] font-medium tracking-wide mb-5 max-w-[200px] leading-relaxed">
            采纳候选后，在此进一步沟通细节并生成阶段蓝图。
          </div>
          <button disabled className="px-6 py-2.5 border border-white/[0.08] text-white/30 rounded-full text-[13px] cursor-not-allowed bg-white/[0.01]">
            等待采纳候选
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-0 bg-[#141416] rounded-[32px] border border-white/[0.04] flex flex-col h-full shadow-lg overflow-hidden relative group/chatbox">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.04] bg-surface2/30">
        <div className="flex items-center gap-3">
          <div className="text-[13px] font-medium text-primary/90">意向规划对话</div>
          {view && (
            <div className="text-[11px] text-white/40 font-medium px-2 py-0.5 rounded-full bg-white/[0.04]">
              {view.turns_used}/{view.max_turns} 轮
            </div>
          )}
          {effectivePlanId && (
            <div className="text-[11px] text-white/40 font-medium px-2 py-0.5 rounded-full bg-white/[0.04]">
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

      {view !== null && effectivePlanId === null && (
        <div className="m-4 p-4 rounded-2xl bg-amber-500/5 border border-amber-500/10 flex flex-col gap-3">
          <div className="text-[13px] text-amber-500/90 font-medium">
            该候选为「新方向」，请先指定注入的计划：
          </div>
          <HoverSelect
            value={chosenPlan}
            onChange={setChosenPlan}
            placeholder="请选择目标计划…"
            className="max-w-sm w-full"
            options={[
              { value: "", label: "请选择目标计划…" },
              ...plans.map((item) => ({ value: String(item.id), label: `#${item.id} ${item.goal}` })),
            ]}
          />
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 scrollbar-hide pb-28">
        
        {isClarifyMode && clarifyData && (
          <div className="flex flex-col gap-3 w-full max-w-[90%] bg-[#1c140a] border border-amber-500/20 p-5 rounded-[24px]">
            <div className="flex items-center gap-3 mb-1">
              <div className="w-6 h-6 rounded-full bg-amber-500/10 flex items-center justify-center">
                <Play className="w-3 h-3 text-amber-500 ml-0.5" />
              </div>
              <span className="text-[13px] font-semibold text-amber-500/90 tracking-wide">
                AI 规划助手
              </span>
            </div>
            
            <div className="pl-9">
              <div className="text-[11px] font-semibold text-amber-500/50 uppercase tracking-widest mb-2 border border-amber-500/20 w-fit px-2 py-0.5 rounded">追问</div>
              <div className="text-[14px] text-amber-400/90 leading-relaxed mb-5">
                {clarifyData.question}
              </div>
              <form onSubmit={onClarifySubmit} className="flex flex-col gap-3">
                <input 
                  value={clarifyAnswer} 
                  onChange={e => setClarifyAnswer?.(e.target.value)} 
                  placeholder="一句话回答..." 
                  className="w-full bg-black/40 border border-amber-500/20 rounded-xl px-4 py-2.5 text-[13px] text-primary outline-none focus:border-amber-500/40 transition-colors" 
                  disabled={busy}
                />
                <button 
                  type="submit" 
                  disabled={busy || !clarifyAnswer?.trim()} 
                  className="w-fit px-5 py-2 bg-amber-500/20 text-amber-500 hover:bg-amber-500/30 rounded-xl text-[12px] font-medium transition-colors disabled:opacity-50"
                >
                  带着回答再问一轮
                </button>
              </form>
            </div>
          </div>
        )}

        {view !== null && view.steps.length > 0 && (
          <div className="bg-white/[0.02] border border-white/[0.04] rounded-2xl p-5 mb-8">
            <div className="text-[13px] font-semibold text-primary/80 mb-1">
              这一步的底稿：它当时给的先后步骤
            </div>
            <div className="text-[12px] text-white/40 mb-4">
              （草案，不是成品——要在哪儿加、合并、砍掉，直接跟它说）
            </div>
            <ul className="space-y-3 pl-2 border-l-2 border-white/[0.06]">
              {view.steps.map((step, index) => (
                <li key={index} className="pl-4 text-[13px] text-primary/90 leading-relaxed">
                  <span className="font-medium mr-2">{step.title}</span>
                  {step.deliverable && (
                    <span className="text-white/40">｜ 要交：{step.deliverable}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {view !== null && view.messages.length === 0 && (
          <div className="flex items-center justify-center text-center mt-12 px-4">
            <p className="text-[13px] text-white/40 max-w-sm leading-relaxed">
              先聊清楚你的时间投入、首选切入点与验收目标，聊透后再出蓝图。
            </p>
          </div>
        )}

        {(view?.messages ?? []).map((item, index) => (
          <ChatMessageView key={index} item={item} />
        ))}
        
      </div>

      {/* Input Area */}
      {!isClarifyMode && (
        <div className="absolute bottom-4 left-4 right-4 bg-surface2/90 backdrop-blur-md rounded-2xl border border-white/[0.04] p-1.5 flex items-end shadow-lg group-focus-within/chatbox:border-white/[0.15] transition-colors z-10">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${e.target.scrollHeight}px`;
            }}
            onKeyDown={handleKeyDown}
            disabled={busy || effectivePlanId === null}
            className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[14px] text-primary placeholder-white/30 py-2.5 px-3 max-h-[160px] min-h-[44px] overflow-hidden leading-relaxed disabled:opacity-50"
            rows={1}
            placeholder="补充要求，例如：每周 5 小时..."
          />
          
          <div className="flex items-center gap-2 pl-2 shrink-0">
            {view?.can_generate && (
              <button
                onClick={onGenerate}
                disabled={busy || effectivePlanId === null}
                className="px-4 py-2.5 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 rounded-xl transition-colors text-[13px] font-medium disabled:opacity-50 disabled:bg-transparent"
                title="意向达成，生成蓝图"
              >
                生成蓝图
              </button>
            )}
            <button
              onClick={handleSend}
              disabled={!text.trim() || busy || effectivePlanId === null}
              className="w-10 h-10 bg-white text-black rounded-xl hover:bg-gray-200 transition-colors flex items-center justify-center disabled:opacity-50 disabled:bg-white/10 disabled:text-white/40"
            >
              {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <ArrowUp className="w-5 h-5" />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
