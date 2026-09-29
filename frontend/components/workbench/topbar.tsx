"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";

const PAGE_NAMES: Record<string, string> = {
  "/workbench": "工作台",
  "/candidates": "找方向",
  "/proposals": "提案",
  "/judge": "判资料",
  "/report": "报告",
  "/memory": "记忆",
  "/profile": "档案",
  "/providers": "设置",
  "/new": "新建",
};

export function Topbar({
  planName,
  onMenuOpen,
}: {
  planName?: string;
  onMenuOpen?: () => void;
}) {
  const pathname = usePathname();
  const isWorkbench = pathname === "/workbench" || pathname === "/";
  const pageName = PAGE_NAMES[pathname];
  const displayTitle = isWorkbench ? planName : pageName;

  return (
    <header className="glass-header absolute inset-x-0 top-0 z-30 flex h-14 items-center justify-between px-4 md:px-6 pointer-events-none">
      <div className="flex min-w-0 items-center gap-2 text-sm font-medium pointer-events-auto md:gap-3">
        {onMenuOpen && (
          <button
            type="button"
            aria-label="打开导航菜单"
            aria-controls="mobile-sidebar"
            onClick={onMenuOpen}
            className="mr-1 inline-flex size-9 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-muted/80 transition-colors hover:bg-white/[0.06] hover:text-primary focus-visible:ring-2 focus-visible:ring-ring md:hidden"
          >
            <Menu className="size-4" aria-hidden="true" />
          </button>
        )}
        <span
          className="shrink-0 text-lg font-semibold tracking-wide text-white opacity-90"
          style={{ fontFamily: "serif", letterSpacing: "-0.02em" }}
        >
          cadence
        </span>
        {displayTitle && (
          <>
            <span aria-hidden="true" className="mx-1 text-white/50">
              /
            </span>
            <span className="truncate text-muted/80">{displayTitle}</span>
          </>
        )}
        <span
          aria-hidden="true"
          className="mt-1 size-1.5 shrink-0 rounded-full bg-white/25"
        />
      </div>

      {planName && !isWorkbench && (
        <div className="ml-3 flex min-w-0 max-w-[38vw] items-center gap-2 text-[11px] text-muted/55 sm:ml-4 sm:max-w-[42vw]">
          <span className="hidden shrink-0 uppercase tracking-[0.16em] sm:inline">当前计划</span>
          <span aria-hidden="true" className="hidden size-1 shrink-0 rounded-full bg-white/25 sm:block" />
          <span className="truncate text-muted/75">{planName}</span>
        </div>
      )}
    </header>
  );
}
