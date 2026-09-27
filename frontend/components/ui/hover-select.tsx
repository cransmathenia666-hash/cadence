"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";

// 选项超过这个数、且调用方开了 searchable 时，面板顶部出现过滤输入框。
// 问答卡等少量选项的场景保持原样（Combobox 只用于长清单，见分阶段方案阶段 4）。
const SEARCHABLE_MIN_OPTIONS = 8;

// 面板的定位/尺寸上限（与翻转检测的估算一致）。
const PANEL_MAX_W = 420;
const PANEL_MAX_H = 320;

// beUI Popover 式选择器：悬停即开（带 140ms 宽限窗防误关），面板上浮淡入，
// 选中项用 beUI Tabs 式的胶囊高亮。替代原生 select（浏览器默认下拉太粗糙）。
// 面板走 portal + fixed 定位：卡片、抽屉等 overflow 容器再也裁不到它。
export function HoverSelect({
  value,
  onChange,
  options,
  disabled,
  placeholder = "请选择...",
  className = "",
  searchable = false,
}: {
  value: string;
  onChange: (val: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  /** 长清单开关：选项多于约 8 个时面板顶部出过滤输入框（输入即过滤，键盘可用）。 */
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [query, setQuery] = useState("");
  const closeTimer = useRef<number | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const showFilter = searchable && options.length > SEARCHABLE_MIN_OPTIONS;
  const normalizedQuery = query.trim().toLowerCase();
  const filtered =
    showFilter && normalizedQuery !== ""
      ? options.filter((o) => o.label.toLowerCase().includes(normalizedQuery))
      : options;

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  // 打开期间页面滚动/缩放就把面板收起——fixed 定位不跟随触发钮，与其漂移不如干脆收起。
  useEffect(() => {
    if (!open) return;
    const closeOnMove = () => close();
    window.addEventListener("scroll", closeOnMove, true);
    window.addEventListener("resize", closeOnMove);
    return () => {
      window.removeEventListener("scroll", closeOnMove, true);
      window.removeEventListener("resize", closeOnMove);
    };
  }, [open, close]);

  const cancelClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const openNow = () => {
    cancelClose();
    // 翻转检测：下方空间放不下面板时向上弹（靠近视口底部的行）
    if (wrapRef.current) {
      const r = wrapRef.current.getBoundingClientRect();
      const below = window.innerHeight - r.bottom;
      const estimated = Math.min(PANEL_MAX_H, options.length * 42 + 20) + (showFilter ? 40 : 0);
      setUp(below < estimated + 12 && r.top > below);
      setRect(r);
    }
    setOpen(true);
  };

  const scheduleClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(close, 140);
  };

  const toggle = () => {
    if (open) {
      close();
      return;
    }
    openNow();
    // 点开（键盘/触控）时直接聚焦过滤框，落到即可输入；悬停误开不打断焦点。
    if (showFilter) {
      window.setTimeout(() => filterRef.current?.focus(), 0);
    }
  };

  const pick = (val: string) => {
    onChange(val);
    close();
  };

  // 过滤框键盘：↓ 跳到首个选项，Enter 选首个匹配，Esc 关闭。
  const focusFirstOption = () => {
    panelRef.current
      ?.querySelector<HTMLButtonElement>("[data-hover-option]")
      ?.focus();
  };
  const handleFilterKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusFirstOption();
    } else if (e.key === "Enter") {
      e.preventDefault();
      // 键盘路径收尾把焦点还给触发钮，避免面板卸载后焦点落空；悬停关面板不打断焦点。
      triggerRef.current?.focus();
      if (filtered.length > 0) pick(filtered[0].value);
    } else if (e.key === "Escape") {
      e.preventDefault();
      triggerRef.current?.focus();
      close();
    }
  };

  const current = options.find((o) => o.value === value);

  // fixed 定位坐标：贴住触发钮，右缘贴视口时往回收，避免横向溢出。
  const panelStyle =
    rect === null
      ? undefined
      : up
        ? {
            left: Math.min(rect.left, Math.max(window.innerWidth - PANEL_MAX_W - 12, 8)),
            bottom: window.innerHeight - rect.top + 8,
            minWidth: rect.width,
          }
        : {
            left: Math.min(rect.left, Math.max(window.innerWidth - PANEL_MAX_W - 12, 8)),
            top: rect.bottom + 8,
            minWidth: rect.width,
          };

  return (
    <>
      <div
        ref={wrapRef}
        className={`relative ${className}`}
        onMouseEnter={openNow}
        onMouseLeave={scheduleClose}
      >
        <button
          ref={triggerRef}
          type="button"
          onClick={toggle}
          disabled={disabled}
          className="w-full flex items-center justify-between gap-2 bg-white/[0.04] hover:bg-white/[0.08] text-white/80 text-[12px] px-4 py-1.5 rounded-full border border-white/[0.06] hover:border-white/[0.16] outline-none transition-all duration-200 disabled:opacity-50"
        >
          <span className="truncate">{current ? current.label : placeholder}</span>
          <ChevronDown
            className={`w-3.5 h-3.5 shrink-0 text-white/50 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          />
        </button>
      </div>

      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {open && rect !== null && (
              <motion.div
                ref={panelRef}
                initial={{ opacity: 0, y: up ? 6 : -6, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: up ? 6 : -6, scale: 0.97 }}
                transition={{ type: "spring", stiffness: 500, damping: 34 }}
                style={panelStyle}
                className="dark fixed z-[80] w-max max-w-[420px] max-h-[320px] overflow-y-auto bg-[#141416] border border-white/[0.08] rounded-2xl p-1.5 shadow-[0_16px_48px_rgba(0,0,0,0.6)] [color-scheme:dark]"
                onClick={(e) => e.stopPropagation()}
                onMouseEnter={cancelClose}
                onMouseLeave={scheduleClose}
              >
                {showFilter && (
                  <input
                    ref={filterRef}
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={handleFilterKeyDown}
                    placeholder="输入关键字筛选…"
                    aria-label="筛选选项"
                    className="w-full bg-black/40 border border-white/[0.08] rounded-xl px-3 py-2 mb-1 text-[12px] text-primary/90 placeholder-white/30 outline-none focus:border-white/[0.2] transition-colors"
                  />
                )}
                {filtered.map((opt) => {
                  const active = opt.value === value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      data-hover-option
                      onClick={() => pick(opt.value)}
                      className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-left text-[13px] transition-colors focus-visible:bg-white/[0.06] focus-visible:text-white/90 outline-none ${
                        active
                          ? "bg-white/[0.1] text-white"
                          : // portal 落在 body 下拿不到主题作用域的按钮底色，必须显式给透明底
                            "bg-transparent text-white/60 hover:bg-white/[0.06] hover:text-white/90"
                      }`}
                    >
                      {active && <Check className="w-3.5 h-3.5 shrink-0 text-white/80" />}
                      <span className="truncate">{opt.label}</span>
                    </button>
                  );
                })}
                {filtered.length === 0 && (
                  <div className="px-3 py-2 text-[12px] text-white/50">没有匹配的选项</div>
                )}
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}
    </>
  );
}
