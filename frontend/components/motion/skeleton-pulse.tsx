"use client";

// skeleton-pulse — 骨架屏脉冲（CSS 配方）。样式来自 styles/skeleton-pulse.css。
import * as React from "react";
import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div aria-hidden className={cn("uf-skeleton uf-skeleton-block", className)} {...props} />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="uf-skeleton uf-skeleton-text" style={{ width: i === lines - 1 ? "60%" : "100%" }} />
      ))}
    </div>
  );
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn("space-y-3 rounded-[var(--radius-lg)] border border-border bg-card p-4", className)} aria-hidden>
      <div className="flex items-center gap-3">
        <div className="uf-skeleton uf-skeleton-circle h-8 w-8" />
        <div className="uf-skeleton uf-skeleton-title w-32" />
      </div>
      <SkeletonText lines={2} />
    </div>
  );
}

export function SkeletonList({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-card px-3 py-2.5">
          <div className="uf-skeleton uf-skeleton-circle h-6 w-6" />
          <div className="uf-skeleton uf-skeleton-text flex-1" />
          <div className="uf-skeleton uf-skeleton-text w-12" />
        </div>
      ))}
    </div>
  );
}