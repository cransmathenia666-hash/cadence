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

/**
 * 最简的报告提交交互：选检查点 → 选结果 → 写一句话 → 提交。
 *
 * 这是 P2 的"闭环第一段"：你在别处学完，回来告诉系统结果，系统按状态机推进节点。
 * 故意不做样式与花活——先把**写**这条路打通；好看的界面属于 T8。
 *
 * 为什么是客户端组件：要在浏览器里发 POST（这样走的才是 CORS 那条路，
 * 而且你在开发者工具的 Network 面板里能看见这次提交）。
 */

const STATUS_LABELS: Record<ReportStatus, string> = {
  done: "完成",
  partial: "部分完成",
  stuck: "卡住了",
  skipped: "跳过",
};

/** 下拉框里的一项：一个检查点，附上它当前的状态，好让你知道自己在报告什么。 */
type Option = { id: number; label: string; status: string };

const LOAD_FAILED = "取计划时出了意外错误";

/** 把异常翻成人话：后端错误直接用它的 detail，其它情况用给定的兜底文案。 */
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

  /**
   * 初始取数。
   *
   * 为什么写成 `.then(回调)` 而不是直接 `await`：React 的规则不允许在 effect 体内
   * **同步** setState（会引发级联渲染），但"数据回来后更新状态"属于"订阅外部系统"，
   * 规则放行。写成 await 会被 eslint 的 react-hooks/set-state-in-effect 拦下。
   */
  useEffect(() => {
    getPlan()
      .then((data) => {
        setTree(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  /** 提交成功后再取一次，界面上的节点状态就跟着变了（事件处理器里可以 await）。 */
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
        // 后端把「从什么状态变成什么」直接返回了，所以这里不用自己推断
        text:
          `已记账：节点状态 ${result.node_status_before} → ${result.node_status}` +
          // T29 起报告不再顺产推进提案（那整类已删），阶段完成与否去计划表上看
          (result.proposal_id === null ? "" : `（这份报告还产出了提案 #${result.proposal_id}）`),
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
    <main>
      <h1>提交报告</h1>
      <p>
        <Link href="/">← 回联通验证页</Link>
      </p>

      {loadError !== null && (
        <p role="alert">
          <strong>取计划失败：</strong>
          {loadError}
        </p>
      )}

      {loadError === null && tree === null && <p role="status">正在取计划…</p>}

      {tree !== null && options.length === 0 && (
        <p role="status">
          这个计划还没有检查点。先去后端的 <code>/docs</code> 建一个，再回到这页。
        </p>
      )}

      {options.length > 0 && (
        <form onSubmit={onSubmit}>
          <p>
            <label htmlFor="node">报告哪个检查点：</label>
            <select
              id="node"
              value={nodeId}
              onChange={(event) => setNodeId(event.target.value)}
              required
            >
              <option value="" disabled>
                请选择…
              </option>
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}（当前 {option.status}）
                </option>
              ))}
            </select>
          </p>

          <fieldset>
            <legend>结果</legend>
            {(Object.keys(STATUS_LABELS) as ReportStatus[]).map((value) => (
              <label key={value}>
                <input
                  type="radio"
                  name="status"
                  value={value}
                  checked={status === value}
                  onChange={() => setStatus(value)}
                />
                {STATUS_LABELS[value]}
              </label>
            ))}
          </fieldset>

          <p>
            <label htmlFor="note">一句话说明（必填）：</label>
            <input
              id="note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              required
            />
          </p>

          <button type="submit" disabled={pending}>
            {pending ? "提交中…" : "提交"}
          </button>
        </form>
      )}

      {feedback !== null && (
        <p role={feedback.ok ? "status" : "alert"}>
          <strong>{feedback.ok ? "成功：" : "失败："}</strong>
          {feedback.text}
        </p>
      )}
    </main>
  );
}
