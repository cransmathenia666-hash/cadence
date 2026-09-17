"use client";

import { useState } from "react";

import {
  type Checkpoint,
  type PlanTree,
  type Stage,
  type TaskNode,
} from "@/lib/api";

/**
 * 计划表里"长什么样"的那一半：只把后端给的数据画出来，外加三个写动作。
 *
 * 三个动作都直通后端、写完让页面重新取一次计划（刷新归 `app/page.tsx`）：
 * - 任务打勾（`POST /nodes/{id}/check`）——一步到完成，不写理由；
 * - 任务跳过（`/skip`）——跳过算完成，但必须写一句理由；
 * - 提交交付物（`/deliverable`）——阶段上的独立动作，可重新提交。
 *
 * 业务判定按 SPEC 第 10 节全在后端：前端不猜"这个阶段算不算完成"，
 * 阶段头上的「完成 / 未完成」直接读后端给的 `finished`。
 */

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

/** 落后的说法。null 表示无从判断（没定完成日，或已跳过）。 */
function lagLabel(lagDays: number | null): string {
  if (lagDays === null) return "无到期日";
  if (lagDays > 0) return `落后 ${lagDays} 天`;
  if (lagDays < 0) return `提前 ${-lagDays} 天`;
  return "按时";
}

function ChildLine({ node }: { node: TaskNode }) {
  return (
    <>
      {node.title} — <strong>{statusLabel(node.status)}</strong>
      {node.due_date !== null && <>，到期 {node.due_date}</>}
      {node.lag_days !== null && <>，{lagLabel(node.lag_days)}</>}
    </>
  );
}

/** 一条任务：显示状态 + 打勾 / 跳过两个动作（已完成或已跳过的就只剩状态）。 */
function TaskItem({
  task,
  busy,
  onAct,
}: {
  task: TaskNode;
  busy: boolean;
  onAct: (taskId: number, action: "check" | "skip", reason?: string) => void;
}) {
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");
  const decided = task.status === "done" || task.status === "skipped";

  return (
    <li>
      <ChildLine node={task} />
      {!decided && (
        <>
          {" "}
          <button type="button" onClick={() => onAct(task.id, "check")} disabled={busy}>
            打勾完成
          </button>{" "}
          {skipping ? (
            <>
              <input
                aria-label={`跳过「${task.title}」的理由`}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="为什么跳过（必填）"
              />
              <button
                type="button"
                onClick={() => onAct(task.id, "skip", reason)}
                disabled={busy || reason.trim() === ""}
              >
                确认跳过
              </button>{" "}
              <button type="button" onClick={() => setSkipping(false)} disabled={busy}>
                取消
              </button>
            </>
          ) : (
            <button type="button" onClick={() => setSkipping(true)} disabled={busy}>
              跳过
            </button>
          )}
        </>
      )}
    </li>
  );
}

/** 阶段上的交付物区：显示当前提交（如果有），并提供「提交 / 重新提交」表单。 */
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
    <ul>
      {stage.deliverable !== null && <li>计划交付物：{stage.deliverable}</li>}
      <li>
        交付物：
        {submission === null ? (
          <strong>未提交</strong>
        ) : (
          <>
            <strong>已提交</strong>：<a href={submission.url}>{submission.url}</a>（
            {submission.note}，{submission.created_at.slice(0, 16).replace("T", " ")}）
          </>
        )}{" "}
        {editing ? (
          <>
            <input
              aria-label={`「${stage.title}」交付物链接`}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="链接：仓库 / URL / 录屏"
            />
            <input
              aria-label={`「${stage.title}」交付物说明`}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="一句话说明"
            />
            <button
              type="button"
              onClick={() => onAct(stage.id, url, note)}
              disabled={busy || url.trim() === "" || note.trim() === ""}
            >
              确认提交
            </button>{" "}
            <button type="button" onClick={() => setEditing(false)} disabled={busy}>
              取消
            </button>
          </>
        ) : (
          <button
            type="button"
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
      </li>
    </ul>
  );
}

