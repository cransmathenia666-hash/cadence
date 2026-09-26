"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";

// beUI Popover 式选择器：悬停即开（带 140ms 宽限窗防误关），面板上浮淡入，
// 选中项用 beUI Tabs 式的胶囊高亮。替代原生 select（浏览器默认下拉太粗糙）。
export function HoverSelect({
  value,
  onChange,
  options,
  disabled,
  placeholder = "请选择...",
  className = "",
}: {
  value: string;
  onChange: (val: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const openNow = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    // 翻转检测：下方空间放不下面板时向上弹（靠近视口底部的行）
    if (wrapRef.current) {
      const r = wrapRef.current.getBoundingClientRect();
      const below = window.innerHeight - r.bottom;
      const estimated = Math.min(320, options.length * 42 + 20);
      setUp(below < estimated + 12 && r.top > below);
    }
    setOpen(true);
  };
  const scheduleClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), 140);
  };
  const toggle = () => (open ? setOpen(false) : openNow());

  const current = options.find((o) => o.value === value);

  return (
    <div
      ref={wrapRef}
      className={`relative ${className}`}
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        className="w-full flex items-center justify-between gap-2 bg-white/[0.04] hover:bg-white/[0.08] text-white/80 text-[12px] px-4 py-1.5 rounded-full border border-white/[0.06] hover:border-white/[0.16] outline-none transition-all duration-200 disabled:opacity-50"
      >
        <span className="truncate">{current ? current.label : placeholder}</span>
        <ChevronDown
          className={`w-3.5 h-3.5 shrink-0 text-white/40 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: up ? 6 : -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: up ? 6 : -6, scale: 0.97 }}
            transition={{ type: "spring", stiffness: 500, damping: 34 }}
            className={`absolute left-0 min-w-full w-max max-w-[420px] z-50 bg-[#141416] border border-white/[0.08] rounded-2xl p-1.5 shadow-[0_16px_48px_rgba(0,0,0,0.6)] ${up ? "bottom-full mb-2" : "top-full mt-2"}`}
            onClick={(e) => e.stopPropagation()}
          >
            {options.map((opt) => {
              const active = opt.value === value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    onChange(opt.value);
                    setOpen(false);
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-left text-[13px] transition-colors ${
                    active
                      ? "bg-white/[0.1] text-white"
                      : "text-white/60 hover:bg-white/[0.06] hover:text-white/90"
                  }`}
                >
                  {active && <Check className="w-3.5 h-3.5 shrink-0 text-white/80" />}
                  <span className="truncate">{opt.label}</span>
                </button>
              );
            })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
