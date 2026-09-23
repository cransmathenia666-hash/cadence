"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  ApiError,
  MEMORY_KINDS,
  PROFILE_CATEGORIES,
  batchApproveMemories,
  createMemory,
  decideProposal,
  getMemory,
  getMemoryInbox,
  listMemoryScans,
  listPlans,
  previewMemoryPurge,
  purgeMemory,
  reviewMemory,
  scanMemories,
  updateMemory,
  voidMemory,
  type MemoryCandidate,
  type MemoryItem,
  type MemoryListing,
  type MemoryScope,
  type MemoryScanRecord,
  type PlanSummary,
  type ProfileCategory,
  type PurgePreview,
} from "@/lib/api";

const LOAD_FAILED = "取记忆时出了意外错误";
const CATEGORY_KEYS = Object.keys(PROFILE_CATEGORIES) as ProfileCategory[];
const KIND_KEYS = Object.keys(MEMORY_KINDS);

// 「还作数」预填的默认周期：与后端 `memory.REVIEW_DAYS` 同一个数，只用来预填输入框
// （用户想填哪天就填哪天——他走查时的反例正是「养成一周的习惯不该复核三个月」）。
const REVIEW_DAYS = 90;

type Tab = "current" | "inbox" | "due";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 本地日期（不是 `toISOString`——那个按 UTC 算，跨时区会差一天）。 */
function defaultReviewDate(): string {
  const next = new Date();
  next.setDate(next.getDate() + REVIEW_DAYS);
  const month = String(next.getMonth() + 1).padStart(2, "0");
  const day = String(next.getDate()).padStart(2, "0");
  return `${next.getFullYear()}-${month}-${day}`;
}

/**
 * 「复核时间」那一行：**常驻**在带复核时间的条目上（走查时它只出现在操作回执的一句话里，
 * 被误读成「永久生效」）。到期时这一行自己变成「已到复核时间 · 不再当依据」。
 */
function ReviewLine({ item }: { item: MemoryItem }) {
  if (!item.review_at) return null;
  const date = item.review_at.slice(0, 10);
  if (item.review_due) {
    return (
      <div style={{ fontSize: "11px", color: "var(--danger)", marginTop: "4px" }}>
        已到复核时间 · 不再当依据（原定 {date}）
      </div>
    );
  }
  return (
    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
      下次复核：{date}
    </div>
  );
}

/**
 * 记忆页（2026-09-21 记忆系统，方案 `docs/记忆系统.md`）。
 *
 * 三个标签页对应长期记忆的三件事：**当前记忆**（现在算数的）、**记忆收件箱**（系统扫出来
 * 等你说行不行的候选）、**待复核**（到了复核时间、默认已不当依据的那些）。
 *
 * 两条口径照后端来，页面不自己发明：
 * ① 只有 `batch_eligible` 的候选给复选框——「新增 + 你自己说过的」才能批量批准，
 *    取代 / Agent 推断 / 彻底删除一律逐条；
 * ② 彻底删除先点「彻底删除」看影响预览，再点「确认删除」才动手，删完如实显示清没清干净。
 */
