import type { ReactNode } from "react";

// 应用路由组只负责组织路由；共享外壳统一由根布局提供。
export default function AppLayout({ children }: { children: ReactNode }) {
  return children;
}
