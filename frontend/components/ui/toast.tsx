"use client";

import { useEffect } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { ArrowUpRight, Check, X } from "lucide-react";
import { EASE_OUT } from "@/lib/ease";

/**
 * 轻量操作回执 Toast——右下角短暂出现，约 6 秒自动消失，可手动关。
 * 只装成功回执；失败提示必须留在页面里（见提案页的错误条），不许被动画带走。
 * 不引第三方库，动效走现有 motion + ease 令牌。
 */

export type ToastData = {
  id: number;
  text: string;
  /** 可选跳转：回执带后续动作时（如「去裁定」）直接给出入口，点了顺带收掉这条。 */
  href?: string;
  hrefLabel?: string;
};

export const TOAST_DURATION_MS = 6000;

/** 单条 Toast：挂载即起计时，到点回调 onExpire；hover 不暂停（回执短平快，不值得多一份状态）。 */
function ToastEntry({
  toast,
  onDismiss,
  onExpire,
}: {
  toast: ToastData;
  onDismiss: (id: number) => void;
  onExpire: (id: number) => void;
}) {
  const reduce = useReducedMotion();

  useEffect(() => {
    const timer = window.setTimeout(() => onExpire(toast.id), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast.id, onExpire]);

  return (
    <motion.div
      layout
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: 32, scale: 0.97 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
      exit={
        reduce
          ? { opacity: 0, transition: { duration: 0.16 } }
          : { opacity: 0, x: 24, scale: 0.97, transition: { duration: 0.16, ease: EASE_OUT } }
      }
      transition={reduce ? { duration: 0.16 } : { duration: 0.28, ease: EASE_OUT }}
      role="status"
      className="pointer-events-auto flex w-[360px] max-w-[calc(100vw-2.5rem)] items-start gap-2.5 rounded-xl border border-white/[0.08] bg-[#141416] p-3.5 shadow-[0_8px_32px_rgba(0,0,0,0.45)]"
    >
      <span className="mt-0.5 flex w-4 h-4 shrink-0 items-center justify-center rounded-full bg-green/15">
        <Check className="w-2.5 h-2.5 text-green stroke-[3]" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] leading-relaxed text-white/80">{toast.text}</p>
        {toast.href ? (
          <Link
            href={toast.href}
            onClick={() => onDismiss(toast.id)}
            className="mt-0.5 inline-flex items-center gap-1 text-[12px] font-medium text-primary/85 underline-offset-2 transition-colors hover:text-primary hover:underline"
          >
            {toast.hrefLabel ?? "去查看"}
            <ArrowUpRight className="w-3 h-3" />
          </Link>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="关闭这条回执"
        className="shrink-0 rounded-full p-1 text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white/80 focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/40"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </motion.div>
  );
}

/**
 * 堆叠容器：右下角固定，进出统一走 AnimatePresence；reduced-motion 时退化为纯淡入淡出。
 * onDismiss / onExpire 期望是稳定引用（页面里用 useCallback 包 functional setState）。
 */
export function ToastStack({
  toasts,
  onDismiss,
}: {
  toasts: ToastData[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      aria-live="polite"
      aria-label="操作回执"
      className="pointer-events-none fixed bottom-6 right-6 z-50 flex flex-col items-end gap-2"
    >
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <ToastEntry
            key={toast.id}
            toast={toast}
            onDismiss={onDismiss}
            onExpire={onDismiss}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
