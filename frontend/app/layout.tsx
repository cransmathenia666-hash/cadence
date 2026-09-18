import type { Metadata } from "next";
import "./globals.css";
import { AppHeader } from "@/components/app-header";

export const metadata: Metadata = {
  title: "Cadence · 学习决策与方向跟进",
  description: "学习决策与方向跟进 Agent",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN">
      <body>
        <AppHeader />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