function StageItem({
  stage,
  busy,
  onTask,
  onDeliverable,
}: {
  stage: Stage;
  busy: boolean;
  onTask: (taskId: number, action: "check" | "skip", reason?: string) => void;
  onDeliverable: (stageId: number, url: string, note: string) => void;
}) {
  const { progress } = stage;

  return (
    <li>
      <h3>
        {stage.title} — <strong>{statusLabel(stage.status)}</strong>
        {" · "}
        {stage.finished ? <strong>完成</strong> : "未完成"}
      </h3>
      <DeliverableBlock stage={stage} busy={busy} onAct={onDeliverable} />
      <ul>
        {stage.due_date !== null && <li>计划完成日：{stage.due_date}</li>}
        {stage.lag_days !== null && <li>{lagLabel(stage.lag_days)}</li>}
        <li>
          任务 {progress.settled} / {progress.total} 收尾（完成 {progress.done}、跳过{" "}
          {progress.skipped}）
          {progress.total === 0
            ? "——没有任务，只看交付物"
            : progress.complete
              ? "，已全部收尾"
              : `，还开着：${progress.open_titles.join("、")}`}
        </li>
      </ul>

      <h4>任务</h4>
      {stage.tasks.length === 0 ? (
        <p>
          <small>这个阶段还没有任务。</small>
        </p>
      ) : (
        <ul>
          {stage.tasks.map((task) => (
            <TaskItem key={task.id} task={task} busy={busy} onAct={onTask} />
          ))}
        </ul>
      )}

      <h4>周打卡</h4>
      {stage.checkpoints.length === 0 ? (
        <p>
          <small>这个阶段还没有周打卡（它只管节奏，不影响阶段完成）。</small>
        </p>
      ) : (
        <ul>
          {stage.checkpoints.map((checkpoint: Checkpoint) => (
            <li key={checkpoint.id}>
              <ChildLine node={checkpoint} />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** 计划的全部信息。数据全部来自 `GET /api/plan`；写动作由页面传进来。 */
export function PlanTreeView({
  tree,
  busy = false,
  onTask = () => {},
  onDeliverable = () => {},
  error = null,
}: {
  tree: PlanTree;
  busy?: boolean;
  onTask?: (taskId: number, action: "check" | "skip", reason?: string) => void;
  onDeliverable?: (stageId: number, url: string, note: string) => void;
  error?: string | null;
}) {
  const { plan, current_stage: currentStage, lag, stages } = tree;

  if (plan === null) {
    return <p role="status">后端连上了，但库里还没有计划。先去「建计划 / 建节点」建一个。</p>;
  }

  return (
    <div>
      <h2>
        计划 #{plan.id}：{plan.goal}
      </h2>
      <ul>
        <li>状态：{plan.status}</li>
        <li>建于：{plan.valid_from}</li>
        <li>
          落后量：
          {lag.behind ? `落后 ${lag.lag_days} 天` : "没有落后"}
          {lag.worst !== null && (
            <>（最堵的是「{lag.worst.title}」，到期 {lag.worst.due_date ?? "未定"}）</>
          )}
        </li>
      </ul>

      {error !== null && (
        <p role="alert">
          <strong>操作失败：</strong>
          {error}
        </p>
      )}

      <h2>当前阶段</h2>
      {currentStage === null ? (
        <p>没有进行中的阶段——要么全收尾了，要么还没建阶段。</p>
      ) : (
        <ul>
          <li>名称：{currentStage.title}</li>
          <li>状态：{statusLabel(currentStage.status)}</li>
          {currentStage.deliverable !== null && <li>计划交付物：{currentStage.deliverable}</li>}
          <li>
            任务进度：{currentStage.progress.settled} / {currentStage.progress.total} 收尾
          </li>
        </ul>
      )}

      <h2>阶段 · 任务 · 周打卡</h2>
      {stages.length === 0 ? (
        <p>还没有阶段。</p>
      ) : (
        <ol>
          {stages.map((stage) => (
            <StageItem
              key={stage.id}
              stage={stage}
              busy={busy}
              onTask={onTask}
              onDeliverable={onDeliverable}
            />
          ))}
        </ol>
      )}
    </div>
  );
}
