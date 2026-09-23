# cadence

> 把「我想学点什么」的模糊念头，变成一个有阶段、有任务、能长期跟进、并且会被记住的计划。

## 这是什么

cadence 是一个跑在你本机的学习决策与跟进 Agent。它只做**方向层**——你学什么、往哪走、现在到哪一步了；学习时长、打卡、番茄钟这类过程追踪不在范围内。

主要能力：

- **找方向**：说清你的想法和背景，它给出一批候选方向，你逐条裁定去留；选中的可以继续对话，聊成一张蓝图（阶段 → 任务），确认后落成计划。
- **跟进计划**：任务打勾、跳过、交交付物；落后了会有提醒；报告页看整体进度。计划支持暂停 / 收尾 / 作废，多条计划严格分开、互不干扰。
- **记忆**：它记得你的偏好、约束与历史结论，跨对话生效；也能从你的原始材料里定期扫描出候选记忆。新增、修正、删除都要经你点头。
- **提案与裁定**：凡是它想改动你的档案、计划或记忆，都会先变成一份提案，你裁定之后才落库。

## 快速开始

**前置**：Python 3.11+（开发环境为 3.14）、Node.js 20.9+

**后端**（第一个终端）

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.db init                            # 建库，可重复执行（以后加表 / 加列也走它）
python -m uvicorn app.main:app --reload --port 8000
```

**前端**（第二个终端）

```bash
cd frontend
npm install
npm run dev
```

打开 <http://localhost:3000>。

**接上你自己的模型服务**：第一次打开后进 `/providers`（模型服务管理），填一个 OpenAI 兼容的服务地址、模型名和你的密钥。密钥只写进你本机的数据库 `data/cadence.db`，不会出现在代码或版本库里。

## 页面导览

| 路径 | 作用 |
| --- | --- |
| `/` | 计划列表与计划管理（暂停 / 收尾 / 作废） |
| `/new` | 提一个新需求，走四问判断 |
| `/candidates` | 候选清单（裁定去留）＋ 对话式规划（聊成蓝图） |
| `/proposals` | 提案裁定（蓝图 / 档案变更 / 资料判断 / 计划改动 / 记忆候选） |
| `/report` | 报告：最新进行中计划的节点进度 |
| `/judge` | 资料判断 |
| `/memory` | 记忆：三层记忆、候选收件箱、扫描、彻底删除 |
| `/profile` | 档案录入与管理 |
| `/providers` | 模型服务管理 |

## 它是怎么运转的

你的输入（需求 / 裁定 / 材料）先到后端；后端把该问模型的交给模型，而模型只产出**结构化建议**（候选、提案），不直接改数据；你在页面上裁定之后，后端经台账（`backend/app/ledger.py`）记下变更并落到业务状态。前端不连数据库、不直连模型，也不保存业务状态。

## 开发与自检

```bash
cd backend
.venv\Scripts\python.exe -m pytest -q        # 全绿（当前 443 passed）
.venv\Scripts\python.exe tools\smoke_p1.py   # 闭环冒烟：自起临时库跑 17 步，不动你的数据
.venv\Scripts\python.exe tools\show_db.py    # 只读看库：计划树 / 报告 / 台账 / 提案

cd frontend
npm run build        # 也做首次类型生成，见下
npm run lint
npx tsc --noEmit
```

非 Windows 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。热加载只监听 Python——改 `backend/sql/schema.sql` 不会触发重启，重新执行 `python -m app.db init` 即可。

刚克隆下来要先跑一次 `npm run build`（或 `npm run dev`）再跑 `npx tsc --noEmit`：Next 16 的 `LayoutProps`、`PageProps` 是构建时生成的类型，没生成过就会报「找不到名字」。

## 边界与约定

- **单用户、本机运行**：没有登录，也没有多人数据隔离——请各自在本机跑一套，不要当成多用户服务部署。
- 数据只有 `data/cadence.db` 一个文件（已在 `.gitignore` 里）；删掉它就等于清空。
- 端口：后端 8000、前端 3000；跨源放行名单默认只放行本机前端，见 `backend/app/config.py`。
- 状态变更必须经台账写入，不直接改业务表；接口错误统一为 `{"detail": 中文一句话, "errors": [...]}`。

## 文档

- `docs/SPEC.md`——产品与架构规格（含全部已定决策）
- `HANDOFF.md`——当前进度、验收命令与下一步
- `docs/记忆系统.md`、`docs/agent落地.md`、`docs/候选路径与追问槽方案.md`、`docs/未决项讨论.md`——各模块方案与讨论

## 现状

P1–P3.10 已完成：后端地基与规则层、接口契约、前端九个页面、候选与去重、三级计划结构、计划生命周期、计划级对话、Agent 工具循环、记忆系统。P4（触达与导出：提醒、周检查点、Markdown 导出）在计划中。