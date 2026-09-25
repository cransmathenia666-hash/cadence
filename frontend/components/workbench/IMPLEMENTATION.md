# 页面实现简报: /workbench

## 改动文件
1. `app/cadence-theme.css`: 深色主题层，提炼自 round3 视觉语言。
2. `app/workbench/page.tsx`: 页面入口，独立引入了深色主题和深色全屏样式重置。
3. `components/workbench/workbench-view.tsx`: 核心页面视图，管理选中计划状态，发请求拉取对话。
4. `components/workbench/topbar.tsx`: 轻量产品顶栏。
5. `components/workbench/sidebar.tsx`: 左侧计划列表。
6. `components/workbench/chat-flow.tsx`: 渲染历史对话流与助手反馈。
7. `components/workbench/chat-input.tsx`: 对话输入区与发送按钮。

## 数据接点
- 计划列表拉取: `listPlans(true)` (`lib/api.ts`)
- 对话记录拉取: `getPlanDialogue(planId)` (`lib/api.ts`)
- 对话交互: `sayPlanDialogue(planId, text)` (`lib/api.ts`)
- 裁定建议: `decideProposal(proposalId, accept, reason)` (`lib/api.ts`)

## TODO 行为
- 左侧栏中的「新建任务」入口目前是一个简单的链接至 `/new`，具体的点击交互可能需要后续确定并迭代。
- API 的加载/错误态只是做了一句话提示，未进行深度视觉定制。