export default function MemoryPage() {
  const [tab, setTab] = useState<Tab>("current");
  const [listing, setListing] = useState<MemoryListing | null>(null);
  const [inbox, setInbox] = useState<MemoryCandidate[]>([]);
  const [scans, setScans] = useState<MemoryScanRecord[]>([]);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [planId, setPlanId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);

  // 手工补一条
  const [newScope, setNewScope] = useState<MemoryScope>("global");
  const [newCategory, setNewCategory] = useState<ProfileCategory>("current_state");
  const [newKind, setNewKind] = useState("constraint");
  const [newContent, setNewContent] = useState("");
  const [newReviewAt, setNewReviewAt] = useState("");
  const [newReason, setNewReason] = useState("");

  // 逐条操作
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editReason, setEditReason] = useState("");
  const [voiding, setVoiding] = useState<MemoryItem | null>(null);
  const [voidReason, setVoidReason] = useState("");
  const [purging, setPurging] = useState<{ item: MemoryItem; preview: PurgePreview | null } | null>(null);
  const [purgeReason, setPurgeReason] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [batchReason, setBatchReason] = useState("");

  // 「还作数」点开之后的日期输入（一次只开一行）。预填今天 + 90 天，改不改由你。
  const [renewOpen, setRenewOpen] = useState<string | null>(null);
  const [renewDate, setRenewDate] = useState("");

  useEffect(() => {
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
  }, []);

  useEffect(() => {
    refresh(planId);
    // 取数写在 .then 回调里：effect 里同步 setState 会被 react-hooks/set-state-in-effect 拦
  }, [planId]);

  async function refresh(id: number | null) {
    try {
      const [memories, candidates, history] = await Promise.all([
        getMemory(id ?? undefined),
        getMemoryInbox(),
        listMemoryScans(10),
      ]);
      setListing(memories);
      setInbox(candidates.candidates);
      setScans(history.scans);
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  async function run(action: () => Promise<string>) {
    setPending(true);
    setFeedback(null);
    try {
      const text = await action();
      setFeedback({ ok: true, text });
      await refresh(planId);
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "这一步没做成，原因不明") });
    } finally {
      setPending(false);
    }
  }

  /**
   * 扫一批新经历。它和别的操作有一点不同：**「扫描失败」不是接口报错**，而是后端如实记下
   * 的一行结果（模型没给出合格输出、或没配 provider）。所以这里读 `status` 把实话摆出来，
   * 而不是当成异常——异常留给「请求本身没发出去」那种情况。
   */
  async function onScan() {
    setPending(true);
    setFeedback(null);
    try {
      const data = await scanMemories({ planId: planId ?? undefined });
      const latest = data.scans[data.scans.length - 1];
      if (!latest) {
        setFeedback({ ok: true, text: "这一轮没有需要扫描的新经历" });
      } else if (latest.status === "failed") {
        setFeedback({
          ok: false,
          text: `扫描没跑成：${latest.error ?? "原因不明"}（一条候选都没落）`,
        });
      } else if (latest.status === "pending") {
        setFeedback({ ok: true, text: "这条扫描还排着队（计划收尾时登记的），再点一次" });
      } else {
        setFeedback({
          ok: true,
          text:
            `扫了 ${latest.scanned} 条经历，落 ${latest.candidates} 条候选` +
            (latest.has_more ? "；还有没扫完的，再点一次接着扫" : ""),
        });
      }
      await refresh(planId);
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "扫描没跑成，原因不明") });
    } finally {
      setPending(false);
    }
  }

  const globalItems = listing?.global ?? [];
  const planItems = listing?.plan ?? [];
  const dueItems = listing?.due ?? [];

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>记忆</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            系统只把值得长期记住的东西提成候选，写不写进去由你点头；每条记忆都带来源原话，
            可改、可作废、可彻底删除。
          </p>
        </div>
        {listing && (
          <span className="badge badge-in_progress">
            全局 {listing.counts.global} 条 · 这个计划 {listing.counts.plan} 条 · 待复核{" "}
            {listing.counts.due} 条 · 收件箱 {inbox.length} 条
          </span>
        )}
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

      {/* 扫描：它只产候选，不写记忆 */}
      <div className="card" style={{ padding: "14px 18px" }}>
        <div className="flex-between">
          <div>
            <h3 style={{ margin: 0 }}>扫描新经历</h3>
            <small style={{ color: "var(--text-muted)" }}>
              翻一遍计划对话、报告、提案裁定这些已经发生过的事，把值得长期记住的提成候选
              ——它不会直接写进记忆。
            </small>
          </div>
          <div className="flex-row gap-sm">
            <button type="button" className="primary sm" disabled={pending} onClick={onScan}>
              {pending ? "扫描中…" : "扫描新经历"}
            </button>
          </div>
        </div>
        {scans.length > 0 && (
          <details style={{ marginTop: "10px", marginBottom: 0, fontSize: "12px" }}>
            <summary style={{ color: "var(--text-muted)" }}>最近扫过几次</summary>
            <ul style={{ margin: "8px 0 0", paddingLeft: "18px", color: "var(--text-muted)" }}>
              {scans.map((row) => (
                <li key={row.scan_id}>
                  {row.created_at?.slice(0, 16)} · {row.trigger_label}
                  {row.plan_id === null ? "（全局）" : `（计划 #${row.plan_id}）`} ·{" "}
                  {row.status === "pending"
                    ? "还没跑（计划收尾时登记的）"
                    : row.status === "failed"
                      ? `失败：${row.error ?? "原因不明"}`
                      : `扫 ${row.scanned} 条 / 落 ${row.candidates} 条候选`}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {/* 计划筛选 */}
      <div className="flex-row gap-sm" style={{ margin: "16px 0", alignItems: "center" }}>
        <label htmlFor="memory-plan">看哪个计划的记忆：</label>
        <select
          id="memory-plan"
          value={planId === null ? "" : String(planId)}
          onChange={(event) => setPlanId(event.target.value === "" ? null : Number(event.target.value))}
          style={{ minWidth: "240px" }}
        >
          <option value="">（不看计划内的，只看全局）</option>
          {plans.map((item) => (
            <option key={item.id} value={item.id}>
              #{item.id}：{item.goal}
            </option>
          ))}
        </select>
      </div>

      {/* 三个标签页 */}
      <div className="flex-row gap-sm" style={{ marginBottom: "12px" }}>
        {(
          [
            ["current", `当前记忆（${globalItems.length + planItems.length}）`],
            ["inbox", `记忆收件箱（${inbox.length}）`],
            ["due", `待复核（${dueItems.length}）`],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={tab === key ? "primary sm" : "sm"}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "current" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "20px" }}>
          <div className="card" style={{ height: "fit-content" }}>
            <h2>补一条记忆</h2>
            <form
              onSubmit={(event: FormEvent<HTMLFormElement>) => {
                event.preventDefault();
                run(async () => {
                  const created = await createMemory({
                    scope: newScope,
                    content: newContent,
                    planId: newScope === "plan" ? (planId ?? undefined) : undefined,
                    category: newScope === "global" ? newCategory : undefined,
                    kind: newScope === "plan" ? newKind : undefined,
                    reviewAt: newReviewAt || undefined,
                    reason: newReason || undefined,
                  });
                  setNewContent("");
                  setNewReason("");
                  return `已记下 #${created.id}（${created.scope_label}·${created.category_label ?? created.kind_label}）`;
                });
              }}
              style={{ display: "flex", flexDirection: "column", gap: "10px" }}
            >
              <div>
                <label htmlFor="new-scope">记在哪一级：</label>
                <select
                  id="new-scope"
                  value={newScope}
                  onChange={(event) => setNewScope(event.target.value as MemoryScope)}
                  style={{ width: "100%" }}
                >
                  <option value="global">全局（跟所有计划都相关，比如「我晚上精力差」）</option>
                  <option value="plan">只在某个计划里（比如「这个计划不碰新框架」）</option>
                </select>
              </div>
              {newScope === "global" ? (
                <div>
                  <label htmlFor="new-category">类别：</label>
                  <select
                    id="new-category"
                    value={newCategory}
                    onChange={(event) => setNewCategory(event.target.value as ProfileCategory)}
                    style={{ width: "100%" }}
                  >
                    {CATEGORY_KEYS.map((key) => (
                      <option key={key} value={key}>
                        {PROFILE_CATEGORIES[key]}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div>
                  <label htmlFor="new-kind">这一条是什么：</label>
                  <select
                    id="new-kind"
                    value={newKind}
                    onChange={(event) => setNewKind(event.target.value)}
                    style={{ width: "100%" }}
                  >
                    {KIND_KEYS.map((key) => (
                      <option key={key} value={key}>
                        {MEMORY_KINDS[key]}
                      </option>
                    ))}
                  </select>
                  {planId === null && (
                    <small style={{ color: "var(--text-muted)" }}>
                      上面还没选计划——先在页面中间挑一个计划，这条才知道记给谁。
                    </small>
                  )}
                </div>
              )}
              <div>
                <label htmlFor="new-content">记什么（一两句话）：</label>
                <textarea
                  id="new-content"
                  value={newContent}
                  onChange={(event) => setNewContent(event.target.value)}
                  rows={3}
                  placeholder="例如：我周二晚上固定有课，那天别排任务"
                  required
                  style={{ width: "100%" }}
                />
              </div>
              <div>
                <label htmlFor="new-review">什么时候回头复核（可留空）：</label>
                <input
                  id="new-review"
                  type="date"
                  value={newReviewAt}
                  onChange={(event) => setNewReviewAt(event.target.value)}
                  style={{ width: "100%" }}
                />
              </div>
              <div>
                <label htmlFor="new-reason">为什么记它（可留空）：</label>
                <input
                  id="new-reason"
                  value={newReason}
                  onChange={(event) => setNewReason(event.target.value)}
                  style={{ width: "100%" }}
                />
              </div>
              <div className="flex-between">
                <small style={{ color: "var(--text-muted)" }}>你自己敲的这条不需要来源证据</small>
                <button
                  type="submit"
                  className="primary"
                  disabled={
                    pending ||
                    newContent.trim() === "" ||
                    (newScope === "plan" && planId === null)
                  }
                >
                  {pending ? "正在保存…" : "记下来"}
                </button>
              </div>
            </form>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <MemoryBlock
              title="全局记忆"
              hint="跟所有计划都相关的那些（档案里的一条就是这里的一条）"
              items={globalItems}
              pending={pending}
              onEdit={(item) => {
                setEditing(item);
                setEditContent(item.content);
                setEditReason("");
                setVoiding(null);
              }}
              onVoid={(item) => {
                setVoiding(item);
                setVoidReason("");
                setEditing(null);
              }}
              onPurge={(item) => {
                setPurging({ item, preview: null });
                setPurgeReason("");
              }}
            />
            <MemoryBlock
              title={planId === null ? "计划内记忆（还没选计划）" : `计划内记忆（#${planId}）`}
              hint="只在这个计划里算数，别的计划读不到"
              items={planItems}
              pending={pending}
              onEdit={(item) => {
                setEditing(item);
                setEditContent(item.content);
                setEditReason("");
                setVoiding(null);
              }}
              onVoid={(item) => {
                setVoiding(item);
                setVoidReason("");
                setEditing(null);
              }}
              onPurge={(item) => {
                setPurging({ item, preview: null });
                setPurgeReason("");
              }}
            />
          </div>
        </div>
      )}

      {tab === "inbox" && (
        <div className="card" style={{ padding: "16px 18px" }}>
          <div className="flex-between" style={{ marginBottom: "10px" }}>
            <div>
              <h2 style={{ margin: 0 }}>记忆收件箱</h2>
              <small style={{ color: "var(--text-muted)" }}>
                勾选的只能是「新增 + 你自己说过的」；取代、Agent 推断、彻底删除都要逐条看。
              </small>
            </div>
            <button
              type="button"
              className="primary sm"
              disabled={pending || selected.length === 0}
              onClick={() =>
                run(async () => {
                  const result = await batchApproveMemories(selected, batchReason || undefined);
                  setSelected([]);
                  setBatchReason("");
                  return (
                    `批准 ${result.approved.length} 条` +
                    (result.skipped.length > 0
                      ? `，${result.skipped.length} 条不能批量（${result.skipped[0].why}）`
                      : "")
                  );
                })
              }
            >
              批量批准（{selected.length}）
            </button>
          </div>

          {inbox.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
              收件箱是空的。点「扫描新经历」看看有没有值得记下来的东西。
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {inbox.map((row) => (
                <CandidateCard
                  key={row.proposal_id}
                  row={row}
                  pending={pending}
                  checked={selected.includes(row.proposal_id)}
                  onToggle={() =>
                    setSelected((was) =>
                      was.includes(row.proposal_id)
                        ? was.filter((id) => id !== row.proposal_id)
                        : [...was, row.proposal_id],
                    )
                  }
                  onApprove={() =>
                    run(async () => {
                      const result = await decideProposal(row.proposal_id, {
                        approved: true,
                        reason: "收件箱里批准的",
                      });
                      return `已批准提案 #${result.id}（${result.effect}）`;
                    })
                  }
                  onReject={() =>
                    run(async () => {
                      await decideProposal(row.proposal_id, {
                        approved: false,
                        reason: "这条不用记",
                      });
                      return `已驳回提案 #${row.proposal_id}`;
                    })
                  }
                />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "due" && (
        <div className="card" style={{ padding: "16px 18px" }}>
          <h2>待复核</h2>
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            到了复核时间的记忆默认已经不当依据（Agent 不会再拿它说话）。挨条过一遍：
            还作数就把时间往后推，不再作数就作废。
          </p>
          {dueItems.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
              现在没有需要复核的。
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {dueItems.map((item) => {
                const rowKey = `${item.scope}-${item.id}`;
                return (
                <div
                  key={rowKey}
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    padding: "10px 12px",
                    background: "var(--bg-subtle)",
                  }}
                >
                  <div className="flex-between">
                    <div style={{ fontSize: "13px", flex: 1 }}>
                      {item.content}
                      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
                        #{item.id} · {item.scope_label}
                        {item.plan_id === null ? "" : `·计划 #${item.plan_id}`} ·{" "}
                        {item.category_label ?? item.kind_label} · {item.source_kind_label}
                      </div>
                      <ReviewLine item={item} />
                    </div>
                    <div className="flex-row gap-sm" style={{ marginLeft: "12px" }}>
                      <button
                        type="button"
                        className="sm ghost"
                        disabled={pending}
                        onClick={() => {
                          setRenewOpen(renewOpen === rowKey ? null : rowKey);
                          setRenewDate(defaultReviewDate());
                        }}
                      >
                        还作数
                      </button>
                      <button
                        type="button"
                        className="sm ghost danger"
                        disabled={pending}
                        onClick={() =>
                          run(async () => {
                            await reviewMemory({
                              memoryId: item.id,
                              scope: item.scope,
                              decision: "void",
                              reason: "复核后确认不再作数",
                            });
                            return `#${item.id} 已作废`;
                          })
                        }
                      >
                        不再作数
                      </button>
                      <button
                        type="button"
                        className="sm ghost danger"
                        disabled={pending}
                        onClick={() => {
                          setPurging({ item, preview: null });
                          setPurgeReason("");
                        }}
                      >
                        彻底删除
                      </button>
                    </div>
                  </div>

                  {renewOpen === rowKey && (
                    <div
                      className="flex-row gap-sm"
                      style={{ marginTop: "10px", alignItems: "center", flexWrap: "wrap" }}
                    >
                      <label htmlFor={`renew-${rowKey}`} style={{ fontSize: "12px" }}>
                        下次复核放到哪天：
                      </label>
                      <input
                        id={`renew-${rowKey}`}
                        type="date"
                        value={renewDate}
                        onChange={(event) => setRenewDate(event.target.value)}
                      />
                      <button
                        type="button"
                        className="primary sm"
                        disabled={pending || renewDate === ""}
                        onClick={() =>
                          run(async () => {
                            const result = await reviewMemory({
                              memoryId: item.id,
                              scope: item.scope,
                              decision: "renew",
                              reviewAt: renewDate,
                              reason: `复核过了，还作数，下次复核放到 ${renewDate}`,
                            });
                            setRenewOpen(null);
                            return `#${item.id} 的复核时间推到了 ${result.memory?.review_at?.slice(0, 10) ?? renewDate}`;
                          })
                        }
                      >
                        确认
                      </button>
                      <button type="button" className="sm" onClick={() => setRenewOpen(null)}>
                        取消
                      </button>
                      <small style={{ color: "var(--text-muted)" }}>
                        留空不行；填哪天都可以（默认往后推 {REVIEW_DAYS} 天）
                      </small>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 改 */}
      {editing !== null && (
        <Modal onClose={() => setEditing(null)}>
          <h2>改这条记忆</h2>
          <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            改 = 台账「取代」：旧值留痕、内容换成新的，编号会变（历史永远查得到当时写过什么）。
          </p>
          <label style={{ fontSize: "12px" }}>新的内容</label>
          <textarea
            value={editContent}
            onChange={(event) => setEditContent(event.target.value)}
            rows={3}
            style={{ width: "100%" }}
          />
          <label style={{ fontSize: "12px" }}>为什么改（必填）</label>
          <input
            value={editReason}
            onChange={(event) => setEditReason(event.target.value)}
            style={{ width: "100%" }}
          />
          <div className="flex-row gap-sm" style={{ marginTop: "10px" }}>
            <button
              type="button"
              className="primary sm"
              disabled={pending || editReason.trim() === "" || editContent.trim() === ""}
              onClick={() =>
                run(async () => {
                  const updated = await updateMemory({
                    memoryId: editing.id,
                    scope: editing.scope,
                    content: editContent,
                    reason: editReason,
                  });
                  setEditing(null);
                  return `已换成 #${updated.id}`;
                })
              }
            >
              保存替换
            </button>
            <button type="button" className="sm" onClick={() => setEditing(null)}>
              取消
            </button>
          </div>
        </Modal>
      )}

      {/* 作废 */}
      {voiding !== null && (
        <Modal onClose={() => setVoiding(null)}>
          <h2>作废这条记忆</h2>
          <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            作废之后它不再参与判断，旧值仍留在台账里（要连正文一起抹掉，用「彻底删除」）。
          </p>
          <div style={{ fontSize: "13px" }}>「{voiding.content}」</div>
          <label style={{ fontSize: "12px" }}>为什么作废（必填）</label>
          <input
            value={voidReason}
            onChange={(event) => setVoidReason(event.target.value)}
            style={{ width: "100%" }}
          />
          <div className="flex-row gap-sm" style={{ marginTop: "10px" }}>
            <button
              type="button"
              className="danger sm"
              disabled={pending || voidReason.trim() === ""}
              onClick={() =>
                run(async () => {
                  await voidMemory({
                    memoryId: voiding.id,
                    scope: voiding.scope,
                    reason: voidReason,
                  });
                  setVoiding(null);
                  return `已作废 #${voiding.id}`;
                })
              }
            >
              确认作废
            </button>
            <button type="button" className="sm" onClick={() => setVoiding(null)}>
              取消
            </button>
          </div>
        </Modal>
      )}

      {/* 彻底删除：先预览、再确认 */}
      {purging !== null && (
        <Modal onClose={() => setPurging({ item: purging.item, preview: null })}>
          <h2>彻底删除（不可恢复）</h2>
          <div style={{ fontSize: "13px" }}>「{purging.item.content}」</div>
          {purging.preview === null ? (
            <div style={{ marginTop: "10px" }}>
              <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                先看一遍会影响哪些地方——这一步不会动任何数据。
              </p>
              <button
                type="button"
                className="sm"
                disabled={pending}
                onClick={() =>
                  run(async () => {
                    const preview = await previewMemoryPurge(purging.item.id, purging.item.scope);
                    setPurging({ item: purging.item, preview });
                    return `预览：一共有 ${preview.copies.length} 处正文副本`;
                  })
                }
              >
                查看影响预览
              </button>
            </div>
          ) : (
            <div style={{ marginTop: "10px" }}>
              <p style={{ fontSize: "12px" }}>
                删除会清掉：这条记忆的正文、引用它的候选里的内容、来源证据摘录，以及来源里
                引用的那句原话（换成「
                已按用户要求删除」）。只剩一条不含内容的删除墓碑。
              </p>
              <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                现在找到的副本（{purging.preview.copies.length} 处）：
                {purging.preview.copies.length === 0
                  ? "（没有别的副本）"
                  : purging.preview.copies
                      .map((copy) => `${copy.table}.${copy.column}#${copy.id}`)
                      .join("、")}
              </p>
              <label style={{ fontSize: "12px" }}>为什么删（可留空）</label>
              <input
                value={purgeReason}
                onChange={(event) => setPurgeReason(event.target.value)}
                style={{ width: "100%" }}
              />
              <div className="flex-row gap-sm" style={{ marginTop: "10px" }}>
                <button
                  type="button"
                  className="danger sm"
                  disabled={pending}
                  onClick={() =>
                    run(async () => {
                      const result = await purgeMemory({
                        memoryId: purging.item.id,
                        scope: purging.item.scope,
                        reason: purgeReason,
                      });
                      setPurging(null);
                      return result.complete
                        ? `已彻底删除 #${result.id}（清掉 ${result.affected} 处），只剩墓碑`
                        : `正文清了 ${result.affected} 处，但还有 ${result.leftover.length} 处没清掉——` +
                            `不敢说删除成功：${result.leftover
                              .map((item) => `${item.table}.${item.column}#${item.id}`)
                              .join("、")}`;
                    })
                  }
                >
                  确认删除
                </button>
                <button
                  type="button"
                  className="sm"
                  onClick={() => setPurging({ item: purging.item, preview: null })}
                >
                  返回
                </button>
                <button type="button" className="sm" onClick={() => setPurging(null)}>
                  取消
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

/** 一段记忆列表（全局一段、计划内一段，同一个渲染）。 */
function MemoryBlock({
  title,
  hint,
  items,
  pending,
  onEdit,
  onVoid,
  onPurge,
}: {
  title: string;
  hint: string;
  items: MemoryItem[];
  pending: boolean;
  onEdit: (item: MemoryItem) => void;
  onVoid: (item: MemoryItem) => void;
  onPurge: (item: MemoryItem) => void;
}) {
  return (
    <div className="card" style={{ margin: 0, padding: "16px 18px" }}>
      <div className="flex-between" style={{ marginBottom: "10px" }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        <small style={{ color: "var(--text-muted)" }}>{hint}</small>
      </div>
      {items.length === 0 ? (
        <p style={{ color: "var(--text-muted)", fontSize: "12px", margin: 0 }}>一条都没有。</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {items.map((item) => (
            <div
              key={`${item.scope}-${item.id}`}
              style={{
                background: "var(--bg-subtle)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "10px 12px",
              }}
            >
              <div className="flex-between" style={{ alignItems: "flex-start" }}>
                <div style={{ fontSize: "13px", lineHeight: 1.5, flex: 1 }}>
                  {item.content}
                </div>
                <div className="flex-row gap-sm" style={{ marginLeft: "12px" }}>
                  <button type="button" className="sm ghost" disabled={pending} onClick={() => onEdit(item)}>
                    改
                  </button>
                  <button
                    type="button"
                    className="sm ghost danger"
                    disabled={pending}
                    onClick={() => onVoid(item)}
                  >
                    作废
                  </button>
                  <button
                    type="button"
                    className="sm ghost danger"
                    disabled={pending}
                    onClick={() => onPurge(item)}
                  >
                    彻底删除
                  </button>
                </div>
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "6px" }}>
                #{item.id} · {item.category_label ?? item.kind_label} · {item.source_kind_label}
                {item.fact_time && ` · 事实时间 ${item.fact_time.slice(0, 10)}`}
              </div>
              <ReviewLine item={item} />
              {item.evidence.length > 0 && (
                <details style={{ marginTop: "6px", marginBottom: 0 }}>
                  <summary style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                    来源（{item.evidence.length} 条）
                  </summary>
                  <ul style={{ margin: "6px 0 0", paddingLeft: "18px", fontSize: "11px" }}>
                    {item.evidence.map((evidence, index) => (
                      <li key={`${evidence.source_type}-${evidence.source_id}-${index}`}>
                        {evidence.source_label}#{evidence.source_id}
                        {evidence.source_time && `（${evidence.source_time.slice(0, 10)}）`}：
                        「{evidence.excerpt}」
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 收件箱里的一张候选卡：动作、范围、陈述还是推断、证据原话，加批准 / 驳回。 */
function CandidateCard({
  row,
  pending,
  checked,
  onToggle,
  onApprove,
  onReject,
}: {
  row: MemoryCandidate;
  pending: boolean;
  checked: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: "12px 14px",
        background: "var(--bg-subtle)",
      }}
    >
      <div className="flex-between" style={{ alignItems: "flex-start" }}>
        <div className="flex-row gap-sm" style={{ flexWrap: "wrap" }}>
          <span className="badge badge-in_progress">{row.action_label}</span>
          <span className="badge badge-not_started">{row.scope_label}</span>
          {row.category_label && <span className="badge badge-not_started">{row.category_label}</span>}
          {row.kind_label && <span className="badge badge-not_started">{row.kind_label}</span>}
          <span className="badge badge-not_started">{row.source_kind_label}</span>
          {row.review_at && (
            <span className="badge badge-not_started">复核时间 {row.review_at.slice(0, 10)}</span>
          )}
        </div>
        {row.batch_eligible && (
          <label style={{ fontSize: "12px", marginLeft: "12px" }}>
            <input type="checkbox" checked={checked} onChange={onToggle} /> 可批量
          </label>
        )}
      </div>

      <div style={{ fontSize: "13px", marginTop: "8px" }}>
        {row.action === "review"
          ? `${row.decision_label ?? "处理"}：${row.target_content ?? ""}`
          : row.content}
      </div>

      {row.action === "supersede" && row.target_content && (
        <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
          取代旧的那条：「{row.target_content}」
        </div>
      )}

      <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "6px" }}>
        为什么：{row.reason}
      </div>

      {row.duplicate_hint && (
        <div className="alert alert-warning" style={{ fontSize: "12px", marginTop: "8px" }}>
          {row.duplicate_hint}
        </div>
      )}

      {row.evidence.length > 0 && (
        <ul style={{ margin: "8px 0 0", paddingLeft: "18px", fontSize: "12px" }}>
          {row.evidence.map((evidence, index) => (
            <li key={`${evidence.source_type}-${evidence.source_id}-${index}`}>
              {evidence.source_label}#{evidence.source_id}
              {evidence.source_time && `（${evidence.source_time.slice(0, 10)}）`}：「
              {evidence.excerpt}」
            </li>
          ))}
        </ul>
      )}

      <div className="flex-row gap-sm" style={{ marginTop: "10px" }}>
        <button type="button" className="primary sm" disabled={pending} onClick={onApprove}>
          批准
        </button>
        <button type="button" className="sm" disabled={pending} onClick={onReject}>
          驳回
        </button>
      </div>
    </div>
  );
}

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
    >
      <div className="card" style={{ maxWidth: "620px", width: "90%", margin: 0 }}>
        {children}
        <div className="flex-row gap-sm" style={{ marginTop: "12px" }}>
          <button type="button" className="sm" onClick={onClose}>
            关掉
          </button>
        </div>
      </div>
    </div>
  );
}
