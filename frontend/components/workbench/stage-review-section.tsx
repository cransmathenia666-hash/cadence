"use client";

import { useState } from "react";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import { type AcceptanceCriterionState, type ReviewDecision, type Stage } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATE_LABELS: Record<AcceptanceCriterionState, string> = {
  met: "已满足",
  unmet: "未满足",
  unknown: "暂无法判断",
};

const DECISION_LABELS: Record<ReviewDecision, string> = {
  accepted: "达标",
  needs_work: "还需继续",
  not_met: "未达标",
};

const control =
  "rounded-sm border border-white/[0.12] bg-black/30 px-2.5 py-1.5 text-[11px] text-primary outline-none focus:border-white/40 focus-visible:ring-2 focus-visible:ring-white/30";

export function StageReviewSection({
  stage,
  open,
  decision,
  criteriaState,
  submissionIds,
  note,
  busy,
  error,
  onOpen,
  onDecision,
  onCriteriaState,
  onSubmissionIds,
  onNote,
  onSubmit,
  onCancel,
}: {
  stage: Stage;
  open: boolean;
  decision: ReviewDecision;
  criteriaState: Record<string, AcceptanceCriterionState>;
  submissionIds: number[];
  note: string;
  busy: boolean;
  error: string | null;
  onOpen: () => void;
  onDecision: (value: ReviewDecision) => void;
  onCriteriaState: (value: Record<string, AcceptanceCriterionState>) => void;
  onSubmissionIds: (value: number[]) => void;
  onNote: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const acceptance = stage.acceptance;
  return (
    <section className="border-b border-white/[0.08] px-6 py-6" aria-label="阶段验收">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[10px] font-medium tracking-[0.2em] text-white/45">02 / 验收</h3>
          <p className="mt-2 text-[13px] text-primary/80">
            当前状态：{acceptance.status === "pending" ? "待验收" : acceptance.status === "accepted" ? "已达标" : acceptance.status === "needs_work" ? "还需继续" : acceptance.status === "not_met" ? "未达标" : acceptance.status === "invalidated" ? "标准已变化，需要重看" : "已跳过"}
          </p>
          <p className="mt-1 text-[11px] text-white/40">验收由你逐条确认；系统不会替你判定。</p>
        </div>
        <button
          type="button"
          data-rec="review-open"
          onClick={onOpen}
          disabled={stage.status === "skipped"}
          className="shrink-0 border-b border-white/25 py-1 text-[11px] text-primary/80 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          {acceptance.latest_review ? "重新验收" : "开始验收"}
        </button>
      </div>

      {open ? (
        <div className="mt-5 space-y-5 border-l border-white/20 pl-4">
          <fieldset className="space-y-3">
            <legend className="text-[11px] text-white/55">逐条确认验收条件</legend>
            {acceptance.criteria.map((criterion) => (
              <label key={criterion.id} className="grid gap-2 border-b border-white/[0.07] pb-3 text-[12px] text-primary/80">
                <span>{criterion.text}{criterion.required ? " · 必需" : " · 可选"}</span>
                <select
                  aria-label={`验收条件：${criterion.text}`}
                  value={criteriaState[criterion.id] ?? "unknown"}
                  onChange={(event) => onCriteriaState({ ...criteriaState, [criterion.id]: event.target.value as AcceptanceCriterionState })}
                  className={control}
                >
                  {(Object.keys(STATE_LABELS) as AcceptanceCriterionState[]).map((state) => <option key={state} value={state}>{STATE_LABELS[state]}</option>)}
                </select>
              </label>
            ))}
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-[11px] text-white/55">关联本阶段证据</legend>
            {acceptance.evidence.length === 0 ? <p className="text-[11px] text-white/40">还没有证据，请先提交证据。</p> : acceptance.evidence.map((evidence) => (
              <label key={evidence.id} className="flex items-start gap-2 text-[11px] text-white/65">
                <input
                  type="checkbox"
                  checked={submissionIds.includes(evidence.id)}
                  onChange={(event) => onSubmissionIds(event.target.checked ? [...submissionIds, evidence.id] : submissionIds.filter((id) => id !== evidence.id))}
                  className="mt-0.5 accent-white"
                />
                <span><span className="text-white/40">#{evidence.id} · {evidence.kind}</span> {evidence.note}</span>
              </label>
            ))}
          </fieldset>

          <div className="grid gap-2 sm:grid-cols-3">
            {(Object.keys(DECISION_LABELS) as ReviewDecision[]).map((value) => (
              <label key={value} className={cn("cursor-pointer border px-3 py-2 text-[11px] transition-colors focus-within:border-white/60 focus-within:ring-2 focus-within:ring-white/40 focus-within:ring-offset-2 focus-within:ring-offset-background", decision === value ? "border-white/50 bg-white/[0.08] text-primary" : "border-white/[0.12] text-white/50 hover:border-white/30")}>
                <input type="radio" name={`review-decision-${stage.id}`} value={value} checked={decision === value} onChange={() => onDecision(value)} className="sr-only peer" />
                {DECISION_LABELS[value]}
              </label>
            ))}
          </div>

          <label className="block text-[11px] text-white/55">
            本次验收说明（必填）
            <textarea value={note} onChange={(event) => onNote(event.target.value)} rows={3} className={cn(control, "mt-1.5 w-full resize-y")} placeholder="依据哪些证据做出这个判断？" />
          </label>
          {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
          <div className="flex items-center gap-2">
            <button type="button" onClick={onSubmit} disabled={busy || !note.trim()} className="inline-flex items-center gap-1 rounded-sm bg-white px-3 py-1.5 text-[11px] font-medium text-black disabled:opacity-40">
              {busy && <Loader2 className="size-3 animate-spin" />}提交验收
            </button>
            <button type="button" onClick={onCancel} disabled={busy} className="text-[11px] text-white/50 hover:text-white/80">取消</button>
          </div>
        </div>
      ) : null}

      {acceptance.latest_review && (
        <div className="mt-4 border-t border-white/[0.07] pt-3 text-[11px] text-white/50">
          <p><Check className="mr-1 inline size-3 text-green/80" aria-hidden="true" />最近判断：{DECISION_LABELS[acceptance.latest_review.decision]} · {acceptance.latest_review.note}</p>
        </div>
      )}
      {acceptance.reviews.length > 0 && (
        <div className="mt-4">
          <button type="button" onClick={() => setHistoryOpen(!historyOpen)} aria-expanded={historyOpen} className="inline-flex items-center gap-1 text-[11px] text-white/45 hover:text-white/75"><ChevronDown className={cn("size-3 transition-transform", historyOpen && "rotate-180")} />查看验收历史（{acceptance.reviews.length}）</button>
          {historyOpen && <ol className="mt-2 space-y-2 border-l border-white/10 pl-3">{acceptance.reviews.map((review) => <li key={review.id} className="text-[11px] text-white/45"><span className="text-white/65">{DECISION_LABELS[review.decision]}</span> · {review.note}{review.invalidated_at ? " · 已失效" : ""}</li>)}</ol>}
        </div>
      )}
    </section>
  );
}
