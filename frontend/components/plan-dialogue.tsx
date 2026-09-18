"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  extractProfileProposals,
  getPlanDialogue,
  sayPlanDialogue,
  type PlanDialogueView,
} from "@/lib/api";

/**
 * T28：计划表页里那块「接着聊」的窗口。
 *
 * 与 `/candidates` 里的「规划对话」不是一回事：那段是**定方向**的（绑候选、最多 6 轮、
 * 终点是出一棵蓝图）；这一段跟着**计划**走，蓝图落地之后才真正开始用它——聊卡在哪、
 * 下一步先做哪个、要不要调整节奏。它**不限轮数**，成本闸是历史字符上限（后端截）。
 *
 * 它能做的只有两件事：说话，以及把聊出的「我的状态变了」提炼成待裁定的档案变更提案
 * （批准才写档案）。计划结构它一个字都改不了——那是计划表上按钮的事。
 */
export function PlanDialogue({ planId }: { planId: number }) {
  const [view, setView] = useState<PlanDialogueView | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getPlanDialogue(planId)
      .then(setView)
      .catch((cause: unknown) => setError(messageOf(cause, "取这段对话时出了意外错误")));
  }, [planId]);

  async function refresh() {
    setView(await getPlanDialogue(planId));
  }

  async function onSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await sayPlanDialogue(planId, message);
      setMessage("");
      await refresh();
      setNotice(`第 ${done.turns_used} 句已回（调了 ${done.calls} 次模型）。`);
    } catch (cause) {
      setError(messageOf(cause, "这一句没成功"));
      await refresh().catch(() => undefined); // 你那句话后端已经记下了，刷新能看到
    } finally {
      setBusy(false);
    }
  }

  async function onExtract() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await extractProfileProposals(planId);
      setNotice(
        done.items.length === 0
          ? "这段对话里没有需要改档案的变化——没落任何提案。"
          : `落了 ${done.items.length} 条档案变更提案（${done.items
              .map((item) => `${item.category}：${item.content}`)
              .join("；")}）——去「待裁定提案」页批准才会真写进档案。`,
      );
    } catch (cause) {
      setError(messageOf(cause, "提炼失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>跟 AI 聊聊这个计划</h2>
      <p>
        <small>
          蓝图落地之后接着用它：卡在哪、下一步先做哪个、要不要调节奏。它看得见这个计划的
          阶段、任务、最近报告与落后情况（每轮重新读一遍，所以你刚打的勾它也知道）。
          <strong>它改不了计划</strong>——要改结构就在下面的计划表里打勾 / 跳过 / 交交付物。
          {view !== null && ` 已经聊了 ${view.turns_used} 句；历史超过 ${view.char_limit} 字符时从最早的截断。`}
        </small>
      </p>

      {(view?.messages ?? []).map((item, index) => (
        <p key={index}>
          <strong>{item.role === "assistant" ? "它：" : "我："}</strong>
          {item.content}
          <br />
          <small>{item.created_at.slice(0, 16).replace("T", " ")}</small>
        </p>
      ))}
      {view !== null && view.messages.length === 0 && (
        <p role="status">
          <small>还没聊过。说一句现在的状况，它就会接着往下聊。</small>
        </p>
      )}

      <form onSubmit={onSend}>
        <textarea
          rows={2}
          cols={70}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="例如：我卡在「用 SQLite 建表」这步了，要不要先跳过它去做接口？"
          required
        />
        <br />
        <button type="submit" disabled={busy || message.trim() === ""}>
          {busy ? "正在说…" : "说这一句"}
        </button>{" "}
        <button
          type="button"
          onClick={onExtract}
          disabled={busy || view === null || !view.can_extract}
          title={view !== null && !view.can_extract ? "先聊过一句才有东西可提炼" : undefined}
        >
          把聊出的变化落成档案提案
        </button>
      </form>

      {error !== null && (
        <p role="alert">
          <strong>对话失败：</strong>
          {error}
        </p>
      )}
      {notice !== null && (
        <p role="status">
          <small>{notice}</small>
        </p>
      )}
    </section>
  );
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}