"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "计划表" },
  { href: "/candidates", label: "候选与规划" },
  { href: "/judge", label: "判资料" },
  { href: "/proposals", label: "待裁定" },
  { href: "/report", label: "提交报告" },
  { href: "/new", label: "新建" },
  { href: "/profile", label: "长期档案" },
  { href: "/providers", label: "模型设置" },
];

export function AppHeader() {
  const pathname = usePathname();

  return (
    <header className="app-header">
      <div className="header-container">
        <Link href="/" className="header-brand">
          <span>Cadence</span>
          <span className="brand-badge">方向跟随</span>
        </Link>
        <nav>
          <ul className="header-nav">
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : pathname?.startsWith(item.href);
              return (
                <li key={item.href}>
                  <Link href={item.href} className={active ? "active" : ""}>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
