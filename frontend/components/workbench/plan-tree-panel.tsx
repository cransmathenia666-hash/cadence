"use client";

// 工作台右栏：当前计划的树。阶段可展开看任务与交付物，阶段旁直接提交交付物。
// 数据只读自 GET /api/plan，提交走 POST /api/plan/nodes/{id}/deliverable；业务规则全在后端。
// 展开/收起动效参考 beui.dev/components/motion/bouncy-accordion：测高 + 弹簧，开更弹、收更收敛。
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  ApiError,
  checkTask,
  getPlan,
  submitDeliverable,
  type PlanTree,
  type Stage,
} from "@/lib/api";
import {
  Check,
  ChevronRight,
  Loader2,
  RefreshCw,
  Upload,
} from "lucide-react";
import { EASE_OUT, SPRING_PRESS } from "@/lib/ease";
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

// 阶段点的口径与后端完成判定一致（决策 30）：任务全收尾 + 交付物已交才算完。
// 阶段状态字段没有自动推进路径（任务打勾只写任务、交交付物不写阶段），
// 照抄 status 会永远停在「未开始」——所以点在前端按进度算：
// 绿 = 完成；黄 = 任务满了只差交付物；白 = 动过了；灰 = 还没动。
function stageDotClass(stage: Stage): string {
  const { settled, total } = stage.progress;
  const allSettled = total === 0 || settled === total;
  if (stage.status === "skipped") return "bg-white/30";
  if (stage.status === "stuck") return "bg-red-400";
  if (allSettled && stage.deliverable_submission) return "bg-green";
  if (allSettled && settled > 0) return "bg-amber-400";
  if (settled > 0 || stage.status === "in_progress" || stage.deliverable_submission) {
    return "bg-white";
  }
  return STATUS_DOT[stage.status] ?? "bg-white/25";
}

const CONTENT_OPEN = { type: "spring", duration: 0.58, bounce: 0.32 } as const;
const CONTENT_CLOSE = { type: "spring", duration: 0.46, bounce: 0.26 } as const;
const CHEVRON = { type: "spring", duration: 0.42, bounce: 0.28 } as const;

// 测高弹簧折叠：内容自然高度靠 ResizeObserver 跟住，避免 auto 高度弹簧的失真。
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
          : {
              height: open ? CONTENT_OPEN : CONTENT_CLOSE,
              opacity: { duration: 0.18, ease: EASE_OUT },
            }
      }
      aria-hidden={!open}
      inert={!open}
      className={cn("overflow-hidden", className)}
    >
      <div ref={innerRef}>{children}</div>
    </motion.div>
  );
}

function StageRow({
  stage,
  onChanged,
}: {
  stage: Stage;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneNote, setDoneNote] = useState<string | null>(null);
  const reduce = useReducedMotion() ?? false;

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
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "提交失败，原因不明"),
      )
      .finally(() => setBusy(false));
  };

  return (
    <li className="rounded-xl bg-white/[0.02] hover:bg-white/[0.04] transition-colors">
      <div className="flex items-center gap-2 px-2.5 py-2">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg text-left outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <motion.span
            aria-hidden="true"
            animate={{ rotate: open ? 90 : 0 }}
            transition={reduce ? { duration: 0 } : CHEVRON}
            className="shrink-0 text-white/50"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </motion.span>
          <span
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              stageDotClass(stage),
            )}
          />
          <span className="min-w-0 flex-1 truncate text-[13px] text-primary/85">
            {stage.title}
          </span>
          <span className="shrink-0 text-[11px] tabular-nums text-white/50">
            {stage.progress.settled}/{stage.progress.total}
          </span>
        </button>
        <button
          type="button"
          onClick={() => {
            setFormOpen(!formOpen);
            setDoneNote(null);
            setError(null);
          }}
          title="提交交付物"
          className="shrink-0 rounded-full border border-white/10 p-1.5 text-white/60 transition-colors hover:bg-white/[0.08] hover:text-primary disabled:opacity-40"
        >
          {formOpen ? (
            <Loader2 className="h-3 w-3 animate-spin text-white/50" />
          ) : (
            <Upload className="h-3 w-3" />
          )}
        </button>
      </div>

      <BouncyCollapse open={formOpen} className="mx-2.5">
        <div className="mb-2 rounded-lg border border-white/[0.06] bg-black/30 p-2.5 flex flex-col gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="交付物链接（必填）"
            className="w-full rounded-lg border border-white/[0.08] bg-transparent px-2.5 py-1.5 text-[12px] text-primary placeholder-white/25 outline-none focus:border-white/25"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="一句话说明（必填）"
            className="w-full rounded-lg border border-white/[0.08] bg-transparent px-2.5 py-1.5 text-[12px] text-primary placeholder-white/25 outline-none focus:border-white/25"
          />
          {error && <p className="text-[11px] text-red-300">{error}</p>}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-[11px] font-medium text-black disabled:opacity-40"
            >
              {busy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Check className="h-3 w-3" />
              )}
              提交
            </button>
            <button
              type="button"
              onClick={() => setFormOpen(false)}
              className="text-[11px] text-white/50 hover:text-white/80"
            >
              取消
            </button>
          </div>
        </div>
      </BouncyCollapse>

      {doneNote && !formOpen && (
        <p className="mb-1.5 px-2.5 text-[11px] text-green/90">
          {doneNote}
        </p>
      )}

      <BouncyCollapse open={open} className="mx-2.5">
        <div className="mb-2 border-t border-white/[0.05] pt-2">
          {stage.deliverable && (
            <p className="mb-1.5 text-[11px] leading-relaxed text-white/50">
              要交的：{stage.deliverable}
            </p>
          )}
          {stage.deliverable_submission && (
            <p className="mb-1.5 truncate text-[11px] text-green/70">
              已交：{stage.deliverable_submission.url}
            </p>
          )}
          {stage.tasks.length > 0 ? (
            <ul className="flex flex-col gap-0.5">
              {stage.tasks.map((task) => (
                <TaskRow key={task.id} task={task} onChanged={onChanged} />
              ))}
            </ul>
          ) : (
            <p className="pl-5 text-[11px] text-white/50">这个阶段还没有任务</p>
          )}
        </div>
      </BouncyCollapse>
    </li>
  );
}

