"use client";

import { useState } from "react";

import {
  type Checkpoint,
  type NodeFieldsInput,
  type PlanTree,
  type Stage,
  type TaskNode,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  not_started: "未开始",
  in_progress: "进行中",
  done: "完成",
  stuck: "卡住",
  skipped: "跳过",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function statusBadgeClass(status: string): string {
  if (status === "done") return "badge-done";
  if (status === "in_progress") return "badge-in_progress";
  if (status === "stuck") return "badge-stuck";
  if (status === "skipped") return "badge-skipped";
  return "badge-not_started";
}

function lagBadge(lagDays: number | null) {
  if (lagDays === null) return null;
  if (lagDays > 0) {
    return <span className="badge badge-lag-danger">落后 {lagDays} 天</span>;
  }
  if (lagDays < 0) {
    return <span className="badge badge-lag-ahead">提前 {-lagDays} 天</span>;
  }
  return <span className="badge badge-lag-normal">按时</span>;
}

/** 就地改一个已经建好的节点（T30，原地改，理由必填） */
function NodeFieldsEditor({
  node,
  busy,
  withDeliverable,
  onSave,
}: {
  node: { id: number; title: string; due_date: string | null; deliverable?: string | null };
  busy: boolean;
  withDeliverable: boolean;
  onSave: (nodeId: number, input: NodeFieldsInput) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(node.title);
  const [deliverable, setDeliverable] = useState(node.deliverable ?? "");
  const [dueDate, setDueDate] = useState(node.due_date ?? "");
  const [reason, setReason] = useState("");

  if (!editing) {
    return (
      <button
        type="button"
        className="ghost sm"
        onClick={() => setEditing(true)}
        disabled={busy}
        style={{ fontSize: "11px", padding: "2px 6px" }}
      >
        改字段
      </button>
    );
  }

  return (
    <div className="inline-edit-box">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
        <div>
          <label style={{ fontSize: "11px" }}>标题：</label>
          <input
            style={{ width: "100%" }}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div>
          <label style={{ fontSize: "11px" }}>截止日期（YYYY-MM-DD，留空清掉）：</label>
          <input
            style={{ width: "100%" }}
            value={dueDate}
            onChange={(event) => setDueDate(event.target.value)}
            placeholder="留空 = 清除到期日"
          />
        </div>
      </div>
      {withDeliverable && (
        <div>
          <label style={{ fontSize: "11px" }}>阶段要交的东西（交付物指标）：</label>
          <input
            style={{ width: "100%" }}
            value={deliverable}
            onChange={(event) => setDeliverable(event.target.value)}
            placeholder="例如：可访问的 Demo 链接，或通过测试的代码仓库"
          />
        </div>
      )}
      <div>
        <label style={{ fontSize: "11px" }}>修改理由（必填，记入不可逆台账）：</label>
        <input
          style={{ width: "100%" }}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="例如：课业变重调整进度；或重构了目标交付物"
        />
      </div>
      <div className="flex-row gap-sm" style={{ marginTop: "4px" }}>
        <button
          type="button"
          className="primary sm"
          onClick={() => {
            setEditing(false);
            onSave(node.id, {
              title,
              due_date: dueDate,
              deliverable: withDeliverable ? deliverable : undefined,
              reason,
            });
          }}
          disabled={busy || reason.trim() === ""}
        >
          保存变更
        </button>
        <button type="button" className="sm" onClick={() => setEditing(false)} disabled={busy}>
          取消
        </button>
      </div>
    </div>
  );
}

/** 一条任务：显示状态 + 打勾 / 跳过 / 改字段 */
function TaskItem({
  task,
  busy,
  onAct,
  onFields,
}: {
  task: TaskNode;
  busy: boolean;
  onAct: (taskId: number, action: "check" | "skip", reason?: string) => void;
  onFields: (nodeId: number, input: NodeFieldsInput) => void;
}) {
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");
  const decided = task.status === "done" || task.status === "skipped";

  return (
    <div className={`task-item-row ${decided ? "is-done" : ""}`}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: 1 }}>
        <span className={`badge ${statusBadgeClass(task.status)}`}>{statusLabel(task.status)}</span>
        <span style={{ fontWeight: decided ? 400 : 500 }}>{task.title}</span>
        {task.due_date && (
          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>到期：{task.due_date}</span>
        )}
        {lagBadge(task.lag_days)}
      </div>

      <div className="flex-row gap-sm">
        {!decided && (
          <>
            <button
              type="button"
              className="primary sm"
              onClick={() => onAct(task.id, "check")}
              disabled={busy}
            >
              打勾完成
            </button>
            {skipping ? (
              <div className="flex-row gap-sm">
                <input
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="跳过理由（必填）"
                  style={{ width: "130px", fontSize: "12px", padding: "2px 6px" }}
                />
                <button
                  type="button"
                  className="danger sm"
                  onClick={() => onAct(task.id, "skip", reason)}
                  disabled={busy || reason.trim() === ""}
                >
                  确认
                </button>
                <button type="button" className="sm" onClick={() => setSkipping(false)} disabled={busy}>
                  取消
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="sm"
                onClick={() => setSkipping(true)}
                disabled={busy}
              >
                跳过
              </button>
            )}
          </>
        )}
        <NodeFieldsEditor node={task} busy={busy} withDeliverable={false} onSave={onFields} />
      </div>
    </div>
  );
}

