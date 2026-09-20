"use client";

import { useEffect, useState } from "react";

import { PlanDialogue } from "@/components/plan-dialogue";
import { PlanTreeView } from "@/components/plan-tree";
import {
  ApiError,
  checkTask,
  closePlan,
  getPlan,
  listPlans,
  pausePlan,
  reopenPlan,
  skipNode,
  submitDeliverable,
  updateNodeFields,
  voidPlan,
  type PlanSummary,
  type PlanTree,
} from "@/lib/api";

/** 计划状态的说法。四态见 SPEC 决策 33（T27 拆分）。 */
const PLAN_STATUS_LABELS: Record<string, string> = {
  active: "进行中",
  paused: "暂时不做",
  closed: "已做完",
  void: "已作废",
};

export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  /** 所有计划（含暂停 / 收尾 / 作废）——历史段从它里面挑出不再进行中的那些。 */
  const [allPlans, setAllPlans] = useState<PlanSummary[]>([]);
  /** 正在看哪个计划；null = 让后端给最新建的那个 active 计划。 */
  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** 正在填作废理由的那个计划。 */
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");

  // 首次进入：取计划列表（一份只含进行中的、一份全的）；之后每次切换计划重新取计划树。
  // setState 一律放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）。
  useEffect(() => {
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
    listPlans(true)
      .then(setAllPlans)
      .catch(() => setAllPlans([]));
  }, []);

  useEffect(() => {
    getPlan(selectedId ?? undefined)
      .then((data) => {
        setTree(data);
        setError(null);
      })
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取计划时出了意外错误"),
      );
  }, [selectedId]);

  /** 写动作的统一外壳：动完重新取计划与两份列表；失败把中文原因摆到页面上。 */
  async function run(action: () => Promise<unknown>) {
    setActing(true);
    setActionError(null);
    try {
      await action();
      setTree(await getPlan(selectedId ?? undefined));
      setPlans(await listPlans());
      setAllPlans(await listPlans(true));
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "操作失败，原因不明");
    } finally {
      setActing(false);
    }
  }

  /** 不再进行中的那些，按状态排在历史段里。 */
  const endedPlans = allPlans.filter((item) => item.status !== "active");

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>当前计划</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            方向跟踪与三级结构任务落实。节点字段原地可改，已达标阶段可直接交交付物，
            不要哪一步就打勾跳过它——跳过只留一句理由，不进永久禁区。
          </p>
        </div>
        {acting && (
          <div className="badge badge-in_progress flex-row">
            <span className="spinner" />
            <span>处理中…</span>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: "14px 18px", marginBottom: "16px" }}>
        <div className="flex-between" style={{ flexWrap: "wrap", gap: "10px" }}>
          <div className="flex-row" style={{ gap: "10px" }}>
            <label htmlFor="plan-switch" style={{ fontWeight: 600, margin: 0 }}>
              选择计划：
            </label>
            {plans.length === 0 ? (
              <span style={{ color: "var(--text-muted)", fontSize: "13px" }}>
                暂无进行中的计划，请前往「新建」页面创建。
              </span>
            ) : (
              <select
                id="plan-switch"
                value={selectedId === null ? "" : String(selectedId)}
                onChange={(event) =>
                  setSelectedId(event.target.value === "" ? null : Number(event.target.value))
                }
                style={{ minWidth: "260px" }}
              >
                <option value="">（最新建的进行中计划）</option>
                {plans.map((item) => (
                  <option key={item.id} value={item.id}>
                    #{item.id}：{item.goal}（{item.stages_finished}/{item.stages} 阶段完成）
                  </option>
                ))}
              </select>
            )}
          </div>

          {tree?.plan && (
            <div className="flex-row gap-sm">
              <span className={`badge badge-${tree.plan.status === "active" ? "in_progress" : "not_started"}`}>
                {PLAN_STATUS_LABELS[tree.plan.status] ?? tree.plan.status}
              </span>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                建于 {tree.plan.valid_from?.slice(0, 10)}
              </span>
            </div>
          )}
        </div>

        {plans.length > 0 && (
          <details style={{ marginTop: "12px", marginBottom: 0, padding: "8px 12px" }}>
            <summary style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              计划管理（暂停 / 收尾 / 作废）
            </summary>
            <div style={{ marginTop: "10px" }}>
              <p style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "10px" }}>
                暂停 = 暂时不做（随时能「继续做」）；收尾 = 做完了（能「重开」）；作废 = 单向门（必须填理由）。
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {plans.map((item) => (
                  <div
                    key={item.id}
                    className="flex-between"
                    style={{
                      padding: "6px 10px",
                      background: "var(--bg-subtle)",
                      borderRadius: "var(--radius-sm)",
                      fontSize: "13px",
                    }}
                  >
                    <span>
                      <strong>#{item.id}</strong> {item.goal}
                    </span>
                    <div className="flex-row gap-sm">
                      <button
                        type="button"
                        className="sm"
                        onClick={() => run(() => closePlan(item.id))}
                        disabled={acting}
                      >
                        收尾
                      </button>
                      <button
                        type="button"
                        className="sm"
                        onClick={() => run(() => pausePlan(item.id))}
                        disabled={acting}
                      >
                        暂停
                      </button>
                      {voidingId === item.id ? (
                        <div className="flex-row gap-sm">
                          <input
                            aria-label={`作废计划 #${item.id} 的理由`}
                            value={voidReason}
                            onChange={(event) => setVoidReason(event.target.value)}
                            placeholder="作废理由（必填）"
                            style={{ width: "160px" }}
                          />
                          <button
                            type="button"
                            className="danger sm"
                            onClick={() =>
                              run(async () => {
                                await voidPlan(item.id, voidReason);
                                setVoidingId(null);
                                setVoidReason("");
                                if (selectedId === item.id) setSelectedId(null);
                              })
                            }
                            disabled={acting || voidReason.trim() === ""}
                          >
                            确认
                          </button>
                          <button
                            type="button"
                            className="sm"
                            onClick={() => setVoidingId(null)}
                            disabled={acting}
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="danger sm"
                          onClick={() => {
                            setVoidingId(item.id);
                            setVoidReason("");
                          }}
                          disabled={acting}
                        >
                          作废
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </details>
        )}

        {endedPlans.length > 0 && (
          <details style={{ marginTop: "8px", marginBottom: 0, padding: "8px 12px" }}>
            <summary style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              历史计划归档（{endedPlans.length} 个）
            </summary>
            <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
              {endedPlans.map((item) => (
                <div
                  key={item.id}
                  className="flex-between"
                  style={{
                    padding: "6px 10px",
                    background: "var(--bg-subtle)",
                    borderRadius: "var(--radius-sm)",
                    fontSize: "13px",
                  }}
                >
                  <div>
                    <strong>#{item.id}</strong> {item.goal}{" "}
                    <span className="badge badge-not_started">
                      {PLAN_STATUS_LABELS[item.status] ?? item.status}
                    </span>
                    {item.ended_reason && (
                      <span style={{ color: "var(--text-muted)", marginLeft: "8px", fontSize: "12px" }}>
                        原因：{item.ended_reason}
                      </span>
                    )}
                  </div>
                  <div>
                    {item.status === "paused" && (
                      <button
                        type="button"
                        className="sm primary"
                        onClick={() => run(() => reopenPlan(item.id))}
                        disabled={acting}
                      >
                        继续做
                      </button>
                    )}
                    {item.status === "closed" && (
                      <button
                        type="button"
                        className="sm"
                        onClick={() => run(() => reopenPlan(item.id))}
                        disabled={acting}
                      >
                        重开
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {error !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>取计划失败：</strong>
          {error}
        </div>
      )}

      {error === null && tree === null && (
        <div className="card" style={{ textAlign: "center", padding: "40px 20px" }}>
          <span className="spinner" style={{ width: "20px", height: "20px", marginBottom: "8px" }} />
          <p style={{ color: "var(--text-muted)" }}>正在加载计划数据…</p>
        </div>
      )}

      {tree !== null && tree.plan !== null && tree.behind_reason !== null && (
        <div className="alert alert-warning" style={{ display: "block" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
            <strong style={{ fontSize: "14px" }}>进度提醒：{tree.behind_reason}</strong>
          </div>
          <p style={{ fontSize: "12px", color: "var(--warning)", marginBottom: "8px" }}>
            落后只是节奏提醒，怎么做完全由你决定。系统建议以下处理方向：
          </p>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            {(tree.advice ?? []).map((item) => (
              <div
                key={item.kind}
                style={{
                  background: "#fff",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "12px",
                  border: "1px solid var(--warning-border)",
                  flex: "1 1 240px",
                }}
              >
                <strong>{item.label}</strong>：{item.detail}
              </div>
            ))}
          </div>
        </div>
      )}

      {tree !== null && (
        <PlanTreeView
          tree={tree}
          busy={acting}
          error={actionError}
          onNode={(nodeId, action, reason) =>
            run(() => (action === "check" ? checkTask(nodeId) : skipNode(nodeId, reason ?? "")))
          }
          onDeliverable={(stageId, url, note) => run(() => submitDeliverable(stageId, url, note))}
          onFields={(nodeId, input) => run(() => updateNodeFields(nodeId, input))}
        />
      )}

      {/* 计划级长对话。对话里「确认」一条建议之后只要重新取一遍数——改的动作后端已经做完，
          所以给同一个外壳传一个空 thunk（它负责把最新计划与两份列表拉回来）。 */}
      {tree !== null && tree.plan !== null && (
        <PlanDialogue
          key={tree.plan.id}
          planId={tree.plan.id}
          onChanged={() => run(async () => undefined)}
        />
      )}
    </div>
  );
}
