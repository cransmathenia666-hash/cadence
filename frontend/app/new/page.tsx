"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createNode,
  createPlan,
  getPlan,
  listPlans,
  type NodeLevel,
  type PlanSummary,
  type PlanTree,
} from "@/lib/api";

const LOAD_FAILED = "取计划时出了意外错误";

const LEVEL_LABELS: Record<NodeLevel, string> = {
  stage: "阶段",
  task: "任务",
  checkpoint: "周打卡",
};

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

export default function NewPage() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [targetPlanId, setTargetPlanId] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);

  // 建计划
  const [goal, setGoal] = useState("");

  // 建节点
  const [level, setLevel] = useState<NodeLevel>("task");
  const [title, setTitle] = useState("");
  const [deliverable, setDeliverable] = useState("");
  const [parentId, setParentId] = useState("");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    getPlan()
      .then((data) => {
        setTree(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
  }, []);

  async function refresh() {
    try {
      setTree(await getPlan(targetPlanId === "" ? undefined : Number(targetPlanId)));
      setPlans(await listPlans());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  const planId = targetPlanId === "" ? (tree?.plan?.id ?? null) : Number(targetPlanId);
  const stages = tree?.stages ?? [];

  async function onCreatePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const created = await createPlan(goal);
      setFeedback({ ok: true, text: `成功建立计划 #${created.id}：${created.goal}` });
      setGoal("");
      setTargetPlanId(String(created.id));
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建计划失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onCreateNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (planId === null) return;
    setPending(true);
    setFeedback(null);
    try {
      const created = await createNode({
        planId,
        level,
        title,
        parentId: level === "stage" ? null : Number(parentId),
        deliverable: level === "stage" ? deliverable : null,
        dueDate: dueDate === "" ? null : dueDate,
      });
      setFeedback({
        ok: true,
        text: `已在计划 #${planId} 下创建${LEVEL_LABELS[level]} #${created.id}：${created.title}`,
      });
      setTitle("");
      setDeliverable("");
      setDueDate("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建节点失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>新建计划与节点</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            手动新建顶级计划，或为现有计划手动补充阶段、任务与周打卡节点。
          </p>
        </div>
      </div>

      {loadError !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>加载失败：</strong>
          {loadError}
        </div>
      )}

      {feedback !== null && (
        <div className={`alert ${feedback.ok ? "alert-success" : "alert-danger"}`} role="status">
          {feedback.text}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>
        {/* 建计划 */}
        <div className="card" style={{ margin: 0 }}>
          <h2>① 创建新计划</h2>
          <form onSubmit={onCreatePlan} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div>
              <label htmlFor="goal">计划核心目标（必填）：</label>
              <input
                id="goal"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="例如：Web 后端工程基础与实战上线"
                required
                style={{ width: "100%" }}
              />
            </div>
            <div className="flex-between" style={{ marginTop: "4px" }}>
              <small>新计划建好后可直接作为活动目标</small>
              <button type="submit" className="primary" disabled={pending || goal.trim() === ""}>
                {pending ? "正在创建…" : "创建新计划"}
              </button>
            </div>
          </form>
        </div>

        {/* 建节点 */}
        <div className="card" style={{ margin: 0 }}>
          <h2>② 添加三级节点</h2>
          {planId === null ? (
            <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
              尚未选择目标计划。请先在左侧新建计划，或在下拉中指定。
            </p>
          ) : (
            <form onSubmit={onCreateNode} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div>
                <label htmlFor="target-plan">目标归属计划：</label>
                <select
                  id="target-plan"
                  value={targetPlanId}
                  onChange={(event) => {
                    setTargetPlanId(event.target.value);
                    getPlan(event.target.value === "" ? undefined : Number(event.target.value)).then(
                      setTree,
                    );
                  }}
                  style={{ width: "100%" }}
                >
                  <option value="">（最新建的计划）#{tree?.plan?.id} {tree?.plan?.goal}</option>
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>
                      #{p.id}：{p.goal}
                    </option>
                  ))}
                </select>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "10px" }}>
                <div>
                  <label htmlFor="level">节点层级：</label>
                  <select
                    id="level"
                    value={level}
                    onChange={(event) => setLevel(event.target.value as NodeLevel)}
                    style={{ width: "100%" }}
                  >
                    <option value="task">任务（可打勾/跳过）</option>
                    <option value="stage">阶段（大阶段容器）</option>
                    <option value="checkpoint">周打卡（节奏检查点）</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="title">节点标题（必填）：</label>
                  <input
                    id="title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder="输入阶段或任务标题"
                    required
                    style={{ width: "100%" }}
                  />
                </div>
              </div>

              {level === "stage" ? (
                <div>
                  <label htmlFor="deliverable">阶段交付物（验收指标）：</label>
                  <input
                    id="deliverable"
                    value={deliverable}
                    onChange={(event) => setDeliverable(event.target.value)}
                    placeholder="例如：输出一份可测试运行的代码仓库"
                    style={{ width: "100%" }}
                  />
                </div>
              ) : (
                <div>
                  <label htmlFor="parent">所属父阶段（必填）：</label>
                  <select
                    id="parent"
                    value={parentId}
                    onChange={(event) => setParentId(event.target.value)}
                    required
                    style={{ width: "100%" }}
                  >
                    <option value="" disabled>
                      请选择归属哪个阶段…
                    </option>
                    {stages.map((stage) => (
                      <option key={stage.id} value={stage.id}>
                        阶段 #{stage.id}：{stage.title}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label htmlFor="due">建议截止日期（可选）：</label>
                <input
                  id="due"
                  type="date"
                  value={dueDate}
                  onChange={(event) => setDueDate(event.target.value)}
                  style={{ width: "100%" }}
                />
              </div>

              <div className="flex-between" style={{ marginTop: "4px" }}>
                <small>新建节点实时进台账</small>
                <button
                  type="submit"
                  className="primary"
                  disabled={pending || title.trim() === "" || (level !== "stage" && parentId === "")}
                >
                  {pending ? "正在添加…" : "添加节点"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
