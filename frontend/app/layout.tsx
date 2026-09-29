import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import "./globals.css";
import "./cadence-theme.css";
import { AppShell } from "@/components/shell/app-shell";
import { WorkspaceProvider } from "@/components/shell/workspace-context";

export const metadata: Metadata = {
  title: "Cadence · 学习决策与方向跟进",
  description: "学习决策与方向跟进 Agent",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Suspense fallback={null}>
          <WorkspaceProvider>
            <AppShell>{children}</AppShell>
          </WorkspaceProvider>
        </Suspense>
      </body>
    </html>
  );
}
