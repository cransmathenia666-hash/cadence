"use client";

import type { ReactNode } from "react";
import { useWorkspace } from "./workspace-context";
import { Sidebar } from "@/components/workbench/sidebar";
import { Topbar } from "@/components/workbench/topbar";

// 应用外壳：顶栏 + 侧栏常驻，children 是各页面在主区的内容。
// 当前阶段侧栏/顶栏沿用 workbench 的实现，视觉由后续反重力轮次统一。
export function AppShell({ children }: { children: ReactNode }) {
  const { plans, selectedPlanId, setSelectedPlanId, loadError } = useWorkspace();
  const selectedPlan = plans.find((p) => p.id === selectedPlanId);

  return (
    // 旧工作台页根节点的布局与作用域类迁移至此：dark 兼作旧元素规则的豁免标记
    <div className="dark flex h-screen w-full overflow-hidden selection:bg-white/20">
      <Topbar planName={selectedPlan?.goal} />
      <Sidebar
        plans={plans}
        selectedPlanId={selectedPlanId}
        onSelect={setSelectedPlanId}
      />
      <main className="flex-1 flex flex-col relative h-full bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background">
        {children}
      </main>
    </div>
  );
}
