"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  decideProposal,
  extractProfileProposals,
  getPlanDialogue,
  sayPlanDialogue,
  type DialogueSuggestion,
  type PlanDialogueView,
} from "@/lib/api";

/** 「忽略」时固定送的理由：它进台账，回答「这条建议当时为什么没动」。 */
const IGNORE_REASON = "聊天里先不动";

/**
 * 计划落地之后那块「接着聊」的窗口（T28）；T31 起它能**提一条可执行建议**。
 *
 * 它能做三件事：说话、把聊出的「我的状态变了」提炼成待裁定的档案变更提案、以及每轮
 * **最多提一条改动建议**（改一个已有节点，或加一件任务 / 一个阶段）。建议不会自己生效——
 * 它就摆在那条消息下面：点「确认」才真改（改的原地改、id 不变；加的走建节点），
 * 点「忽略」就当没提过（台账记一句「聊天里先不动」）。两者都是当场裁定那条提案，
 * 所以建议不会在「待裁定」页堆积。
 */
export function PlanDialogue({
  planId,
  onChanged,
}: {
  planId: number;
  /** 确认 / 忽略之后叫父组件重新取一遍计划——改完马上在计划表里看得见。 */
  onChanged?: () => void | Promise<void>;
}) {
  const [view, setView] = useState<PlanDialogueView | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [decidingId, setDecidingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
      setNotice(
        done.suggestion === null
          ? `第 ${done.turns_used} 句已回（调用了 ${done.calls} 次模型）。`
          : `第 ${done.turns_used} 句已回（调用了 ${done.calls} 次模型），它还提了一条建议——` +
              "就在下面那条消息里，点「确认」才会真改。",
      );
    } catch (cause) {
      setError(messageOf(cause, "这一句未能成功发送"));
      await refresh().catch(() => undefined);
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
          ? "对话中未识别到需要同步档案的长期变更。"
          : `已提炼 ${done.items.length} 条档案变更提案（${done.items
              .map((item) => `${item.category}：${item.content}`)
              .join("；")}）——请前往「待裁定」页面查看并批准生效。`,
      );
    } catch (cause) {
      setError(messageOf(cause, "提炼档案提案失败"));
    } finally {
      setBusy(false);
    }
  }

  /** 确认 / 忽略一条建议：两件事都是当场裁定那条提案（批准 / 驳回）。 */
  async function onDecide(suggestion: DialogueSuggestion, approved: boolean) {
    setDecidingId(suggestion.proposal_id);
    setError(null);
    setNotice(null);
    try {
      const done = await decideProposal(suggestion.proposal_id, {
        approved,
        reason: approved ? undefined : IGNORE_REASON,
      });
      await refresh();
      await onChanged?.(); // 计划表跟着刷新——改完马上看得见
      if (!approved) {
        setNotice(`这条先不动（台账记了一句「${IGNORE_REASON}」）。`);
      } else if (done.effect === "node_updated" && done.updated !== null) {
        const { node_id, changed, after } = done.updated;
        setNotice(
          `改好了：#${node_id} 的 ${changed.join("、")} 改成了 ` +
            `${changed.map((name) => after[name] ?? "（清空）").join("、")}` +
            "——编号没变，台账留了一条流水。",
        );
      } else if (done.effect === "node_added" && done.added !== null) {
        setNotice(`加好了：#${done.added.id}「${done.added.title}」已经进计划表。`);
      } else {
        setNotice("已确认。");
      }
    } catch (cause) {
      setError(messageOf(cause, approved ? "确认失败" : "忽略失败"));
      await refresh().catch(() => undefined); // 计划可能已经变了，把最新状态摆出来
    } finally {
      setDecidingId(null);
    }
  }

  return (
    <div className="card" style={{ marginTop: "24px" }}>
      <div className="card-header">
        <div>
          <h2 style={{ margin: 0, fontSize: "16px" }}>计划落地跟进助手</h2>
          <small>
            可随时探讨卡点、拆解优先级、或汇报临时状态变更。助手会实时感知当前计划所有阶段与任务；
            它一轮最多提一条改动建议，而且改不动任何东西——你点「确认」它才真改，点「忽略」就当没提过。
          </small>
        </div>
        {view !== null && (
          <span className="badge badge-not_started">
            已对话 {view.turns_used} 轮 · 历史上限 {view.char_limit} 字
          </span>
        )}
      </div>

      <div className="chat-container">
        {view !== null && view.messages.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--text-muted)", padding: "20px 0" }}>
            <p>尚未发起对话。输入你在执行当前计划时的疑惑或心得，AI 助手将结合你的档案共同探讨。</p>
          </div>
        )}

        {(view?.messages ?? []).map((item, index) => {
          const isUser = item.role === "user";
          return (
            <div key={index} className={`chat-bubble ${isUser ? "user" : "assistant"}`}>
              <span className="bubble-role">{isUser ? "你" : "AI 决策助手"}</span>
              <div style={{ whiteSpace: "pre-wrap" }}>{item.content}</div>
              {item.suggestion !== null && (
                <SuggestionBar
                  suggestion={item.suggestion}
                  busy={busy || decidingId !== null}
                  deciding={decidingId === item.suggestion.proposal_id}
                  onDecide={onDecide}
                />
              )}
              <span className="bubble-time">
                {item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : ""}
              </span>
            </div>
          );
        })}
      </div>

      <form onSubmit={onSend} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <textarea
          rows={3}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="例如：我卡在第 2 个任务的接口测试上了，是先跳过还是继续死磕？"
          disabled={busy}
          style={{ width: "100%" }}
          required
        />
        <div className="flex-between">
          <button
            type="button"
            className="sm"
            onClick={onExtract}
            disabled={busy || view === null || !view.can_extract}
            title={view !== null && !view.can_extract ? "先聊过至少一轮才能提炼变化" : undefined}
          >
            提炼对话中的个人状态变更进档案提案
          </button>
          <button type="submit" className="primary" disabled={busy || message.trim() === ""}>
            {busy ? "思考与回复中…" : "发送消息"}
          </button>
        </div>
      </form>

      {error !== null && (
        <div className="alert alert-danger" style={{ marginTop: "12px" }}>
          <strong>对话失败：</strong>
          {error}
        </div>
      )}
      {notice !== null && (
        <div className="alert alert-info" style={{ marginTop: "12px" }}>
          {notice}
        </div>
      )}
    </div>
  );
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/**
 * 一条建议的确认条：一行人话 + 「确认 / 忽略」两个按钮（T31，SPEC 决策 39）。
 *
 * 已裁定的（`status !== "pending"`）只显示结果、不给按钮——它已经进过台账了；
 * 反悔要改就去计划表里改，或者再让它提一条。
 */
