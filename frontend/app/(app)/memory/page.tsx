"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  ArrowRight,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  FileClock,
  Inbox,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";

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
import { HoverSelect } from "@/components/ui/hover-select";

const LOAD_FAILED = "取记忆时出了意外错误";
const CATEGORY_KEYS = Object.keys(PROFILE_CATEGORIES) as ProfileCategory[];
const KIND_KEYS = Object.keys(MEMORY_KINDS);

// 「还作数」预填的默认周期：与后端 `memory.REVIEW_DAYS` 同一个数，只用来预填输入框
// （用户想填哪天就填哪天——他走查时的反例正是「养成一周的习惯不该复核三个月」）。
const REVIEW_DAYS = 90;

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
      <div className="mt-1 inline-flex items-center gap-1.5 border-l border-amber-300/50 pl-2 text-[11px] text-amber-200/90">
        <Clock3 className="size-3" />
        已到复核时间 · 不再当依据（原定 {date}）
      </div>
    );
  }
  return (
    <div className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-white/50">
      <Clock3 className="size-3" />下次复核：{date}
    </div>
  );
}

/**
 * 记忆页（2026-09-21 记忆系统，方案 `docs/记忆系统.md`；2026-10-05 重排构图）。
 *
 * 构图：**左操作台、右记忆本体**。扫描 / 工作范围 / 手工补一条 / 收件箱入口收进左栏，
 * 右栏首屏就是记忆卡片——到期复核置顶成琥珀色提醒带，其余按「计划内 / 全局」分节、
 * 按类别分组，每条记忆一张卡。收件箱处理是主栏的一种临时视图，从左栏入口进出。
 *
 * 两条口径照后端来，页面不自己发明：
 * ① 只有 `batch_eligible` 的候选给复选框——「新增 + 你自己说过的」才能批量批准，
 *    取代 / Agent 推断 / 彻底删除一律逐条；
 * ② 彻底删除先点开影响预览，再确认，删完如实显示清没清干净。
 */export default function MemoryPage() {
  const [view, setView] = useState<"list" | "inbox">("list");
  const [showComposer, setShowComposer] = useState(false);
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
    <div className="memory-page min-h-full bg-transparent px-5 pb-16 pt-20 md:px-8 lg:px-12">
      <div className="mx-auto w-full max-w-[1440px]">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-x-8 gap-y-3 border-b border-white/[0.1] pb-5">
          <div className="flex items-center gap-3">
            <BrainCircuit className="size-5 text-white/70" />
            <div>
              <h1 className="text-[26px] font-semibold tracking-tight text-white">记忆</h1>
              <p className="mt-0.5 text-xs text-white/50">长期依据工作台 · 可追溯、可复核、可撤回</p>
            </div>
          </div>
          {listing && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-l border-white/[0.1] pl-5">
              <Metric label="全局" value={listing.counts.global} />
              <Metric label="计划内" value={listing.counts.plan} />
              <Metric label="待复核" value={listing.counts.due} accent="amber" />
              <Metric label="收件箱" value={inbox.length} />
            </div>
          )}
        </header>

        {loadError !== null && (
          <div className="mb-5 flex items-start gap-3 rounded-2xl border border-red-300/15 bg-red-400/10 px-4 py-3 text-sm text-red-200" role="alert">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <span><strong className="font-medium">加载失败：</strong>{loadError}</span>
          </div>
        )}
        {feedback !== null && (
          <div className={`mb-5 flex items-start gap-3 rounded-2xl border px-4 py-3 text-sm ${feedback.ok ? "border-green/20 bg-green/10 text-green" : "border-red-300/15 bg-red-400/10 text-red-200"}`} role="status">
            {feedback.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <ShieldAlert className="mt-0.5 size-4 shrink-0" />}
            <span>{feedback.text}</span>
          </div>
        )}

        <div className="grid gap-10 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="min-w-0 border-b border-white/[0.1] pb-8 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-8">
            <div className="space-y-8">
              <RailBlock title="工作范围" hint="计划内记忆与扫描对象都跟着它走">
                <HoverSelect
                  value={planId === null ? "" : String(planId)}
                  onChange={(value) => setPlanId(value === "" ? null : Number(value))}
                  options={[{ value: "", label: "全局（不限定计划）" }, ...plans.map((item) => ({ value: String(item.id), label: `#${item.id}：${item.goal}` }))]}
                  className="w-full"
                />
              </RailBlock>

              <RailBlock title="扫描新经历" hint="翻过去的对话、报告和裁定，只产候选，不直接写记忆">
                <button type="button" disabled={pending} onClick={onScan} className="btn-primary inline-flex h-9 w-full items-center justify-center gap-2 rounded-md px-3.5 text-xs font-medium text-white disabled:opacity-40">
                  <RefreshCw className={`size-3.5 ${pending ? "animate-spin" : ""}`} />{pending ? "扫描中…" : "开始扫描"}
                </button>
                <p className="mt-2.5 text-[11px] leading-5 text-white/45">
                  {scans[0]
                    ? <><span className="text-white/65">最近 {scans[0].created_at?.slice(0, 16)}</span> · {scans[0].status === "failed" ? "扫描失败" : scans[0].status === "pending" ? "等待处理" : `落 ${scans[0].candidates} 条候选`}</>
                    : "还没有扫描记录"}
                </p>
                {scans.length > 0 && (
                  <details className="mt-2">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] text-white/45 hover:text-white/65"><FileClock className="size-3" />扫描记录<ChevronDown className="size-3" /></summary>
                    <div className="mt-2 space-y-1.5 border-l border-white/[0.1] pl-2.5">
                      {scans.map((row) => <p key={row.scan_id} className="text-[11px] leading-5 text-white/45"><span className="text-white/60">{row.created_at?.slice(0, 16)}</span> · {row.trigger_label}{row.plan_id === null ? "（全局）" : `（计划 #${row.plan_id}）`} · {row.status === "pending" ? "还没跑" : row.status === "failed" ? `失败：${row.error ?? "原因不明"}` : `扫 ${row.scanned} 条 / 落 ${row.candidates} 条候选`}</p>)}
                    </div>
                  </details>
                )}
              </RailBlock>

              <RailBlock title="手工补一条" hint="你自己确认的话，不需要来源证据就能记下">
                <button type="button" onClick={() => setShowComposer((open) => !open)} className="inline-flex h-9 w-full items-center justify-center gap-2 border border-white/[0.12] px-3 text-xs text-white/70 hover:border-white/30 hover:text-white">
                  <Plus className="size-3.5" />{showComposer ? "收起录入框" : "打开录入框"}
                </button>
              </RailBlock>

              <RailBlock title="记忆收件箱" hint="扫描出的候选要你点头，才会成为依据">
                {inbox.length === 0 ? (
                  <p className="text-[11px] leading-5 text-white/40">收件箱是空的。扫描之后，值得记的内容会出现在这里。</p>
                ) : (
                  <button type="button" onClick={() => setView("inbox")} className="flex w-full items-center justify-between gap-3 rounded-lg border border-amber-300/25 bg-amber-400/[0.06] px-3.5 py-3 text-left transition-colors hover:border-amber-300/45">
                    <span><span className="block text-sm font-medium text-amber-100">{inbox.length} 条待确认</span><span className="mt-0.5 block text-[11px] text-white/45">批准或驳回，逐条处理</span></span>
                    <ArrowRight className="size-4 shrink-0 text-amber-200/80" />
                  </button>
                )}
              </RailBlock>
            </div>
          </aside>

          <main className="min-w-0">
            {view === "inbox" ? (
              <section>
                <div className="mb-5 flex flex-col gap-3 border-b border-white/[0.14] pb-4 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div className="flex items-center gap-3">
                      <button type="button" onClick={() => setView("list")} className="inline-flex h-7 items-center border border-white/[0.12] px-2.5 text-[11px] text-white/60 hover:border-white/30 hover:text-white">返回记忆</button>
                      <h2 className="text-base font-medium text-white">记忆收件箱</h2>
                    </div>
                    <p className="mt-1.5 text-xs text-white/50">批准后进入当前依据；驳回即忽略。</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <input aria-label="批量批准理由" value={batchReason} onChange={(event) => setBatchReason(event.target.value)} placeholder="批量批准理由（可留空）" className="memory-input h-8 rounded-md px-3 text-xs sm:w-56" />
                    <button type="button" disabled={pending || selected.length === 0} onClick={() => run(async () => { const result = await batchApproveMemories(selected, batchReason || undefined); setSelected([]); setBatchReason(""); return `批准 ${result.approved.length} 条` + (result.skipped.length > 0 ? `，${result.skipped.length} 条不能批量（${result.skipped[0].why}）` : ""); })} className="btn-primary inline-flex h-8 items-center justify-center gap-2 rounded-md px-3.5 text-xs font-medium text-white disabled:opacity-35"><CheckCircle2 className="size-3.5" />批准（{selected.length}）</button>
                  </div>
                </div>
                {inbox.length === 0 ? <EmptyState icon={<Inbox className="size-5" />} title="收件箱是空的" text="点左栏「扫描新经历」，看看有没有值得记下来的内容。" /> : <div className="space-y-3">{inbox.map((row) => <CandidateCard key={row.proposal_id} row={row} pending={pending} checked={selected.includes(row.proposal_id)} onToggle={() => setSelected((was) => was.includes(row.proposal_id) ? was.filter((id) => id !== row.proposal_id) : [...was, row.proposal_id])} onApprove={() => run(async () => { const result = await decideProposal(row.proposal_id, { approved: true, reason: "收件箱里批准的" }); return `已批准提案 #${result.id}（${result.effect}）`; })} onReject={() => run(async () => { await decideProposal(row.proposal_id, { approved: false, reason: "这条不用记" }); return `已驳回提案 #${row.proposal_id}`; })} />)}</div>}
              </section>
            ) : (
              <section className="space-y-9">
                {showComposer && (
                  <section className="rounded-xl border border-white/[0.1] bg-white/[0.02] p-5">
                    <div className="mb-4 flex items-start justify-between gap-4">
                      <div><h3 className="text-sm font-medium text-white">手工补一条</h3><p className="mt-1 text-xs text-white/50">你确认过的事实，会成为 Agent 可以引用的上下文。</p></div>
                      <button type="button" onClick={() => setShowComposer(false)} aria-label="收起录入框" className="p-0.5 text-white/45 hover:text-white"><X className="size-4" /></button>
                    </div>
                    <form onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); run(async () => { const created = await createMemory({ scope: newScope, content: newContent, planId: newScope === "plan" ? (planId ?? undefined) : undefined, category: newScope === "global" ? newCategory : undefined, kind: newScope === "plan" ? newKind : undefined, reviewAt: newReviewAt || undefined, reason: newReason || undefined }); setNewContent(""); setNewReason(""); setShowComposer(false); return `已记下 #${created.id}（${created.scope_label}·${created.category_label ?? created.kind_label}）`; }); }} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(260px,1fr)_200px_200px_100px] lg:items-end">
                      <Field label="记什么（一两句话）" htmlFor="new-content"><textarea id="new-content" value={newContent} onChange={(event) => setNewContent(event.target.value)} rows={2} placeholder="例如：我周二晚上固定有课，那天别排任务" required className="memory-input min-h-16 w-full resize-y rounded-lg px-3.5 py-2.5 text-sm leading-5" /></Field>
                      <Field label="记在哪一级" htmlFor="new-scope"><HoverSelect value={newScope} onChange={(value) => setNewScope(value as MemoryScope)} options={[{ value: "global", label: "全局 · 所有计划都相关" }, { value: "plan", label: "计划内 · 当前计划" }]} /></Field>
                      {newScope === "global" ? <Field label="类别" htmlFor="new-category"><HoverSelect value={newCategory} onChange={(value) => setNewCategory(value as ProfileCategory)} options={CATEGORY_KEYS.map((key) => ({ value: key, label: PROFILE_CATEGORIES[key] }))} /></Field> : <Field label="这一条是什么" htmlFor="new-kind"><HoverSelect value={newKind} onChange={setNewKind} options={KIND_KEYS.map((key) => ({ value: key, label: MEMORY_KINDS[key] }))} /></Field>}
                      <button type="submit" disabled={pending || newContent.trim() === "" || (newScope === "plan" && planId === null)} className="btn-primary inline-flex h-9 items-center justify-center gap-2 rounded-md px-4 text-xs font-medium text-white disabled:opacity-35"><Check className="size-3.5" />{pending ? "保存中…" : "记下来"}</button>
                      <div className="sm:col-span-2 lg:col-span-4 grid gap-4 sm:grid-cols-2"><Field label="什么时候复核（可留空）" htmlFor="new-review"><input id="new-review" type="date" value={newReviewAt} onChange={(event) => setNewReviewAt(event.target.value)} className="memory-input w-full rounded-lg px-3.5 py-2.5 text-sm" /></Field><Field label="为什么记它（可留空）" htmlFor="new-reason"><input id="new-reason" value={newReason} onChange={(event) => setNewReason(event.target.value)} className="memory-input w-full rounded-lg px-3.5 py-2.5 text-sm" /></Field></div>
                    </form>
                  </section>
                )}

                {dueItems.length > 0 && (
                  <section>
                    <div className="flex items-baseline justify-between border-b border-amber-300/25 pb-2.5">
                      <h2 className="text-sm font-medium text-amber-100">待复核<span className="ml-2 text-xs font-normal text-white/45">{dueItems.length} 条已到期 · 默认不再当依据</span></h2>
                    </div>
                    <div className="mt-3 space-y-2.5">
                      {dueItems.map((item) => {
                        const rowKey = `${item.scope}-${item.id}`;
                        return (
                          <article key={rowKey} className="rounded-xl border border-amber-300/20 bg-amber-400/[0.04] px-4 py-3.5">
                            <div className="flex items-start justify-between gap-x-4 gap-y-2">
                              <div className="min-w-0">
                                <p className="break-words text-sm leading-5 text-white/90">{item.content}</p>
                                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-white/50"><span>#{item.id}</span><span>·</span><span>{item.scope_label}</span>{item.plan_id !== null && <><span>·</span><span>计划 #{item.plan_id}</span></>}<span>·</span><span>{item.category_label ?? item.kind_label}</span><span>·</span><span>{item.source_kind_label}</span></div>
                                <ReviewLine item={item} />
                              </div>
                              <div className="flex shrink-0 items-center gap-1.5">
                                <button type="button" disabled={pending} onClick={() => { setRenewOpen(renewOpen === rowKey ? null : rowKey); setRenewDate(defaultReviewDate()); }} className="inline-flex h-8 items-center gap-1 border border-white/[0.12] px-2.5 text-xs text-white/70 hover:border-white/25 hover:bg-white/[0.05] disabled:opacity-35"><RefreshCw className="size-3" />还作数</button>
                                <button type="button" disabled={pending} onClick={() => run(async () => { await reviewMemory({ memoryId: item.id, scope: item.scope, decision: "void", reason: "复核后确认不再作数" }); return `#${item.id} 已作废`; })} className="inline-flex h-8 items-center gap-1 border border-red-300/[0.12] px-2.5 text-xs text-red-200/70 hover:border-red-300/30 hover:bg-red-400/[0.06] disabled:opacity-35"><X className="size-3" />不再作数</button>
                                <button type="button" disabled={pending} onClick={() => { setPurging({ item, preview: null }); setPurgeReason(""); }} className="inline-flex h-8 items-center gap-1 border border-red-300/[0.1] px-2.5 text-xs text-red-200/55 hover:border-red-300/25 hover:bg-red-400/[0.06] disabled:opacity-35"><Trash2 className="size-3" />删除</button>
                              </div>
                            </div>
                            {renewOpen === rowKey && <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-amber-300/20 pt-3"><label htmlFor={`renew-${rowKey}`} className="text-xs text-amber-100/75">下次复核：</label><input id={`renew-${rowKey}`} type="date" value={renewDate} onChange={(event) => setRenewDate(event.target.value)} className="memory-input h-8 rounded-md px-3 text-xs" /><button type="button" disabled={pending || renewDate === ""} onClick={() => run(async () => { const result = await reviewMemory({ memoryId: item.id, scope: item.scope, decision: "renew", reviewAt: renewDate, reason: `复核过了，还作数，下次复核放到 ${renewDate}` }); setRenewOpen(null); return `#${item.id} 的复核时间推到了 ${result.memory?.review_at?.slice(0, 10) ?? renewDate}`; })} className="btn-primary h-8 rounded-md px-3 text-xs text-white disabled:opacity-35">确认</button><button type="button" onClick={() => setRenewOpen(null)} className="h-8 px-2 text-xs text-white/55 hover:text-white">取消</button><span className="text-[11px] text-white/50">默认往后推 {REVIEW_DAYS} 天，也可以自己改日期</span></div>}
                          </article>
                        );
                      })}
                    </div>
                  </section>
                )}

                {planId !== null && (
                  <MemorySection title={`计划内记忆 · #${planId}`} hint="只在这个计划里算数" items={planItems} pending={pending} onEdit={(item) => { setEditing(item); setEditContent(item.content); setEditReason(""); setVoiding(null); }} onVoid={(item) => { setVoiding(item); setVoidReason(""); setEditing(null); }} onPurge={(item) => { setPurging({ item, preview: null }); setPurgeReason(""); }} emptyText="这个计划还没有记忆。批准候选或手工补充后，会显示在这里。" />
                )}
                <MemorySection title="全局记忆" hint={planId === null ? "所有计划共用的依据" : "不受计划限制，哪里都用得上"} items={globalItems} pending={pending} onEdit={(item) => { setEditing(item); setEditContent(item.content); setEditReason(""); setVoiding(null); }} onVoid={(item) => { setVoiding(item); setVoidReason(""); setEditing(null); }} onPurge={(item) => { setPurging({ item, preview: null }); setPurgeReason(""); }} emptyText="还没有全局记忆。批准候选或手工补充后，会显示在这里。" />
              </section>
            )}
          </main>
        </div>

      {/* 改 = 台账「取代」：旧值留痕、编号换新 */}
      {editing !== null && (
        <Modal onClose={() => setEditing(null)}>
          <h2>改这条记忆</h2>
          <p className="text-xs text-white/50">
            改 = 台账「取代」：旧值留痕、内容换成新的，编号会变（历史永远查得到当时写过什么）。
          </p>
          <label style={{ fontSize: "12px" }}>新的内容</label>
          <textarea
            value={editContent}
            onChange={(event) => setEditContent(event.target.value)}
            rows={3}
            className="memory-input mt-2 min-h-24 w-full resize-y rounded-2xl px-3.5 py-3 text-sm leading-5"
          />
          <label className="mt-4 block text-xs text-white/55">为什么改（必填）</label>
          <input
            value={editReason}
            onChange={(event) => setEditReason(event.target.value)}
            className="memory-input mt-2 h-10 w-full rounded-xl px-3.5 text-sm"
          />
          <div className="mt-5 flex gap-2">
            <button
              type="button"
              className="btn-primary inline-flex h-9 items-center rounded-md px-4 text-xs text-white disabled:opacity-35"
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
            <button type="button" className="inline-flex h-9 items-center rounded-md border border-white/[0.1] px-4 text-xs text-white/55 hover:bg-white/[0.07]" onClick={() => setEditing(null)}>
              取消
            </button>
          </div>
        </Modal>
      )}

      {/* 作废：不再参与判断，旧值留痕 */}
      {voiding !== null && (
        <Modal onClose={() => setVoiding(null)}>
          <h2>作废这条记忆</h2>
          <p className="text-xs leading-5 text-white/50">
            作废之后它不再参与判断，旧值仍留在台账里（要连正文一起抹掉，用「彻底删除」）。
          </p>
          <div className="mt-3 rounded-2xl bg-red-400/[0.06] px-3.5 py-3 text-sm leading-5 text-white/75">「{voiding.content}」</div>
          <label className="mt-4 block text-xs text-white/55">为什么作废（必填）</label>
          <input
            value={voidReason}
            onChange={(event) => setVoidReason(event.target.value)}
            className="memory-input mt-2 h-10 w-full rounded-xl px-3.5 text-sm"
          />
          <div className="mt-5 flex gap-2">
            <button
              type="button"
              className="inline-flex h-9 items-center rounded-md bg-red-400/15 px-4 text-xs text-red-100 hover:bg-red-400/25 disabled:opacity-35"
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
            <button type="button" className="inline-flex h-9 items-center rounded-md border border-white/[0.1] px-4 text-xs text-white/55 hover:bg-white/[0.07]" onClick={() => setVoiding(null)}>
              取消
            </button>
          </div>
        </Modal>
      )}

      {/* 彻底删除：先预览、再确认 */}
      {purging !== null && (
        <Modal onClose={() => setPurging({ item: purging.item, preview: null })}>
          <h2>彻底删除（不可恢复）</h2>
          <div className="mt-3 rounded-2xl bg-red-400/[0.06] px-3.5 py-3 text-sm leading-5 text-white/75">「{purging.item.content}」</div>
          {purging.preview === null ? (
            <div className="mt-4">
              <p className="text-xs leading-5 text-white/50">
                先看一遍会影响哪些地方——这一步不会动任何数据。
              </p>
              <button
                type="button"
                className="inline-flex h-9 items-center rounded-md border border-white/[0.1] px-4 text-xs text-white/70 hover:bg-white/[0.07]"
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
            <div className="mt-4">
              <p className="text-xs leading-5 text-white/55">
                删除会清掉：这条记忆的正文、引用它的候选里的内容、来源证据摘录，以及来源里
                引用的那句原话（换成「
                已按用户要求删除」）。只剩一条不含内容的删除墓碑。
              </p>
              <p className="mt-3 text-xs leading-5 text-white/50">
                现在找到的副本（{purging.preview.copies.length} 处）：
                {purging.preview.copies.length === 0
                  ? "（没有别的副本）"
                  : purging.preview.copies
                      .map((copy) => `${copy.table}.${copy.column}#${copy.id}`)
                      .join("、")}
              </p>
              <label className="mt-4 block text-xs text-white/55">为什么删（可留空）</label>
              <input
                value={purgeReason}
                onChange={(event) => setPurgeReason(event.target.value)}
                className="memory-input mt-2 h-10 w-full rounded-xl px-3.5 text-sm"
              />
              <div className="mt-5 flex gap-2">
                <button
                  type="button"
                  className="inline-flex h-9 items-center rounded-md bg-red-400/15 px-4 text-xs text-red-100 hover:bg-red-400/25 disabled:opacity-35"
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
                  className="inline-flex h-9 items-center rounded-md border border-white/[0.1] px-4 text-xs text-white/55 hover:bg-white/[0.07]"
                  onClick={() => setPurging({ item: purging.item, preview: null })}
                >
                  返回
                </button>
                <button type="button" className="inline-flex h-9 items-center rounded-md border border-white/[0.1] px-4 text-xs text-white/55 hover:bg-white/[0.07]" onClick={() => setPurging(null)}>
                  取消
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
      </div>
    </div>
  );
}


/** 一段记忆列表（全局一段、计划内一段，同一个渲染）。 */
function Metric({ label, value, accent = "white" }: { label: string; value: number; accent?: "white" | "amber" }) {
  const color = accent === "amber" ? "text-amber-200" : "text-white";
  return <div><div className="text-[10px] uppercase tracking-[0.16em] text-white/50">{label}</div><div className={`mt-0.5 text-lg font-medium tracking-tight ${color}`}>{value}</div></div>;
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return <div className="space-y-2"><label htmlFor={htmlFor} className="block text-[11px] font-medium text-white/50">{label}</label>{children}</div>;
}

function EmptyState({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="flex min-h-44 flex-col items-center justify-center rounded-2xl border border-dashed border-white/[0.1] bg-white/[0.015] px-6 text-center"><div className="mb-3 flex size-10 items-center justify-center rounded-2xl bg-white/[0.06] text-white/55">{icon}</div><p className="text-sm text-white/75">{title}</p><p className="mt-1 text-xs text-white/50">{text}</p></div>;
}

function RailBlock({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="text-sm font-medium text-white/85">{title}</h3>
      <p className="mb-3 mt-0.5 text-[11px] leading-5 text-white/40">{hint}</p>
      {children}
    </section>
  );
}

/** 主栏的一节记忆：按类别/类型分组，组内每条一张卡——分明的边界代替原先的连续细线行。 */
function MemorySection({
  title,
  hint,
  items,
  pending,
  onEdit,
  onVoid,
  onPurge,
  emptyText,
}: {
  title: string;
  hint: string;
  items: MemoryItem[];
  pending: boolean;
  onEdit: (item: MemoryItem) => void;
  onVoid: (item: MemoryItem) => void;
  onPurge: (item: MemoryItem) => void;
  emptyText: string;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const groups = items.reduce<[string, MemoryItem[]][]>((result, item) => {
    const label = item.category_label ?? item.kind_label ?? "未分类";
    const group = result.find(([name]) => name === label);
    if (group) group[1].push(item);
    else result.push([label, [item]]);
    return result;
  }, []);

  return (
    <section className="min-w-0">
      <div className="flex items-baseline justify-between border-b border-white/[0.14] pb-2.5">
        <h2 className="text-sm font-medium text-white">{title}<span className="ml-2 text-xs font-normal text-white/45">{items.length}</span></h2>
        <span className="text-[11px] text-white/40">{hint}</span>
      </div>
      {items.length === 0 ? (
        <p className="py-7 text-xs leading-5 text-white/40">{emptyText}</p>
      ) : (
        groups.map(([groupLabel, groupItems], groupIndex) => (
          <section key={groupLabel} className={groupIndex === 0 ? "mt-5" : "mt-7"}>
            <div className="mb-3 flex items-center gap-2.5">
              <h3 className="text-xs font-medium text-white/70">{groupLabel}</h3>
              <span className="text-[11px] text-white/40">{groupItems.length}</span>
              <span className="h-px flex-1 bg-white/[0.07]" />
            </div>
            <div className="space-y-2.5">
              {groupItems.map((item) => {
                const rowKey = `${item.scope}-${item.id}`;
                const isLong = item.content.length > 64;
                const expanded = expandedKey === rowKey;
                return (
                  <article key={rowKey} className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-3.5 transition-colors hover:border-white/[0.16]">
                    <div className="flex items-start justify-between gap-x-4 gap-y-2">
                      <p className={`min-w-0 break-words text-sm leading-5 text-white/85 ${isLong && !expanded ? "line-clamp-2" : ""}`}>{item.content}</p>
                      <div className="flex shrink-0 items-center gap-1">
                        <button type="button" disabled={pending} onClick={() => onEdit(item)} className="inline-flex h-7 items-center border border-white/[0.1] px-2 text-xs text-white/55 hover:border-white/25 hover:text-white disabled:opacity-35">改</button>
                        <button type="button" disabled={pending} onClick={() => onVoid(item)} className="inline-flex h-7 items-center border border-red-300/[0.12] px-2 text-xs text-red-200/60 hover:border-red-300/30 hover:text-red-100 disabled:opacity-35">作废</button>
                        <button type="button" disabled={pending} onClick={() => onPurge(item)} className="inline-flex h-7 items-center border border-red-300/[0.1] px-2 text-xs text-red-200/50 hover:border-red-300/25 hover:text-red-100 disabled:opacity-35">删除</button>
                      </div>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-white/45"><span>#{item.id}</span><span>·</span><span>{item.source_kind_label}</span>{item.fact_time && <><span>·</span><span>事实 {item.fact_time.slice(0, 10)}</span></>}</div>
                    <ReviewLine item={item} />
                    {isLong && <button type="button" aria-expanded={expanded} onClick={() => setExpandedKey(expanded ? null : rowKey)} className="mt-1 px-0.5 py-0.5 text-[11px] text-white/55 hover:text-white/85">{expanded ? "收起全文" : "展开全文"}</button>}
                    {item.evidence.length > 0 && <details className="mt-1.5"><summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] text-green/90 hover:text-green"><ArrowRight className="size-3" />来源 {item.evidence.length}</summary><ul className="mt-2 space-y-1 border-l border-green/25 pl-3 text-[11px] leading-5 text-white/50">{item.evidence.map((evidence, index) => <li key={`${evidence.source_type}-${evidence.source_id}-${index}`}>{evidence.source_label}#{evidence.source_id}{evidence.source_time && `（${evidence.source_time.slice(0, 10)}）`}：「{evidence.excerpt}」</li>)}</ul></details>}
                  </article>
                );
              })}
            </div>
          </section>
        ))
      )}
    </section>
  );
}

/** 收件箱里的一行候选：把「为什么」和「依据」紧跟在陈述后面，动作固定在右侧。 */
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
    <article className="grid gap-3 border-b border-white/[0.06] py-4 first:pt-0 last:border-b-0 last:pb-1 lg:grid-cols-[minmax(0,1fr)_auto] lg:gap-6">
      <div className="min-w-0">
        <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-white/50">
          <span className="font-medium text-white/85">{row.action_label}</span>
          <span>{row.scope_label}</span>
          {row.category_label && <span>{row.category_label}</span>}
          {row.kind_label && <span>{row.kind_label}</span>}
          <span>{row.source_kind_label}</span>
          {row.review_at && <span className="text-amber-200/75">复核 {row.review_at.slice(0, 10)}</span>}
        </div>
        <p className="text-sm leading-5 text-white/85">{row.action === "review" ? `${row.decision_label ?? "处理"}：${row.target_content ?? ""}` : row.content}</p>
        {row.action === "supersede" && row.target_content && <p className="mt-1.5 border-l border-white/[0.12] pl-3 text-xs leading-5 text-white/50">取代旧的那条：「{row.target_content}」</p>}
        <p className="mt-1.5 text-xs leading-5 text-white/50"><span className="text-white/55">原因</span> {row.reason}</p>
        {row.duplicate_hint && <div className="mt-2 border-l border-amber-300/35 pl-3 text-xs leading-5 text-amber-100/75">{row.duplicate_hint}</div>}
        {row.evidence.length > 0 && <ul className="mt-2 space-y-1 border-l border-green/25 pl-3 text-[11px] leading-5 text-white/50">{row.evidence.map((evidence, index) => <li key={`${evidence.source_type}-${evidence.source_id}-${index}`}><span className="text-green/90">{evidence.source_label}#{evidence.source_id}</span>{evidence.source_time && `（${evidence.source_time.slice(0, 10)}）`}：「{evidence.excerpt}」</li>)}</ul>}
      </div>
      <div className="flex shrink-0 items-center gap-2 lg:items-start lg:pt-0.5">
        {row.batch_eligible && <label className="inline-flex h-7 items-center gap-1.5 text-[11px] text-white/50"><input type="checkbox" checked={checked} onChange={onToggle} className="size-3.5 accent-green" />批量</label>}
        <button type="button" disabled={pending} onClick={onApprove} className="inline-flex h-7 items-center gap-1 border border-white/[0.18] bg-white px-2.5 text-xs font-medium text-black hover:bg-white/85 disabled:opacity-35"><Check className="size-3" />批准</button>
        <button type="button" disabled={pending} onClick={onReject} className="inline-flex h-7 items-center gap-1 border border-white/[0.1] px-2.5 text-xs text-white/60 hover:bg-white/[0.07] hover:text-white disabled:opacity-35"><X className="size-3" />驳回</button>
      </div>
    </article>
  );
}

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 px-5 backdrop-blur-sm">
      <div className="card-border-gradient max-h-[88vh] w-full max-w-[620px] overflow-y-auto p-5 md:p-6">
        {children}
        <div className="mt-5 flex justify-end">
          <button type="button" onClick={onClose} className="inline-flex h-8 items-center rounded-md border border-white/[0.1] px-3 text-xs text-white/55 hover:bg-white/[0.07] hover:text-white">关掉</button>
        </div>
      </div>
    </div>
  );
}
