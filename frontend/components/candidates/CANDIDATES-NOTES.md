# /candidates 重构笔记

## 改了什么
1. **目录结构升级**：将原 `app/candidates/page.tsx` 移至 `app/(app)/candidates/page.tsx`（迁入带导航外壳的 App Shell）。
2. **组件拆分**：提取了 `PromptInput`（提需求区域）、`CandidateCard`（候选卡片）、`ChatBox`（对话与蓝图生成区域）至 `components/candidates/` 下，保持主页状态管理整洁。
3. **视觉全面焕新**：遵循 `style-dna.md` 规范。
   - 整体走深色质感，背景分层采用浮起（`shadow-card-glow`, `bg-surface2/80`）。
   - 移除生硬线框，使用 `rounded-2xl`（肥圆角）和极淡的描边（`border-white/[0.04]` 等）。
   - 纯白仅用于主 CTA（"发送对话"等）和高亮语义。
   - 对话气泡（用户发言纯白胶囊，AI 发言深色融合背景带 `Play` 图标），对齐 workbench 的高级对话体验。
   - 状态徽章改造：用绿点/琥珀点 + 小文字，废弃冗长色块。

## 行为对照表
| 原行为 | 现实现 | 检查结果 |
| --- | --- | --- |
| getProfile 读档案 | `useEffect` 初始化拉取，渲染为右上角 `长期档案 x 条` 胶囊 | 通/保留 |
| rawText 提交 findCandidates | `PromptInput` 组件内的表单与按钮处理 | 通/保留 |
| 追问槽 (clarify) 回答拼回原问题 | 原样保留 `clarifyAnswer` 状态，并随同 `rawText` 发起 `ask` | 通/保留 |
| listCandidates 展示 | 兼容了 `proposed`/`accepted`/`rejected` 等所有状态徽章并由 `CandidateCard` 渲染 | 通/保留 |
| 逐条 verdictCandidate 否决 | 选否决后内联出现必填 input 框，输入理由提交后落库 | 通/保留 |
| 采纳双模式 (挂计划/就地新建) | `CandidateCard` 内置「选现有计划 / 新建一个计划」切换卡片 | 通/保留 |
| 采纳落点记忆 (landingPlans) | 原样保留，在展开 ChatBox 前有效关联目标计划 | 通/保留 |
| 对话式规划 (chattingId / getPlanChat) | 采纳后自动关联展开 `ChatBox`，加载历史对话记录 | 通/保留 |
| sayPlanChat 发言六轮上限提示 | 保留右上角 `${turns_used}/${max_turns} 轮` 的 UI 提示 | 通/保留 |
| generateBlueprint 生成蓝图 | UI 提供专门高亮 CTA，一键触发 `generateBlueprint` 并显示「待批蓝图」链接 | 通/保留 |
| 全部加载态、错误态、空态 | `PromptInput` 及 `CandidatesPage` 中充分兼容并使用规范样式提示 | 通/保留 |

## 遗留 TODO
- 纯视觉与体验层面的精调（如果用户在实际操作中觉得需要更高密度的排版）。
- `ChatBox` 底稿与 AI 的多轮交互由于不再是纯文本形式，如果 AI 返回格式复杂，可能需要进一步定制化解析。

## 第二轮：排版重构
- **两栏布局**：将候选页重构为左主干（≥60%）、右边栏（对话与蓝图，常驻 ~38% 宽）。彻底消除点击采纳后对话区突然撑开的跳跃感。
- **主次分离与列表降噪**：废除连续卡片堆叠模式。主推候选及路径型结果采用 `shadow-card-glow` 大卡高亮，其余候选以无边框列表行（Row）呈现，悬浮整行提亮。交互按钮小型化、行内化。
- **重塑 Prompt Input**：去除了外层卡片包装、说明标题及图标，压缩为极简的圆角药丸状 Prompt Bar（下拉框 + 输入框 + 提交按钮并排一排），更贴近 beUI。
- **对话框视觉升级**：全面移除外边框，输入区改为 `rounded-[24px]` 的胶囊形态，适配深色磨砂与悬浮交互。

## 第三轮：组件级重设计
- **提需求 bar (Prompt Input)**：参照 beUI「Prompt Input」，去除了外层大卡，内部采用首行 textarea，底行左侧计划选择，右侧纯白圆形发送按钮，整体为自增高容器。
- **候选行 (Candidate Card)**：参照 beautifului「Task Rows」，整体从大卡堆叠重构为状态胶囊行。去除了单独的大卡模式，展开呈现详情，内置审批操作。左侧使用不同状态圆点，右侧为状态徽章。
- **采纳确认 (Approval)**：参照 beUI「Approval Card」，彻底重构为候选行下方内联审批条。问题一句加两个选项胶囊（现有/新建）及确认按钮。
- **右栏规划对话 (Chat Box)**：参照 beautifului「Chat」面板。作为右侧全高的对话流与工作台。追问槽 (clarify) 变为无具体计划候选时消息流内的一张琥珀描边卡，替代了原本平铺在左侧的纯橙色大卡。
