"use client";

// 工作台右栏：以阶段为章节的执行视图，目录与正文互相切换。
// 数据只读自 GET /api/plan，写动作复用现有计划接口；业务规则全在后端。
import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  ApiError,
  checkTask,
  closePlan,
  pausePlan,
  reopenNode,
  reopenPlan,
  voidPlan,
  getPlan,
  skipNode,
  submitDeliverable,
  updateNodeFields,
  type NodeFieldsInput,
  type PlanTree,
  type Stage,
} from "@/lib/api";
import { ArrowLeft, ArrowRight, Check, ChevronRight, List, Loader2, RefreshCw, Upload } from "lucide-react";
import { EASE_OUT, SPRING_PRESS } from "@/lib/ease";
import { useWorkspace } from "@/components/shell/workspace-context";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  not_started: "未开始",
  in_progress: "进行中",
  done: "已完成",
  stuck: "卡住",
  skipped: "已跳过",
};

const STATUS_DOT: Record<string, string> = {
  not_started: "bg-white/25",
  in_progress: "bg-white",
  done: "bg-green",
  stuck: "bg-red-400",
  skipped: "bg-white/30",
};

function stageDotClass(stage: Stage): string {
  const { settled, total } = stage.progress;
  const allSettled = total === 0 || settled === total;
  if (stage.status === "skipped") return "bg-white/30";
  if (stage.status === "stuck") return "bg-red-400";
  if (allSettled && stage.deliverable_submission) return "bg-green";
  if (allSettled && settled > 0) return "bg-amber-400";
  if (settled > 0 || stage.status === "in_progress" || stage.deliverable_submission) return "bg-white";
  return STATUS_DOT[stage.status] ?? "bg-white/25";
}

const CONTENT_OPEN = { type: "spring", duration: 0.58, bounce: 0.32 } as const;
const CONTENT_CLOSE = { type: "spring", duration: 0.46, bounce: 0.26 } as const;

