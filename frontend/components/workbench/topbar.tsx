"use client";

import { usePathname } from "next/navigation";

const PAGE_NAMES: Record<string, string> = {
  "/workbench": "工作台",
  "/candidates": "找方向",
  "/proposals": "提案",
  "/report": "报告",
  "/memory": "记忆",
  "/profile": "档案",
  "/providers": "设置",
  "/new": "新建",
};

export function Topbar({ planName }: { planName?: string }) {
  const pathname = usePathname();
  const isWorkbench = pathname === "/workbench" || pathname === "/";
  
  const displayTitle = isWorkbench ? planName : PAGE_NAMES[pathname];

  return (
    <header className="glass-header absolute top-0 left-0 right-0 h-14 flex items-center px-6 z-30 justify-between pointer-events-none">
      <div className="flex items-center gap-3 text-sm font-medium pointer-events-auto">
        <span
          className="text-white opacity-90 tracking-wide font-semibold text-lg"
          style={{ fontFamily: "serif", letterSpacing: "-0.02em" }}
        >
          cadence
        </span>
        {displayTitle && (
          <>
            <span className="text-white/20 mx-1">/</span>
            <span className="text-muted/80">{displayTitle}</span>
          </>
        )}
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] mt-1 animate-pulse"></span>
      </div>
    </header>
  );
}
