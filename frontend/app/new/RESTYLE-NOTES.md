# /new 深色重做记录（2026-09-25）

## 来历

本页由反重力（gemini-3.1-pro-high）在 CLI 会话中重做——会话末尾虽报网络错误中断，但页面改写实际已完成；中断后由主会话接手收尾：修一处运行时隐患、补暗色作用域、通过全部检查。行为逻辑与旧版逐项对照过，一行未改。

## 保留了什么行为

- 创建新计划（createPlan + 成功回执 + 自动切换目标计划）
- 添加三级节点（createNode：任务/阶段/周打卡三种层级、交付物、父阶段、截止日期、台账实时性说明）
- 目标计划下拉切换（getPlan 联动刷新阶段列表）
- 加载失败与操作反馈的展示、防重复提交

## 改了什么

1. 整页套用 cadence-theme 深色语言：顶部「返回工作台」链接、Card/Input/Label/Button/Select 全部来自 components/ui，卡片用 card-border-gradient、按钮用 btn-primary、输入用 input-glow
2. 修 Radix Select 空字符串值隐患：「最新建的计划」项改用哨兵值 `latest` 映射回空串状态
3. 页面根节点挂 `dark` 类——旧外壳的元素级规则（h1/label/button/input 等的海军蓝与白底）在 .dark 子树内一律豁免（old globals.css 已统一加 `:not(.dark *)`），旧页面不受影响

## 已验证

- `npm run lint` 0 错误、`npx tsc --noEmit` 0 错误、`npm run build` 通过
- 浏览器实测：页面渲染、目标计划下拉展开（真实计划列表）、无运行时报错