function SuggestionBar({
  suggestion,
  busy,
  deciding,
  onDecide,
}: {
  suggestion: DialogueSuggestion;
  busy: boolean;
  deciding: boolean;
  onDecide: (suggestion: DialogueSuggestion, approved: boolean) => void;
}) {
  const done = suggestion.status !== "pending";
  return (
    <div
      style={{
        marginTop: "10px",
        padding: "10px 12px",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-subtle)",
      }}
    >
      <div className="flex-row gap-sm" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <span className={`badge ${done ? "badge-done" : "badge-in_progress"}`}>
          {done ? "已裁定" : "它想改计划"}
        </span>
        <strong style={{ fontSize: "13px" }}>{suggestion.summary}</strong>
      </div>
      {done ? (
        <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "6px" }}>
          {suggestion.status === "accepted"
            ? "你已经确认过这一条。"
            : "你选了先不动这一条（台账记了一句「聊天里先不动」）。"}
        </div>
      ) : (
        <div className="flex-row gap-sm" style={{ marginTop: "8px" }}>
          <button type="button" className="primary sm" onClick={() => onDecide(suggestion, true)} disabled={busy}>
            {deciding ? "正在改…" : "确认改"}
          </button>
          <button type="button" className="sm" onClick={() => onDecide(suggestion, false)} disabled={busy}>
            忽略
          </button>
        </div>
      )}
    </div>
  );
}
