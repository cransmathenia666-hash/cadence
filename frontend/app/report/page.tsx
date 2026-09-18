"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  getPlan,
  submitReport,
  type PlanTree,
  type ReportStatus,
} from "@/lib/api";

const STATUS_LABELS: Record<ReportStatus, string> = {
  done: "完成",
  partial: "部分完成",
  stuck: "卡住了",
  skipped: "跳过",
};

type Option = { id: number; label: string; status: string };

const LOAD_FAILED = "取计划时出了意外错误";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

function toOptions(tree: PlanTree): Option[] {
  return tree.stages.flatMap((stage) =>
    stage.checkpoints.map((checkpoint) => ({
      id: checkpoint.id,
      label: `${stage.title} · ${checkpoint.title}`,
      status: checkpoint.status,
    })),
  );
}

export default function ReportPage() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState("");
  const [status, setStatus] = useState<ReportStatus>("done");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getPlan()
      .then((data) => {
        setTree(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  async function refresh() {
    try {
      setTree(await getPlan());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  const options = tree === null ? [] : toOptions(tree);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const result = await submitReport({ nodeId: Number(nodeId), status, note });
      setFeedback({
        ok: true,
        text: `已记账：节点状态 ${result.node_status_before} → ${result.node_status}`,
      });
      setNote("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "提交失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>提交进度报告</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            对指定周打卡检查点上报当周执行情况，驱动状态机更新并留下不可逆台账记录。
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

      <div className="card">
        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <div>
            <label htmlFor="node">选择周打卡检查点：</label>
            <select
              id="node"
              value={nodeId}
              onChange={(event) => setNodeId(event.target.value)}
              required
              style={{ width: "100%" }}
            >
              <option value="">请选择检查点…</option>
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}（当前：{option.status}）
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "12px" }}>
            <div>
              <label htmlFor="status">执行结果：</label>
              <select
                id="status"
                value={status}
                onChange={(event) => setStatus(event.target.value as ReportStatus)}
                style={{ width: "100%" }}
              >
                {(Object.keys(STATUS_LABELS) as ReportStatus[]).map((key) => (
                  <option key={key} value={key}>
                    {STATUS_LABELS[key]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="note">一句话执行说明（必填）：</label>
              <input
                id="note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="例如：完成了所有基础接口，但测试覆盖率稍显不足"
                required
                style={{ width: "100%" }}
              />
            </div>
          </div>

          <div className="flex-between" style={{ marginTop: "6px" }}>
            <Link href="/" style={{ fontSize: "13px" }}>
              ← 返回计划表
            </Link>
            <button
              type="submit"
              className="primary"
              disabled={pending || nodeId === "" || note.trim() === ""}
            >
              {pending ? "正在提交台账…" : "提交报告"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
