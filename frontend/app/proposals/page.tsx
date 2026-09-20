"use client";

import { useEffect, useState } from "react";

import { BlueprintBody, selectionOf } from "@/components/blueprint-body";
import { JudgmentView } from "@/components/judgment-view";
import {
  ApiError,
  decideProposal,
  listProposals,
  planChangeTasks,
  type MaterialJudgmentPayload,
  type PlanChangePayload,
  type ProfileChangePayload,
  type Proposal,
} from "@/lib/api";

const CATEGORY_LABELS: Record<string, string> = {
  life_habit: "生活习惯（睡眠/运动/作息）",
  life_log: "生活记录（日程/课程/近况）",
  current_state: "当前状态（精力/时间/压力）",
  short_term_goal: "短期目标 + 当下痛点",
  long_axis: "长期主线（职业方向）",
};

const KIND_TITLES: Record<string, string> = {
  plan_blueprint: "蓝图待批",
  material_judgment: "资料判断",
  profile_change: "档案变更",
  plan_change: "计划改动",
};

export default function ProposalsPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [selections, setSelections] = useState<Record<number, string[]>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);

  function refresh() {
    listProposals()
      .then(setProposals)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取提案时出了意外错误"),
      );
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onDecide(proposal: Proposal, approved: boolean, reason?: string) {
    setBusy(true);
    setError(null);
    const selected = selectionOf(proposal, selections);
    try {
      const done = await decideProposal(proposal.id, {
        approved,
        reason,
        selected: proposal.kind === "plan_blueprint" ? selected : undefined,
      });
      setNotes((previous) => [
        approved
          ? done.effect === "blueprint_built"
            ? `已批准：计划 #${done.built?.plan_id ?? ""} 里建了 ` +
              `${done.built?.stages.length ?? 0} 个新阶段、${done.built?.tasks.length ?? 0} 件任务` +
              `${done.built?.notes.length ? `。${done.built.notes.join("；")}` : "。"}`
            : done.effect === "profile_written"
              ? `已批准：把「${done.written?.content ?? ""}」写进了长期档案（${done.written?.category ?? ""}）` +
                "——去「长期档案」页能看到它。"
              : done.effect === "node_updated" && done.updated !== null
                ? `已批准并原地修改：#${done.updated.node_id} 的 ${done.updated.changed.join("、")} 改为了 ` +
                  `${done.updated.changed.map((k) => done.updated?.after[k] ?? "（清空）").join("、")}（编号保持不变，台账已留痕）。`
                : done.effect === "node_added" && done.added !== null
                  ? `已批准并新建：${done.added.nodes
                      .map((node) => `${node.level === "stage" ? "阶段" : "任务"} #${node.id}「${node.title}」`)
                      .join("、")} 已进计划。`
                  : "已批准，只记账：这项不会改计划或档案。"
          : "已驳回，理由已记入台账。",
        ...previous,
      ]);
      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
      setRejectingId(null);
      refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>待裁定提案</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            所有可能改动实际数据的 AI 输出必须经过你在此拍板。勾选即生效，驳回必留理由。
          </p>
        </div>
        {proposals !== null && (
          <span className="badge badge-in_progress">{proposals.length} 项等待裁定</span>
        )}
      </div>

      {error !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>操作失败：</strong>
          {error}
        </div>
      )}

      {notes.length > 0 && (
        <div className="alert alert-success" role="status">
          <ul style={{ paddingLeft: "16px", margin: 0 }}>
            {notes.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      )}

      {proposals !== null && proposals.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "40px" }}>
          <p style={{ color: "var(--text-muted)", margin: 0 }}>
            当前没有待裁定的提案。当在候选清单生成蓝图、或在对话中提炼出档案变更时会出现在这里。
          </p>
        </div>
      )}

      {proposals === null && (
        <div className="card" style={{ textAlign: "center", padding: "40px" }}>
          <span className="spinner" style={{ width: "20px", height: "20px", marginBottom: "8px" }} />
          <p style={{ color: "var(--text-muted)" }}>正在读取待裁定提案…</p>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        {(proposals ?? []).map((proposal) => {
          const isBlueprint = proposal.kind === "plan_blueprint";
          const nothingTicked = isBlueprint && selectionOf(proposal, selections).length === 0;

          return (
            <div key={proposal.id} className="card" style={{ margin: 0 }}>
              <div className="card-header">
                <div className="flex-row gap-sm">
                  <span className="badge badge-in_progress">
                    {KIND_TITLES[proposal.kind] ?? proposal.kind}
                  </span>
                  <span style={{ fontWeight: 600 }}>提案 #{proposal.id}</span>
                  <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                    提出于 {proposal.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                </div>
              </div>

              {proposal.reason && (
                <p style={{ fontSize: "13px", color: "var(--text-muted)", marginBottom: "12px" }}>
                  背景说明：{proposal.reason}
                </p>
              )}

              <ProposalBody
                proposal={proposal}
                selection={selectionOf(proposal, selections)}
                onSelection={(value) =>
                  setSelections((previous) => ({ ...previous, [proposal.id]: value }))
                }
              />

              <div
                style={{
                  borderTop: "1px solid var(--border)",
                  marginTop: "16px",
                  paddingTop: "12px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div className="flex-row gap-sm">
                  <button
                    type="button"
                    className="primary"
                    onClick={() => onDecide(proposal, true)}
                    disabled={busy || nothingTicked}
                    title={nothingTicked ? "至少要勾一个阶段或任务" : undefined}
                  >
                    批准生效
                  </button>
                  {rejectingId === proposal.id ? (
                    <div className="flex-row gap-sm">
                      <input
                        id={`reason-${proposal.id}`}
                        value={reasons[proposal.id] ?? ""}
                        onChange={(event) =>
                          setReasons((previous) => ({
                            ...previous,
                            [proposal.id]: event.target.value,
                          }))
                        }
                        placeholder="驳回理由（必填，进不可逆台账）"
                        style={{ width: "240px" }}
                      />
                      <button
                        type="button"
                        className="danger"
                        onClick={() => onDecide(proposal, false, reasons[proposal.id])}
                        disabled={busy || (reasons[proposal.id] ?? "").trim() === ""}
                      >
                        确认驳回
                      </button>
                      <button type="button" onClick={() => setRejectingId(null)} disabled={busy}>
                        取消
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="danger"
                      onClick={() => setRejectingId(proposal.id)}
                      disabled={busy}
                    >
                      驳回
                    </button>
                  )}
                </div>
                {nothingTicked && (
                  <span style={{ fontSize: "12px", color: "var(--danger)" }}>
                    蓝图必须勾选至少一项阶段或任务才能批准
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProposalBody({
  proposal,
  selection,
  onSelection,
}: {
  proposal: Proposal;
  selection: string[];
  onSelection: (value: string[]) => void;
}) {
  if (proposal.kind === "plan_blueprint") {
    return (
      <div>
        <div className="alert alert-info" style={{ fontSize: "12px", margin: "8px 0 12px" }}>
          此蓝图来自对话式规划。勾选的项目才会新建入计划树，未勾选项直接舍弃。同名阶段将自动复用。
        </div>
        <BlueprintBody proposal={proposal} selection={selection} onSelection={onSelection} />
      </div>
    );
  }

  if (proposal.kind === "material_judgment") {
    const payload = proposal.payload as unknown as MaterialJudgmentPayload;
    return (
      <div>
        <div style={{ background: "var(--bg-subtle)", padding: "10px", borderRadius: "var(--radius-sm)", marginBottom: "10px" }}>
          <span style={{ fontWeight: 600, fontSize: "12px" }}>资料原文：</span>
          <span style={{ fontSize: "13px" }}>{payload.source_text}</span>
        </div>
        {payload.judgment !== undefined && <JudgmentView judgment={payload.judgment} />}
      </div>
    );
  }

  if (proposal.kind === "profile_change") {
    const payload = proposal.payload as unknown as ProfileChangePayload;
    return (
      <div
        style={{
          background: "var(--bg-subtle)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          padding: "14px",
        }}
      >
        <div className="flex-row gap-sm" style={{ marginBottom: "8px" }}>
          <span className="badge badge-in_progress">{payload.category}</span>
          <span style={{ fontWeight: 600 }}>{CATEGORY_LABELS[payload.category] ?? payload.category}</span>
        </div>
        <div style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-main)", marginBottom: "8px" }}>
          {payload.content}
        </div>
        {payload.why && (
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            <strong>提炼依据：</strong>
            {payload.why}
          </div>
        )}
      </div>
    );
  }

  if (proposal.kind === "plan_change") {
    const payload = proposal.payload as unknown as PlanChangePayload;
    const tasks = planChangeTasks(payload);
    return (
      <div
        style={{
          background: "var(--bg-subtle)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          padding: "14px",
        }}
      >
        <div className="flex-row gap-sm" style={{ marginBottom: "8px" }}>
          <span className="badge badge-in_progress">
            {payload.action === "update_node"
              ? "改节点字段"
              : payload.action === "add_task"
                ? "加任务"
                : "加阶段"}
          </span>
          <span style={{ fontWeight: 600 }}>计划 #{payload.plan_id} 改动建议</span>
        </div>

        <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-main)", marginBottom: "8px" }}>
          {payload.summary || "未提供改动概要"}
        </div>

        {payload.action === "update_node" && payload.fields && (
          <div style={{ fontSize: "13px", marginBottom: "6px", color: "var(--text-main)" }}>
            目标节点：<strong>{payload.node_title ?? `#${payload.node_id}`}</strong>（
            {payload.level === "stage" ? "阶段" : "任务"}）
            <ul style={{ paddingLeft: "18px", margin: "4px 0", fontSize: "12px", color: "var(--text-muted)" }}>
              {Object.entries(payload.fields).map(([k, v]) => (
                <li key={k}>
                  {k}：{payload.before?.[k] ?? "（无）"} → <strong>{v ?? "（清空）"}</strong>
                </li>
              ))}
            </ul>
          </div>
        )}

        {payload.action === "add_task" && (
          <div style={{ fontSize: "13px", marginBottom: "6px", color: "var(--text-main)" }}>
            所属阶段：<strong>{payload.stage_title ?? `阶段 #${payload.stage_id}`}</strong>
            <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
              要加的任务（{tasks.length} 件）：
            </div>
            <ul style={{ paddingLeft: "18px", margin: "2px 0", fontSize: "12px" }}>
              {tasks.map((task, index) => (
                <li key={`${task.title}-${index}`}>
                  <strong>{task.title}</strong>
                  {task.due_date && ` · 截止日：${task.due_date}`}
                </li>
              ))}
            </ul>
          </div>
        )}

        {payload.action === "add_stage" && payload.stage && (
          <div style={{ fontSize: "13px", marginBottom: "6px", color: "var(--text-main)" }}>
            新阶段（排最后）：<strong>{payload.stage.title}</strong>
            {payload.stage.deliverable && (
              <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "2px" }}>
                交付物：{payload.stage.deliverable}
              </div>
            )}
            {tasks.length > 0 && (
              <>
                <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "6px" }}>
                  一并建的任务（{tasks.length} 件）：
                </div>
                <ul style={{ paddingLeft: "18px", margin: "2px 0", fontSize: "12px" }}>
                  {tasks.map((task, index) => (
                    <li key={`${task.title}-${index}`}>
                      <strong>{task.title}</strong>
                      {task.due_date && ` · 截止日：${task.due_date}`}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {payload.why && (
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "6px" }}>
            <strong>提炼理由：</strong>
            {payload.why}
          </div>
        )}

        <div style={{ fontSize: "11px", color: "var(--text-dim)", marginTop: "8px" }}>
          来自计划跟进对话。批准将直接落地修改并留下不可逆台账，驳回将忽略此建议。
        </div>
      </div>
    );
  }

  return <p>未知类型的提案格式</p>;
}