// 任务行：空圈点击打勾 → done（取消勾没有接口，已完成的不可再点；跳过的保持原样）。
// 勾选钮的质感对齐 motion/checkbox 样张：按压回弹、勾是画线动画、退出模糊缩小。
const CHECK_PATH = "M5 13l4 4L19 7";

function TaskRow({
  task,
  onChanged,
}: {
  task: Stage["tasks"][number];
  onChanged: () => void;
}) {
  const reduce = useReducedMotion() ?? false;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = task.status === "done";
  const skipped = task.status === "skipped";

  const check = () => {
    if (busy || done || skipped) return;
    setBusy(true);
    setError(null);
    checkTask(task.id)
      .then(() => onChanged())
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "打勾失败"),
      )
      .finally(() => setBusy(false));
  };

  return (
    <li className="flex flex-col">
      <div className="flex items-center gap-2 rounded-lg py-0.5 pl-5 pr-1 transition-colors hover:bg-white/[0.03]">
        <motion.button
          type="button"
          role="checkbox"
          aria-checked={done}
          aria-label={done ? "已完成" : "打勾完成"}
          title={skipped ? "已跳过" : done ? "已完成" : "打勾完成"}
          disabled={done || skipped || busy}
          onClick={check}
          whileTap={reduce || done || skipped || busy ? undefined : { scale: 0.88 }}
          transition={SPRING_PRESS}
          className={cn(
            "grid size-4 shrink-0 place-items-center rounded-full border outline-none transition-colors duration-200",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            "disabled:cursor-not-allowed disabled:opacity-60",
            done
              ? "border-green bg-green text-background"
              : skipped
                ? "cursor-default border-white/15"
                : "border-white/30 bg-transparent hover:border-white/60 hover:bg-white/[0.04]",
          )}
        >
          <AnimatePresence initial={false}>
            {done ? (
              <motion.svg
                key="check"
                width="10"
                height="10"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={3.5}
                strokeLinecap="round"
                strokeLinejoin="round"
                initial={reduce ? { opacity: 1 } : { opacity: 0, scale: 0.5 }}
                animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5, filter: "blur(4px)" }}
                transition={reduce ? { duration: 0 } : { duration: 0.16, ease: EASE_OUT }}
                aria-hidden="true"
              >
                <motion.path
                  d={CHECK_PATH}
                  initial={reduce ? { pathLength: 1 } : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={
                    reduce ? { duration: 0 } : { duration: 0.3, ease: EASE_OUT, delay: 0.04 }
                  }
                />
              </motion.svg>
            ) : null}
          </AnimatePresence>
        </motion.button>
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[12px] text-white/65 transition-colors duration-200",
            done && "text-white/40 line-through decoration-white/30",
          )}
        >
          {task.title}
        </span>
        <span className="shrink-0 text-[10px] text-white/50">
          {STATUS_LABELS[task.status] ?? task.status}
          {task.due_date ? ` · ${task.due_date}` : ""}
        </span>
      </div>
      {error && <p className="pl-11 text-[10px] text-red-300">{error}</p>}
    </li>
  );
}

export function PlanTreePanel({
  planId,
  version,
}: {
  planId: number;
  /** 变了就重取一次：对话里的建议被裁定、计划有改动时由上层递增。 */
  version: number;
}) {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    return getPlan(planId)
      .then((data) => {
        setTree(data);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "读取计划失败"),
      )
      .finally(() => setLoading(false));
  }, [planId]);

  useEffect(() => {
    Promise.resolve().then(() => setLoading(true));
    load();
  }, [load, version]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-white/[0.015]">
      <div className="flex items-center justify-between border-b border-white/[0.05] px-4 pb-2.5 pt-4">
        <span className="text-[11px] font-semibold tracking-widest text-white/50 uppercase">
          计划树
        </span>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            load();
          }}
          aria-label="刷新计划树"
          className="rounded-lg p-1 text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white/80"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        {error ? (
          <p className="mx-1 rounded-lg bg-red-400/10 p-3 text-[12px] text-red-300">
            {error}
          </p>
        ) : loading && !tree ? (
          <p className="px-1 text-[12px] text-white/50">读取计划中…</p>
        ) : tree?.plan ? (
          <>
            <p className="mx-1 mb-3 line-clamp-2 text-[12px] leading-relaxed text-white/55">
              {tree.plan.goal}
            </p>
            <ul className="flex flex-col gap-1.5">
              {tree.stages.map((stage) => (
                <StageRow
                  key={stage.id}
                  stage={stage}
                  onChanged={() => {
                    load();
                  }}
                />
              ))}
            </ul>
            {tree.lag.behind && tree.behind_reason && (
              <p className="mx-1 mt-3 rounded-lg bg-amber-500/10 p-2.5 text-[11px] leading-relaxed text-amber-400/90">
                {tree.behind_reason}
              </p>
            )}
          </>
        ) : (
          <p className="px-1 text-[12px] text-white/50">没有计划</p>
        )}
      </div>
    </div>
  );
}
