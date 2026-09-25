"use client";

// stagger-list — 列表错峰入场（CSS 配方）。样式来自 styles/stagger-list.css。
import * as React from "react";
import { cn } from "@/lib/utils";

export function StaggerList({
  children,
  step = "40ms",
  speed,
  className,
  ...props
}: React.ComponentProps<"ul"> & { step?: string; speed?: "fast" | "slow" }) {
  return (
    <ul
      className={cn("uf-stagger", speed === "fast" && "uf-stagger-fast", speed === "slow" && "uf-stagger-slow", className)}
      style={{ ["--uf-stagger-step" as string]: step }}
      {...props}
    >
      {children}
    </ul>
  );
}