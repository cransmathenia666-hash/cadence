"use client";

import { useRef, type KeyboardEvent } from "react";
import { Paperclip, ArrowUp, Loader2 } from "lucide-react";

// 纯输入框：文案与发送都归上层管（问答卡的回答走同一条 send 路）。
export function ChatInput({
  value,
  onChange,
  onSend,
  sending,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string) => void;
  sending: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    if (!value.trim() || sending) return;
    onSend(value);
    if (textareaRef.current) {
      textareaRef.current.style.height = "";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-background via-background/95 to-transparent pt-12 pb-4 px-4 md:px-12 z-20 pointer-events-none">
      <div className="max-w-[56rem] mx-auto pointer-events-auto">
        <div
          className={`relative group input-glow bg-[#0a0a0c]/80 backdrop-blur-md rounded-2xl border ${
            sending ? "border-white/10" : "border-white/[0.06]"
          } flex items-end p-1.5 transition-all`}
        >
          <button
            className="p-2 text-muted/40 hover:text-white/80 transition-colors shrink-0 mb-0.5 rounded-xl disabled:opacity-50"
            disabled={sending}
          >
            <Paperclip className="w-5 h-5" />
          </button>

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              onChange(e.target.value);
              e.target.style.height = "";
              e.target.style.height = `${e.target.scrollHeight}px`;
            }}
            onKeyDown={handleKeyDown}
            disabled={sending}
            className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[14px] text-primary placeholder-muted/30 py-2.5 max-h-[200px] min-h-[44px] overflow-hidden leading-relaxed disabled:opacity-50"
            rows={1}
            placeholder={sending ? "cadence 正在思考..." : "回复 cadence..."}
          ></textarea>

          <button
            onClick={handleSend}
            disabled={!value.trim() || sending}
            className="p-2 bg-white text-black rounded-xl hover:bg-gray-200 transition-colors shrink-0 mb-0.5 shadow-sm disabled:opacity-50 disabled:bg-gray-700 disabled:text-gray-400"
          >
            {sending ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <ArrowUp className="w-5 h-5" />
            )}
          </button>
        </div>
        <div className="text-center mt-2.5">
          <p className="text-[11px] text-muted/30 font-medium tracking-wide">
            cadence 可能会在执行前要求确认。Shift + Enter 换行
          </p>
        </div>
      </div>
    </div>
  );
}