function BouncyCollapse({
  open,
  children,
  className,
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion() ?? false;
  const innerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const measure = () => setHeight(el.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <motion.div
      initial={false}
      animate={{ height: open ? height : 0, opacity: open ? 1 : 0 }}
      transition={
        reduce
          ? { duration: 0 }
          : { height: open ? CONTENT_OPEN : CONTENT_CLOSE, opacity: { duration: 0.18, ease: EASE_OUT } }
      }
      aria-hidden={!open}
      inert={!open}
      className={cn("overflow-hidden", className)}
    >
      <div ref={innerRef}>{children}</div>
    </motion.div>
  );
}

const FIELD_INPUT =
  "w-full rounded-sm border border-white/[0.12] bg-black/30 px-2.5 py-1.5 text-[12px] text-primary placeholder-white/25 outline-none focus:border-white/35";

/** 详情面板里的小动作按钮（打勾 / 跳过 / 放回 / 编辑）。 */
function SmallAction({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="border-b border-white/20 py-1 text-[11px] text-white/60 transition-colors hover:border-white/70 hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
    >
      {children}
    </button>
  );
}

/**
 * 「写一句理由 → 确认」的行内小表单：跳过与放回共用。
 *
 * 两者都是裁定式动作（理由进台账），所以形状必须一致：输入框 + 确认 + 取消，
 * 理由没写就不让确认。`onSubmit` 失败时自己接住错误就地显示，成功由调用方收场。
 */
function ReasonPrompt({
  ariaLabel,
  placeholder,
  confirmLabel,
  tone = "neutral",
  onCancel,
  onSubmit,
}: {
  ariaLabel: string;
  placeholder: string;
  confirmLabel: string;
  tone?: "neutral" | "danger";
  onCancel: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const cleaned = reason.trim();
    if (!cleaned || busy) return;
    setBusy(true);
    setError(null);
    onSubmit(cleaned)
      .catch((err) => setError(err instanceof ApiError ? err.message : "操作失败，原因不明"))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <input
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className={cn(FIELD_INPUT, "min-w-0 flex-1 text-[11px]")}
      />
      <button
        type="button"
        onClick={submit}
        disabled={busy || !reason.trim()}
        className={cn(
          "rounded-sm px-2.5 py-1 text-[11px] font-medium disabled:opacity-40",
          tone === "danger" ? "bg-red-400/15 text-red-200" : "bg-white text-black",
        )}
      >
        {busy ? "…" : confirmLabel}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        className="text-[11px] text-white/50 hover:text-white/80 disabled:opacity-40"
      >
        取消
      </button>
      {error && (
        <p role="alert" className="w-full text-[11px] text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * 改节点字段的表单：**铺在展开的详情面板里**（原来挤在单行内、被压成一条，
 * 标题、日期、理由全展示不全）。只负责收集与提交，开关由调用方管。
 */
function NodeFieldsForm({
  node,
  withDeliverable,
  onCancel,
  onSubmit,
}: {
  node: { id: number; title: string; due_date: string | null; deliverable?: string | null };
  withDeliverable: boolean;
  onCancel: () => void;
  onSubmit: (input: NodeFieldsInput) => Promise<void>;
}) {
  const [title, setTitle] = useState(node.title);
  const [dueDate, setDueDate] = useState(node.due_date ?? "");
  const [deliverable, setDeliverable] = useState(node.deliverable ?? "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const canSave = Boolean(title.trim()) && Boolean(reason.trim()) && !saving;

  const save = () => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    const input: NodeFieldsInput = {
      title: title.trim(),
      due_date: dueDate,
      reason: reason.trim(),
    };
    if (withDeliverable) input.deliverable = deliverable.trim();
    onSubmit(input)
      .catch((err) => setError(err instanceof ApiError ? err.message : "保存字段失败"))
      .finally(() => setSaving(false));
  };

  return (
    <div className="mt-3 border-l border-white/25 bg-black/20 p-3" onClick={(event) => event.stopPropagation()}>
      <div className="flex flex-col gap-2">
        <label className="block">
          <span className="mb-1 block text-[10px] text-white/40">标题</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} className={FIELD_INPUT} />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] text-white/40">截止日（留空 = 不带日期，不算落后）</span>
          <input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} className={FIELD_INPUT} />
        </label>
        {withDeliverable && (
          <label className="block">
            <span className="mb-1 block text-[10px] text-white/40">阶段交付物（留空清除）</span>
            <input value={deliverable} onChange={(event) => setDeliverable(event.target.value)} className={FIELD_INPUT} />
          </label>
        )}
        <label className="block">
          <span className="mb-1 block text-[10px] text-white/40">修改理由（必填，进台账）</span>
          <input value={reason} onChange={(event) => setReason(event.target.value)} className={FIELD_INPUT} />
        </label>
        {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="inline-flex items-center gap-1 rounded-sm bg-white px-2.5 py-1 text-[11px] font-medium text-black disabled:opacity-40"
          >
            {saving && <Loader2 className="h-3 w-3 animate-spin" />}
            保存
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="text-[11px] text-white/50 hover:text-white/80 disabled:opacity-40"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

function StageChapter({
  planId,
  stage,
  index,
  total,
  isCurrent,
  onChanged,
}: {
  planId: number;
  stage: Stage;
  index: number;
  total: number;
  isCurrent: boolean;
  onChanged: () => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [skipOpen, setSkipOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneNote, setDoneNote] = useState<string | null>(null);
  const settled = stage.status === "done" || stage.status === "skipped";

  const canSubmit = url.trim() !== "" && note.trim() !== "" && !busy;

  const submit = () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    submitDeliverable(stage.id, url.trim(), note.trim())
      .then(() => {
        setDoneNote("交付物已提交");
        setFormOpen(false);
        setUrl("");
        setNote("");
        onChanged();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "提交失败，原因不明"))
      .finally(() => setBusy(false));
  };

  return (
    <article aria-label={`阶段 ${index + 1}：${stage.title}`}>
      <div className="border-b border-white/[0.08] px-6 pb-7 pt-7">
        <div className="mb-5 flex items-end justify-between text-[10px] tracking-[0.22em] text-white/45">
          <span>{isCurrent ? "当前阶段 / NOW" : "计划章节 / STAGE"}</span>
          <span className="font-mono tabular-nums">{String(index + 1).padStart(2, "0")} / {String(total).padStart(2, "0")}</span>
        </div>
        <h2 className="max-w-[22ch] text-[23px] font-medium leading-[1.28] tracking-tight text-primary">{stage.title}</h2>
        <div className="mt-5 flex items-center justify-between text-[11px] text-white/55">
          <span className="flex items-center gap-2"><span className={cn("size-1.5 rounded-full", stageDotClass(stage))} />{stage.finished ? "阶段已兑现" : STATUS_LABELS[stage.status] ?? stage.status}</span>
          <span className="font-mono tabular-nums">任务 {stage.progress.settled} / {stage.progress.total}</span>
        </div>
        <div className="mt-3 flex h-px bg-white/10" aria-hidden="true">
          <div className="h-full bg-white/70" style={{ width: `${stage.progress.total ? (stage.progress.settled / stage.progress.total) * 100 : 0}%` }} />
        </div>
      </div>

      <section className="border-b border-white/[0.08] px-6 py-6" aria-label="阶段交付物">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-[10px] font-medium tracking-[0.2em] text-white/45">01 / 交付物</h3>
          <button type="button" onClick={() => { setFormOpen(!formOpen); setDoneNote(null); setError(null); }} aria-expanded={formOpen} className="inline-flex items-center gap-1.5 text-[11px] text-primary/80 underline decoration-white/25 underline-offset-4 transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white">
            <Upload className="size-3" />{formOpen ? "返回交付物" : stage.deliverable_submission ? "重新提交" : "提交交付物"}
          </button>
        </div>
        {formOpen ? <div className="flex flex-col gap-3">
          <label className="text-[11px] text-white/55">交付物链接<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="粘贴链接" className={cn(FIELD_INPUT, "mt-1.5 rounded-sm")} /></label>
          <label className="text-[11px] text-white/55">一句话说明<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="这次交付了什么" className={cn(FIELD_INPUT, "mt-1.5 rounded-sm")} /></label>
          {error && <p className="text-[11px] text-red-300">{error}</p>}
          <div className="flex items-center gap-2">
            <button type="button" onClick={submit} disabled={!canSubmit} className="inline-flex items-center gap-1 rounded-sm bg-white px-3 py-1.5 text-[11px] font-medium text-black disabled:opacity-40">
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
              提交
            </button>
            <button type="button" onClick={() => setFormOpen(false)} className="text-[11px] text-white/50 hover:text-white/80">取消</button>
          </div>
        </div> : <div className="border-l border-white/25 pl-4">
          <p className="text-[13px] leading-relaxed text-primary/80">{stage.deliverable || "这个阶段还没有写明交付物"}</p>
          {stage.deliverable_submission && (/^https?:\/\//i.test(stage.deliverable_submission.url)
            ? <a href={stage.deliverable_submission.url} target="_blank" rel="noopener noreferrer" className="mt-3 block break-all text-[11px] text-green/80 underline decoration-green/30 underline-offset-4 hover:text-green">已提交 · {stage.deliverable_submission.url}</a>
            : <p className="mt-3 break-all text-[11px] text-green/80">已提交 · {stage.deliverable_submission.url}</p>)}
          {doneNote && <p role="status" className="mt-2 text-[11px] text-green/90">{doneNote}</p>}
        </div>}
      </section>

      <section className="border-b border-white/[0.08] px-6 py-6" aria-label="阶段任务">
        <h3 className="mb-3 text-[10px] font-medium tracking-[0.2em] text-white/45">02 / 执行清单</h3>
        {stage.tasks.length > 0 ? (
          <ul className="divide-y divide-white/[0.06]">
            {stage.tasks.map((task) => <TaskRow key={task.id} task={task} onChanged={onChanged} />)}
          </ul>
        ) : (
          <p className="text-[12px] text-white/50">这个阶段还没有任务</p>
        )}
      </section>
      {stage.checkpoints.length > 0 && (
        <section className="border-b border-white/[0.08] px-6 py-6" aria-label="周检查点">
          <h3 className="mb-3 text-[10px] font-medium tracking-[0.2em] text-white/45">03 / 周检查点</h3>
          <ul className="divide-y divide-white/[0.06]">
            {stage.checkpoints.map((checkpoint) => (
              <li key={checkpoint.id} className="flex items-center gap-2 py-2.5 text-[11px] text-white/65">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATUS_DOT[checkpoint.status] ?? "bg-white/25")} />
                <span className="min-w-0 flex-1">{checkpoint.title}</span>
                <span className="shrink-0 text-[10px] text-white/35">{checkpoint.due_date ?? STATUS_LABELS[checkpoint.status]}</span>
                <Link
                  href={`/report?plan_id=${planId}&node_id=${checkpoint.id}`}
                  title={`为「${checkpoint.title}」提交报告`}
                  aria-label={`为「${checkpoint.title}」提交报告`}
                  className="shrink-0 text-[10px] text-white/65 underline decoration-white/25 underline-offset-4 hover:text-primary"
                >
                  报告
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="px-6 py-6">
        <p className="mb-3 text-[10px] tracking-[0.2em] text-white/40">阶段操作</p>
        <div className="flex flex-wrap items-center gap-1.5">
            {!settled && <SmallAction onClick={() => setSkipOpen(!skipOpen)}>跳过这个阶段</SmallAction>}
            {settled && <SmallAction onClick={() => setReopenOpen(!reopenOpen)}>放回未完成</SmallAction>}
            <SmallAction onClick={() => setEditOpen(!editOpen)}>编辑这个阶段</SmallAction>
        </div>
          {skipOpen && (
            <ReasonPrompt
              ariaLabel={`跳过「${stage.title}」的理由`}
              placeholder="为什么跳过（进台账，必填）"
              confirmLabel="确认跳过"
              tone="danger"
              onCancel={() => setSkipOpen(false)}
              onSubmit={async (reason) => {
                await skipNode(stage.id, reason);
                setSkipOpen(false);
                onChanged();
              }}
            />
          )}
          {reopenOpen && (
            <ReasonPrompt
              ariaLabel={`放回「${stage.title}」的理由`}
              placeholder="为什么放回来（进台账，必填）"
              confirmLabel="确认放回"
              onCancel={() => setReopenOpen(false)}
              onSubmit={async (reason) => {
                await reopenNode(stage.id, reason);
                setReopenOpen(false);
                onChanged();
              }}
            />
          )}
          {editOpen && (
            <NodeFieldsForm
              node={stage}
              withDeliverable
              onCancel={() => setEditOpen(false)}
              onSubmit={async (input) => {
                await updateNodeFields(stage.id, input);
                setEditOpen(false);
                onChanged();
              }}
            />
          )}
      </div>
    </article>
  );
}

function TaskRow({
  task,
  onChanged,
}: {
  task: Stage["tasks"][number];
  onChanged: () => void;
}) {
  const reduce = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [skipOpen, setSkipOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = task.status === "done";
  const skipped = task.status === "skipped";
  const settled = done || skipped;
  const lag = task.lag_days ?? 0;

  // 打勾是一步到完成：先在本行给出「确认完成 / 取消」，不点确认就不落库
  const check = () => {
    if (busy || settled) return;
    setBusy(true);
    setError(null);
    checkTask(task.id)
      .then(() => {
        setConfirming(false);
        onChanged();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "打勾失败"))
      .finally(() => setBusy(false));
  };

  return (
    <li className="flex flex-col py-2.5">
      <div className="flex items-start gap-3">
        <motion.button
          type="button"
          role="checkbox"
          aria-checked={done}
          aria-label={done ? "已完成" : "打勾完成"}
          title={skipped ? "已跳过" : done ? "已完成" : "打勾完成（会先让你确认）"}
          disabled={settled || busy}
          onClick={() => setConfirming(!confirming)}
          whileTap={reduce || settled || busy ? undefined : { scale: 0.88 }}
          transition={SPRING_PRESS}
          className={cn(
            "mt-0.5 grid size-4 shrink-0 place-items-center rounded-sm border outline-none transition-colors duration-200",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            "disabled:cursor-not-allowed disabled:opacity-60",
            done ? "border-green bg-green text-background" : skipped ? "cursor-default border-white/15" : "border-white/30 bg-transparent hover:border-white/60 hover:bg-white/[0.04]",
          )}
        >
          <AnimatePresence initial={false}>
            {done ? (
              <motion.svg key="check" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round" initial={reduce ? { opacity: 1 } : { opacity: 0, scale: 0.5 }} animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1 }} exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5, filter: "blur(4px)" }} transition={reduce ? { duration: 0 } : { duration: 0.16, ease: EASE_OUT }} aria-hidden="true">
                <motion.path d="M5 13l4 4L19 7" initial={reduce ? { pathLength: 1 } : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={reduce ? { duration: 0 } : { duration: 0.3, ease: EASE_OUT, delay: 0.04 }} />
              </motion.svg>
            ) : null}
          </AnimatePresence>
        </motion.button>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-label={`${open ? "收起" : "展开"}「${task.title}」`}
          className="flex min-w-0 flex-1 items-center text-left outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <span className={cn("min-w-0 flex-1 text-[12px] leading-relaxed text-primary/75 transition-colors duration-200", done && "text-white/40 line-through decoration-white/30", !open && "line-clamp-2")}>{task.title}</span>
        </button>
        <ChevronRight className={cn("mt-0.5 size-3 shrink-0 text-white/35 transition-transform", open && "rotate-90")} aria-hidden="true" />
      </div>

      {confirming && !settled && (
        <div className="mt-2 flex flex-wrap items-center gap-2 pl-7">
          <span className="text-[11px] text-white/55">标记为已完成？</span>
          <button
            type="button"
            onClick={check}
            disabled={busy}
            className="rounded-sm bg-white px-2.5 py-1 text-[11px] font-medium text-black disabled:opacity-40"
          >
            {busy ? "…" : "确认完成"}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            disabled={busy}
            className="text-[11px] text-white/50 hover:text-white/80 disabled:opacity-40"
          >
            取消
          </button>
        </div>
      )}
      {error && <p className="pl-7 text-[10px] text-red-300">{error}</p>}

      <BouncyCollapse open={open} className="pl-7">
        <div className="pb-1 pt-3">
          <p className="text-[11px] leading-relaxed text-white/55">
            {STATUS_LABELS[task.status] ?? task.status}
            {task.due_date ? ` · 截止 ${task.due_date}` : " · 没定截止日"}
            {!skipped && lag > 0 ? ` · 落后 ${lag} 天` : ""}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {!settled && <SmallAction onClick={() => setConfirming(true)}>打勾完成</SmallAction>}
            {settled && <SmallAction onClick={() => setReopenOpen(!reopenOpen)}>放回未完成</SmallAction>}
            {!settled && <SmallAction onClick={() => setSkipOpen(!skipOpen)}>跳过</SmallAction>}
            <SmallAction onClick={() => setEditOpen(!editOpen)}>编辑标题与截止日</SmallAction>
          </div>
          {skipOpen && (
            <ReasonPrompt
              ariaLabel={`跳过「${task.title}」的理由`}
              placeholder="为什么跳过（进台账，必填）"
              confirmLabel="确认跳过"
              tone="danger"
              onCancel={() => setSkipOpen(false)}
              onSubmit={async (reason) => {
                await skipNode(task.id, reason);
                setSkipOpen(false);
                onChanged();
              }}
            />
          )}
          {reopenOpen && (
            <ReasonPrompt
              ariaLabel={`放回「${task.title}」的理由`}
              placeholder="为什么放回来（进台账，必填）"
              confirmLabel="确认放回"
              onCancel={() => setReopenOpen(false)}
              onSubmit={async (reason) => {
                await reopenNode(task.id, reason);
                setReopenOpen(false);
                onChanged();
              }}
            />
          )}
          {editOpen && (
            <NodeFieldsForm
              node={task}
              withDeliverable={false}
              onCancel={() => setEditOpen(false)}
              onSubmit={async (input) => {
                await updateNodeFields(task.id, input);
                setEditOpen(false);
                onChanged();
              }}
            />
          )}
        </div>
      </BouncyCollapse>
    </li>
  );
}

function PlanManagement({ planId, onChanged }: { planId: number; onChanged: () => void }) {
  const { plans, refreshPlans } = useWorkspace();
  const plan = plans.find((item) => item.id === planId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setVoiding(false);
      setReason("");
      await refreshPlans();
      onChanged();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "计划操作失败");
    } finally {
      setBusy(false);
    }
  }
  if (!plan) return null;
  const labels: Record<string, string> = { active: "进行中", paused: "暂时不做", closed: "已做完", void: "已作废" };
  const buttonClass = "border border-white/15 px-2.5 py-1.5 text-[11px] text-primary transition-colors hover:bg-white/[0.06] disabled:opacity-40";
  return (
    <details className="border-t border-white/[0.08] px-6 py-5 text-[11px] text-muted">
      <summary className="cursor-pointer text-primary/70 marker:text-white/40">计划管理 <span className="ml-2 text-white/40">{labels[plan.status] ?? plan.status}</span></summary>
      <p className="my-2">暂停可继续，收尾可重开；作废不可恢复。</p>
      {plan.ended_reason && <p className="mb-2">原因：{plan.ended_reason}</p>}
      <div className="flex flex-wrap gap-2">
        {plan.status === "active" && <button className={buttonClass} disabled={busy} onClick={() => void run(() => pausePlan(planId))}>暂停</button>}
        {(plan.status === "active" || plan.status === "paused") && <button className={buttonClass} disabled={busy} onClick={() => void run(() => closePlan(planId))}>收尾</button>}
        {(plan.status === "paused" || plan.status === "closed") && <button className={buttonClass} disabled={busy} onClick={() => void run(() => reopenPlan(planId))}>{plan.status === "paused" ? "继续做" : "重开"}</button>}
        {plan.status === "active" && !voiding && <button className={buttonClass} disabled={busy} onClick={() => setVoiding(true)}>作废</button>}
      </div>
      {voiding && <div className="mt-2 flex flex-wrap gap-2">
        <input aria-label="作废计划的理由" placeholder="作废理由（必填）" value={reason} onChange={(event) => setReason(event.target.value)} className="min-w-0 w-full rounded-lg border border-white/10 bg-transparent px-2 py-1 text-primary" />
        <button className={buttonClass} disabled={busy || !reason.trim()} onClick={() => void run(() => voidPlan(planId, reason.trim()))}>确认作废</button>
        <button className={buttonClass} disabled={busy} onClick={() => setVoiding(false)}>取消</button>
      </div>}
      {error && <p role="alert" className="mt-2 text-red-300">{error}</p>}
    </details>
  );
}

export function PlanTreePanel({
  planId,
  version,
}: {
  planId: number;
  version: number;
}) {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStageId, setSelectedStageId] = useState<number | null>(null);
  const [showIndex, setShowIndex] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    return getPlan(planId)
      .then((data) => {
        setTree(data);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "读取计划失败"))
      .finally(() => setLoading(false));
  }, [planId]);

  useEffect(() => {
    Promise.resolve().then(() => setLoading(true));
    load();
  }, [load, version]);

  const stages = tree?.stages ?? [];
  const currentId = tree?.current_stage?.id ?? null;
  const fallbackIndex = stages.findIndex((stage) => stage.id === currentId || (currentId === null && !stage.finished));
  const chosenIndex = stages.findIndex((stage) => stage.id === selectedStageId);
  const selectedIndex = Math.max(0, chosenIndex >= 0
    ? chosenIndex
    : fallbackIndex >= 0 ? fallbackIndex : stages.length - 1);
  const selectedStage = stages[selectedIndex];
  const selectStage = (id: number) => {
    setSelectedStageId(id);
    setShowIndex(false);
    scrollRef.current?.scrollTo({ top: 0 });
  };
  const toggleIndex = () => {
    setShowIndex((open) => !open);
    scrollRef.current?.scrollTo({ top: 0 });
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-white/[0.015]">
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.08] px-6 py-4">
        <span className="text-[10px] font-medium tracking-[0.24em] text-white/50">执行案卷 / WORKBENCH</span>
        <button type="button" onClick={() => { setLoading(true); load(); }} aria-label="刷新计划" className="p-1 text-white/50 transition-colors hover:text-white focus-visible:outline-2 focus-visible:outline-white">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p role="alert" className="px-6 py-5 text-[12px] text-red-300">{error}</p>
        ) : loading && !tree ? (
          <p className="px-6 py-5 text-[12px] text-white/50">读取计划中…</p>
        ) : tree?.plan ? (
          <>
            <div className="border-b border-white/[0.08] px-6 py-5">
              <p className="mb-2 text-[10px] tracking-[0.2em] text-white/40">计划目标</p>
              <p className="line-clamp-2 text-[13px] leading-relaxed text-white/75" title={tree.plan.goal}>{tree.plan.goal}</p>
              <div className="mt-5 flex items-center justify-between border-t border-white/[0.06] pt-4">
                <button type="button" onClick={toggleIndex} aria-expanded={showIndex} className="inline-flex items-center gap-2 text-[12px] text-primary/85 transition-colors hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white">
                  {showIndex ? <ArrowLeft className="size-3.5" /> : <List className="size-3.5" />}
                  {showIndex ? "返回章节" : `阶段目录 · ${stages.length}`}
                </button>
                {!showIndex && currentId && selectedStage?.id !== currentId && <button type="button" onClick={() => selectStage(currentId)} className="text-[11px] text-white/55 underline decoration-white/25 underline-offset-4 hover:text-white">回到当前</button>}
              </div>
            </div>
            {showIndex ? (
              <div aria-label="阶段目录">
                <div className="px-6 pb-5 pt-8">
                  <p className="text-[10px] tracking-[0.2em] text-white/40">CONTENTS / 计划全貌</p>
                  <h2 className="mt-2 text-[24px] font-medium tracking-tight text-primary">阶段目录</h2>
                  <p className="mt-2 text-[12px] leading-relaxed text-white/50">选择一章，右侧直接切换到该阶段的执行内容。</p>
                </div>
                <ol className="border-t border-white/[0.08]">
                  {stages.map((stage, index) => (
                    <li key={stage.id}>
                      <button type="button" onClick={() => selectStage(stage.id)} aria-current={selectedStage?.id === stage.id ? "page" : undefined} className={cn("group flex w-full items-start gap-4 border-b border-white/[0.08] px-6 py-4 text-left transition-colors hover:bg-white/[0.04] focus-visible:outline-2 focus-visible:outline-white", selectedStage?.id === stage.id && "bg-white/[0.035]")}>
                        <span className={cn("pt-0.5 font-mono text-[12px] tabular-nums", selectedStage?.id === stage.id ? "text-primary" : "text-white/35")}>{String(index + 1).padStart(2, "0")}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[13px] leading-snug text-primary/80 group-hover:text-primary">{stage.title}</span>
                          <span className="mt-1.5 block text-[10px] text-white/45">{stage.id === currentId ? "当前阶段 · " : ""}{stage.finished ? "已兑现" : STATUS_LABELS[stage.status] ?? stage.status} · {stage.progress.settled}/{stage.progress.total}</span>
                        </span>
                        <ChevronRight className="mt-1 size-3.5 shrink-0 text-white/25 group-hover:text-white/70" aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ol>
                <PlanManagement key={planId} planId={planId} onChanged={load} />
              </div>
            ) : selectedStage ? (
              <>
                <StageChapter key={selectedStage.id} planId={planId} stage={selectedStage} index={selectedIndex} total={stages.length} isCurrent={selectedStage.id === currentId} onChanged={load} />
                {tree.lag.behind && tree.behind_reason && <p className="border-b border-white/[0.08] px-6 py-5 text-[12px] leading-relaxed text-amber-400/90">节奏提醒 / {tree.behind_reason}</p>}
                <nav aria-label="相邻阶段" className="flex items-center justify-between px-6 py-6 text-[11px]">
                  {selectedIndex > 0 ? <button type="button" onClick={() => selectStage(stages[selectedIndex - 1].id)} className="inline-flex items-center gap-1.5 text-white/55 hover:text-white"><ArrowLeft className="size-3" />上一阶段</button> : <span />}
                  {selectedIndex < stages.length - 1 && <button type="button" onClick={() => selectStage(stages[selectedIndex + 1].id)} className="inline-flex items-center gap-1.5 text-white/55 hover:text-white">下一阶段<ArrowRight className="size-3" /></button>}
                </nav>
              </>
            ) : <p className="px-6 py-8 text-[12px] text-white/50">还没有阶段。可以在对话中规划下一步。</p>}
          </>
        ) : (
          <p className="px-6 py-5 text-[12px] text-white/50">没有计划</p>
        )}
      </div>
    </div>
  );
}
