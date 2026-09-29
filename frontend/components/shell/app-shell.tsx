"use client";

import type { ReactNode } from "react";
import { useCallback, useState } from "react";
import { useWorkspace } from "./workspace-context";
import { Sidebar } from "@/components/workbench/sidebar";
import { Topbar } from "@/components/workbench/topbar";

// 应用外壳：顶栏 + 侧栏常驻，children 是各页面在主区的内容。
export function AppShell({ children }: { children: ReactNode }) {
  const { plans, selectedPlanId, setSelectedPlanId } = useWorkspace();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const selectedPlan = plans.find((p) => p.id === selectedPlanId);

  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const selectPlan = (id: number) => {
    setSelectedPlanId(id);
    closeSidebar();
  };

  return (
    // 旧工作台页根节点的布局与作用域类迁移至此：dark 兼作旧元素规则的豁免标记
    <div className="dark flex h-screen w-full overflow-hidden selection:bg-white/20">
      <Topbar
        planName={selectedPlan?.goal}
        onMenuOpen={() => setSidebarOpen(true)}
      />
      <Sidebar
        plans={plans}
        selectedPlanId={selectedPlanId}
        onSelect={selectPlan}
        mobileOpen={sidebarOpen}
        onMobileClose={closeSidebar}
      />
      <main className="relative flex h-full min-w-0 flex-1 flex-col overflow-y-auto bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background">
        {children}
      </main>
    </div>
  );
}
