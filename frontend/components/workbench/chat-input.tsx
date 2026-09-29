"use client";
// 形态参考 ui.halaska.com 的 Prompt input：输入区在上、工具栏行在下、键盘提示在框外下方。
// 文案与发送都归上层管（问答卡的回答走同一条 send 路）。
import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { SPRING_SWAP } from "@/lib/ease";
import { cn } from "@/lib/utils";

export function ChatInput({
  value,
  onChange,
  onSend,
  sending,
  toolbar,
  note,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string) => void;
  sending: boolean;
  /** 工具栏行左侧插槽（提炼档案提案入口）。 */
  toolbar?: ReactNode;
  /** 输入框上方的回执行（提炼结果、错误等）。 */
  note?: ReactNode;
}) {
  const reduce = useReducedMotion() ?? false;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    if (!value.trim() || sending) return;
    onSend(value);
    if (textareaRef.current) {
      textareaRef.current.style.height = "";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="absolute bottom-0 left-0 right-0 z-20 bg-gradient-to-t from-background via-background/95 to-transparent pt-12 pb-3 px-4 md:px-12 pointer-events-none">
      <div className="w-full pointer-events-auto">
        {note ? <div className="mb-1.5 px-1">{note}</div> : null}

        <div
          className={cn(
            "relative rounded-[20px] border bg-[#0a0a0c]/85 backdrop-blur-md p-2 transition-colors",
            sending
              ? "border-white/10"
              : "border-white/[0.06] focus-within:border-white/15",
          )}
        >
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
            className="block w-full resize-none border-none bg-transparent px-2 py-1.5 text-[14px] leading-6 text-primary placeholder-muted/35 outline-none focus:ring-0 max-h-[160px] overflow-hidden disabled:opacity-50"
            rows={1}
            placeholder={
              sending ? "cadence 正在思考..." : "回复 cadence..."
            }
          />

          <div className="mt-0.5 flex min-h-8 items-center gap-1">
            {toolbar}
            <button
              type="button"
              onClick={handleSend}
              disabled={!value.trim() || sending}
              aria-label={sending ? "发送中" : "发送"}
              className="ml-auto grid size-9 shrink-0 place-items-center rounded-full bg-white text-black transition-colors hover:bg-white/85 disabled:bg-white/15 disabled:text-white/45 disabled:opacity-100"
            >
              <AnimatePresence initial={false} mode="popLayout">
                <motion.span
                  key={sending ? "busy" : "send"}
                  initial={reduce ? { opacity: 1 } : { opacity: 0, y: 3, scale: 0.8 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={reduce ? { opacity: 0 } : { opacity: 0, y: -3, scale: 0.8 }}
                  transition={reduce ? { duration: 0 } : SPRING_SWAP}
                  className="grid place-items-center"
                >
                  {sending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <ArrowUp className="size-4" />
                  )}
                </motion.span>
              </AnimatePresence>
            </button>
          </div>
        </div>

        <p className="mt-1.5 hidden select-none text-center text-[11px] text-muted/40 sm:block">
          按 Enter 键发送，按 Shift+Enter 键换行
        </p>
      </div>
    </div>
  );
}
