"use client";

import { PlanSummary } from "@/lib/api";
import {
  CheckCircle2,
  Lightbulb,
  LayoutDashboard,
  Compass,
  Inbox,
  LineChart,
  BrainCircuit,
  UserCircle,
  Settings,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

const NAV_ITEMS = [
  { href: "/workbench", label: "工作台", icon: LayoutDashboard },
  { href: "/candidates", label: "找方向", icon: Compass },
  { href: "/proposals", label: "提案", icon: Inbox },
  { href: "/judge", label: "判资料", icon: Lightbulb },
  { href: "/report", label: "报告", icon: LineChart },
  { href: "/memory", label: "记忆", icon: BrainCircuit },
  { href: "/profile", label: "档案", icon: UserCircle },
  { href: "/providers", label: "设置", icon: Settings },
];

type SidebarProps = {
  plans: PlanSummary[];
  selectedPlanId: number | null;
  onSelect: (id: number) => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
};

export function Sidebar({
  plans,
  selectedPlanId,
  onSelect,
  mobileOpen = false,
  onMobileClose,
}: SidebarProps) {
  const pathname = usePathname();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion() ?? false;

  useEffect(() => {
    if (!mobileOpen) return;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMobileClose?.();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen, onMobileClose]);

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  const renderContent = (mobile = false) => (
    <>
      {mobile && (
        <div className="flex h-14 items-center justify-between border-b border-white/[0.06] px-5">
          <span className="text-sm font-semibold tracking-wide text-primary">导航</span>
          <button
            ref={closeButtonRef}
            type="button"
            aria-label="关闭导航菜单"
            onClick={onMobileClose}
            className="inline-flex size-9 items-center justify-center rounded-[var(--radius-control)] text-muted/75 transition-colors hover:bg-white/[0.06] hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
      )}
      <div className="mb-8 space-y-0.5 px-3">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || (pathname === "/" && item.href === "/workbench");
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href === "/report" && selectedPlanId !== null ? `/report?plan_id=${selectedPlanId}` : item.href}
              onClick={mobile ? onMobileClose : undefined}
              className={`group flex items-center gap-3 rounded-[var(--radius-control)] px-3 py-2 text-[14px] font-medium transition-colors ${
                isActive
                  ? "bg-white/[0.05] text-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]"
                  : "text-muted/60 hover:bg-white/[0.02] hover:text-muted/90"
              }`}
            >
              <Icon className={`size-4 transition-colors ${isActive ? "text-primary/90" : "opacity-50 group-hover:opacity-80"}`} />
              {item.label}
            </Link>
          );
        })}
      </div>

      <div className="mb-4 flex items-center justify-between px-5">
        <h2 className="text-[12px] font-semibold uppercase tracking-widest text-muted/75">Plans</h2>
        <Link
          href="/candidates"
          onClick={mobile ? onMobileClose : undefined}
          className="flex cursor-pointer items-center gap-1 text-[11px] text-muted/60 transition-colors hover:text-white/90"
        >
          <Lightbulb className="size-3.5" />
          提出想法
        </Link>
      </div>

      <Link href="/new" onClick={mobile ? onMobileClose : undefined} className="mb-3 px-5 text-[11px] text-muted/60 hover:text-primary">
        高级手工新建
      </Link>
      <div className="scrollbar-hide flex-1 space-y-1 overflow-y-auto px-3">
        {plans.map((plan) => {
          const isSelected = plan.id === selectedPlanId;
          const isDone = plan.status === "closed";
          const isVoid = plan.status === "void";
          const isActive = plan.status === "active";
          const isWeak = isDone || isVoid;
          const progress = plan.stages > 0 ? Math.round((plan.stages_finished / plan.stages) * 100) : 0;

          if (isSelected) {
            return (
              <button
                type="button"
                key={plan.id}
                onClick={() => onSelect(plan.id)}
                aria-current="true"
                className="group relative w-full cursor-pointer rounded-[var(--radius-card)] border border-white/[0.06] bg-white/[0.03] p-3.5 text-left shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="absolute bottom-3.5 left-0 top-3.5 w-1 rounded-r-full bg-white shadow-[0_0_12px_rgba(255,255,255,0.9)]" />
                <h3 className="mb-2.5 pr-1 text-[13px] font-medium leading-snug text-primary">{plan.goal}</h3>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-medium text-muted/80">{isActive ? "进行中" : plan.status}</span>
                  <div className="h-2 w-20 overflow-hidden rounded-full border border-white/[0.04] bg-black/50">
                    <div className="h-full rounded-full bg-white/80" style={{ width: `${progress}%` }} />
                  </div>
                </div>
              </button>
            );
          }

          return (
            <button
              type="button"
              key={plan.id}
              onClick={() => onSelect(plan.id)}
              className="group relative block w-full cursor-pointer rounded-[var(--radius-card)] border border-transparent p-3.5 text-left outline-none transition-colors hover:bg-white/[0.02] focus-visible:ring-2 focus-visible:ring-ring"
            >
              <h3 className={`line-clamp-2 text-[13px] font-medium leading-snug transition-colors ${isWeak ? "text-muted/65 group-hover:text-muted/70" : "text-muted/60 group-hover:text-muted/90"}`}>
                {plan.goal}
              </h3>
              <div className="mt-2 flex items-center justify-between opacity-40 transition-opacity group-hover:opacity-60">
                <span className="text-[11px]">{isActive ? "进行中" : plan.status}</span>
                {isDone && <CheckCircle2 className="size-3.5" />}
              </div>
            </button>
          );
        })}
      </div>
    </>
  );

  return (
    <>
      <aside className="z-20 hidden h-full w-[260px] shrink-0 flex-col border-r border-white/[0.06] bg-surface-raised pb-6 pt-20 md:flex">
        {renderContent()}
      </aside>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            className="fixed inset-0 z-40 md:hidden"
            initial={reduce ? { opacity: 1 } : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={reduce ? { opacity: 1 } : { opacity: 0 }}
            transition={{ duration: reduce ? 0 : 0.2 }}
          >
            <button
              type="button"
              aria-label="关闭导航菜单"
              onClick={onMobileClose}
              className="absolute inset-0 h-full w-full bg-black/60"
            />
            <motion.aside
              id="mobile-sidebar"
              aria-label="移动端导航"
              initial={reduce ? { x: 0 } : { x: "-100%" }}
              animate={{ x: 0 }}
              exit={reduce ? { x: 0 } : { x: "-100%" }}
              transition={{ duration: reduce ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}
              className="relative flex h-full w-[min(86vw,320px)] flex-col border-r border-white/[0.08] bg-surface-overlay pb-6 pt-0 shadow-lg"
            >
              {renderContent(true)}
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