/** 阶段交付物 */
function DeliverableBlock({
  stage,
  busy,
  onAct,
}: {
  stage: Stage;
  busy: boolean;
  onAct: (stageId: number, url: string, note: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const submission = stage.deliverable_submission;

  return (
    <div
      style={{
        background: "var(--bg-subtle)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: "10px 14px",
        margin: "10px 0 14px",
        fontSize: "13px",
      }}
    >
      <div className="flex-between">
        <div>
          <span style={{ fontWeight: 600, color: "var(--text-main)" }}>目标交付物：</span>
          <span style={{ color: "var(--text-main)" }}>
            {stage.deliverable || <span style={{ color: "var(--text-muted)" }}>未设定</span>}
          </span>
        </div>
        {!editing && (
          <button
            type="button"
            className="sm primary"
            onClick={() => {
              setUrl(submission?.url ?? "");
              setNote("");
              setEditing(true);
            }}
            disabled={busy}
          >
            {submission === null ? "提交交付物" : "重新提交"}
          </button>
        )}
      </div>

      <div style={{ marginTop: "6px", fontSize: "12px" }}>
        <span style={{ fontWeight: 600 }}>验收状态：</span>
        {submission === null ? (
          <span className="badge badge-not_started">未提交验收凭据</span>
        ) : (
          <span className="badge badge-done">
            已提交：
            <a
              href={submission.url}
              target="_blank"
              rel="noreferrer"
              style={{ marginLeft: "4px", color: "var(--primary)" }}
            >
              {submission.url}
            </a>
            <span style={{ marginLeft: "6px", color: "var(--text-muted)" }}>
              ({submission.note} · {submission.created_at.slice(0, 10)})
            </span>
          </span>
        )}
      </div>

      {editing && (
        <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "6px" }}>
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="凭据链接（如 Github 仓库、在线 Demo、飞书文档、视频链接）"
            style={{ width: "100%" }}
          />
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="一句话说明本次交付内容或亮点"
            style={{ width: "100%" }}
          />
          <div className="flex-row gap-sm" style={{ marginTop: "2px" }}>
            <button
              type="button"
              className="primary sm"
              onClick={() => {
                onAct(stage.id, url, note);
                setEditing(false);
              }}
              disabled={busy || url.trim() === "" || note.trim() === ""}
            >
              确认提交验收
            </button>
            <button type="button" className="sm" onClick={() => setEditing(false)} disabled={busy}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function StageItem({
  stage,
  busy,
  onTask,
  onDeliverable,
  onFields,
}: {
  stage: Stage;
  busy: boolean;
  onTask: (taskId: number, action: "check" | "skip", reason?: string) => void;
  onDeliverable: (stageId: number, url: string, note: string) => void;
  onFields: (nodeId: number, input: NodeFieldsInput) => void;
}) {
  const { progress } = stage;

  return (
    <div className={`stage-card ${stage.finished ? "stage-done" : ""}`}>
      <div className="stage-header">
        <div>
          <div className="flex-row gap-sm" style={{ marginBottom: "4px" }}>
            <h3 style={{ margin: 0, fontSize: "15px" }}>{stage.title}</h3>
            <span className={`badge ${statusBadgeClass(stage.status)}`}>
              {statusLabel(stage.status)}
            </span>
            <span className={`badge ${stage.finished ? "badge-done" : "badge-not_started"}`}>
              {stage.finished ? "阶段已达成" : "阶段未达成"}
            </span>
            {lagBadge(stage.lag_days)}
          </div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            任务达成度：{progress.settled}/{progress.total}（完成 {progress.done}，跳过 {progress.skipped}）
            {progress.total === 0 ? " · 阶段无任务，直接以交付物验收" : progress.complete ? " · 任务已清空" : ""}
            {stage.due_date && ` · 截止日 ${stage.due_date}`}
          </div>
        </div>

        <NodeFieldsEditor node={stage} busy={busy} withDeliverable onSave={onFields} />
      </div>

      <div className="stage-content">
        <DeliverableBlock stage={stage} busy={busy} onAct={onDeliverable} />

        <div style={{ marginBottom: "12px" }}>
          <h4>阶段拆解任务 ({stage.tasks.length})</h4>
          {stage.tasks.length === 0 ? (
            <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              该阶段暂无具体任务清单，可随时在「新建」页面添加。
            </p>
          ) : (
            <div>
              {stage.tasks.map((task) => (
                <TaskItem key={task.id} task={task} busy={busy} onAct={onTask} onFields={onFields} />
              ))}
            </div>
          )}
        </div>

        {stage.checkpoints.length > 0 && (
          <details style={{ padding: "6px 10px", margin: 0, background: "var(--bg-subtle)" }}>
            <summary style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              周节奏打卡节点（{stage.checkpoints.length} 个，仅管节奏，不阻碍阶段达成）
            </summary>
            <div style={{ marginTop: "6px" }}>
              {stage.checkpoints.map((cp: Checkpoint) => (
                <div key={cp.id} className="task-item-row" style={{ fontSize: "12px", padding: "4px 8px" }}>
                  <div className="flex-row gap-sm">
                    <span className={`badge ${statusBadgeClass(cp.status)}`}>{statusLabel(cp.status)}</span>
                    <span>{cp.title}</span>
                    {cp.due_date && <span>({cp.due_date})</span>}
                  </div>
                  <NodeFieldsEditor node={cp} busy={busy} withDeliverable={false} onSave={onFields} />
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

export function PlanTreeView({
  tree,
  busy = false,
  onTask = () => {},
  onDeliverable = () => {},
  onFields = () => {},
  error = null,
}: {
  tree: PlanTree;
  busy?: boolean;
  onTask?: (taskId: number, action: "check" | "skip", reason?: string) => void;
  onDeliverable?: (stageId: number, url: string, note: string) => void;
  onFields?: (nodeId: number, input: NodeFieldsInput) => void;
  error?: string | null;
}) {
  const { plan, current_stage: currentStage, lag, stages } = tree;

  if (plan === null) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "30px 20px" }}>
        <p style={{ color: "var(--text-muted)" }}>
          当前库里尚未建立有效计划。请前往导航栏「新建」创建一个主目标计划。
        </p>
      </div>
    );
  }

  return (
    <div>
      {error !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>操作失败：</strong>
          {error}
        </div>
      )}

      <div className="card" style={{ marginBottom: "16px", padding: "16px 20px" }}>
        <div className="flex-between">
          <div>
            <span style={{ fontSize: "12px", color: "var(--text-muted)", fontWeight: 600 }}>
              当前正在推进的阶段：
            </span>
            {currentStage === null ? (
              <span style={{ marginLeft: "8px", color: "var(--text-muted)" }}>暂无进行中的阶段</span>
            ) : (
              <span style={{ marginLeft: "8px", fontWeight: 700, fontSize: "15px" }}>
                {currentStage.title}
              </span>
            )}
          </div>
          <div className="flex-row gap-sm">
            {lag.behind ? (
              <span className="badge badge-lag-danger">总体进度落后 {lag.lag_days} 天</span>
            ) : (
              <span className="badge badge-lag-ahead">整体进度按期进行</span>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: "20px" }}>
        <div className="flex-between" style={{ marginBottom: "10px" }}>
          <h2>阶段与任务执行树 ({stages.length} 个阶段)</h2>
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            任务全勾/跳过 ＋ 提交交付物 ＝ 阶段达成
          </span>
        </div>

        {stages.length === 0 ? (
          <div className="card" style={{ textAlign: "center", padding: "30px" }}>
            <p style={{ color: "var(--text-muted)" }}>
              计划下还没有阶段节点。可在「新建」页面手动补充，或在「候选清单」采纳提案生成蓝图。
            </p>
          </div>
        ) : (
          <div>
            {stages.map((stage) => (
              <StageItem
                key={stage.id}
                stage={stage}
                busy={busy}
                onTask={onTask}
                onDeliverable={onDeliverable}
                onFields={onFields}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
