import type { ReactNode } from "react";
import "../cadence-theme.css";
import { WorkspaceProvider } from "@/components/shell/workspace-context";
import { AppShell } from "@/components/shell/app-shell";

// 应用外壳布局：进到这里的一切页面都带顶栏、侧栏与共享的计划上下文。
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <WorkspaceProvider>
      <AppShell>{children}</AppShell>
    </WorkspaceProvider>
  );
}
