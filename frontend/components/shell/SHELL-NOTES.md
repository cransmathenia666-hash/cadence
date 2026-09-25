# Shell Notes

## 改了什么文件
- `components/workbench/sidebar.tsx`: 增加了侧栏的页面导航区，包含 7 个页面的入口；使用 `usePathname` 实现 active 状态高亮；调整了导航区的视觉层级，连接了 `+ 新建` 按钮到 `/new`。
- `components/workbench/topbar.tsx`: 引入了基于 `usePathname` 的顶栏标题逻辑，在 `/workbench` 或 `/` 路由下显示「cadence / 当前计划名」，在其他页面显示「cadence / 页面名」。

## 导航映射表
- `/workbench` -> 工作台
- `/candidates` -> 找方向
- `/proposals` -> 提案
- `/report` -> 报告
- `/memory` -> 记忆
- `/profile` -> 档案
- `/providers` -> 设置

## 遗留 TODO
- `components/shell/app-shell.tsx` 中的 `loadError` 变量目前未使用（抛出 ESLint 警告），未来可以在顶层（App Shell）提供全局错误 Toast 提示。
- 其他旧页面（如 `/candidates`, `/proposals` 等）目前仍是旧版界面，未完全重构成新设计语言，点击过去会看到旧样子，需在未来各页重做轮次中逐步替换。
