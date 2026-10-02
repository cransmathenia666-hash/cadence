"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, ChevronDown } from "lucide-react";
import { ApiError, getPlan, getReviewCard, submitReport, type PlanTree, type ReportNextAction, type ReportResult, type ReviewCard, type ReportStatus } from "@/lib/api";

const STATUS_LABELS: Record<ReportStatus, string> = { done: "完成", partial: "部分完成", stuck: "卡住了", skipped: "跳过" };
const NEXT_ACTION_LABELS: Record<ReportNextAction, string> = { continue: "继续推进", narrow: "缩小范围", defer: "整体顺延", switch: "换路线", stop: "停止这条计划" };
const NODE_STATUS_LABELS: Record<string, string> = { not_started: "未开始", in_progress: "进行中", done: "执行状态已完成（不代表验收）", stuck: "卡住", skipped: "已跳过" };
const STATUS_HINTS: Record<ReportStatus, string> = { done: "本周目标已经达成", partial: "有进展，还有未完成的部分", stuck: "遇到阻碍，需要留下原因", skipped: "本次没有执行，留下背景" };
type Option = { id: number; label: string; stage: string; status: string };
type Feedback = { ok: boolean; text: string; result?: ReportResult };
const messageOf = (cause: unknown, fallback: string) => cause instanceof ApiError ? cause.message : fallback;
const toOptions = (tree: PlanTree): Option[] => tree.stages.flatMap((stage) => stage.checkpoints.map((checkpoint) => ({ id: checkpoint.id, label: checkpoint.title, stage: stage.title, status: checkpoint.status })));

function ReportForm() {
  const searchParams = useSearchParams();
  const requestedPlanId = searchParams.get("plan_id");
  const requestedNodeId = searchParams.get("node_id");
  const planId = requestedPlanId === null ? null : Number(requestedPlanId);
  const validPlanId = requestedPlanId === null || (/^[1-9]\d*$/.test(requestedPlanId) && Number.isSafeInteger(planId));
  const parsedNodeId = requestedNodeId !== null && /^[1-9]\d*$/.test(requestedNodeId) ? Number(requestedNodeId) : null;
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState("");
  const [status, setStatus] = useState<ReportStatus>("done");
  const [note, setNote] = useState("");
  const [artifactUrl, setArtifactUrl] = useState("");
  const [materialFeedback, setMaterialFeedback] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [retroOpen, setRetroOpen] = useState(false);
  const [stageIds, setStageIds] = useState<number[]>([]);
  const [criteriaIds, setCriteriaIds] = useState<string[]>([]);
  const [nextAction, setNextAction] = useState<"" | ReportNextAction>("");
  const [reviewRequested, setReviewRequested] = useState(false);
  const [currentCard, setCurrentCard] = useState<ReviewCard | null>(null);
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.resolve().then(() => { if (alive) { setLoading(true); setTree(null); setNodeId(""); setLoadError(null); setFeedback(null); } });
    if (!validPlanId) {
      Promise.resolve().then(() => { if (alive) { setLoading(false); setLoadError("计划编号无效，请从工作台重新打开报告入口。"); } });
      return () => { alive = false; };
    }
    getPlan(planId ?? undefined).then((data) => {
      if (!alive) return;
      setTree(data); setLoadError(data.plan ? null : "当前没有可提交报告的计划。");
      if (parsedNodeId !== null && toOptions(data).some((option) => option.id === parsedNodeId)) setNodeId(String(parsedNodeId));
    }).catch((cause: unknown) => { if (alive) setLoadError(messageOf(cause, "取计划时出了意外错误")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [parsedNodeId, planId, requestedPlanId, validPlanId]);

  const options = tree ? toOptions(tree) : [];
  const chosen = options.find((option) => String(option.id) === nodeId);
  const requestedNodeMissing = parsedNodeId !== null && !options.some((option) => option.id === parsedNodeId);
  const currentPlanId = tree?.plan?.id ?? null;
  const back = currentPlanId === null ? "/workbench" : `/workbench?plan_id=${currentPlanId}`;

  // 当前复盘卡（只读）：随计划切换重取；取不到就静默不显示（旧计划/无计划不报错）。
  useEffect(() => {
    let alive = true;
    if (currentPlanId === null) {
      Promise.resolve().then(() => { if (alive) setCurrentCard(null); });
      return () => { alive = false; };
    }
    getReviewCard(currentPlanId)
      .then((card) => { if (alive) setCurrentCard(card); })
      .catch(() => { if (alive) setCurrentCard(null); });
    return () => { alive = false; };
  }, [currentPlanId]);

  const stageOptions = tree?.stages ?? [];
  const criteriaOptions = stageOptions
    .filter((stage) => stageIds.includes(stage.id))
    .flatMap((stage) => stage.acceptance.criteria.map((criterion) => ({ ...criterion, stage: stage.title })));

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (currentPlanId === null || !nodeId || pending) return;
    setPending(true); setFeedback(null);
    try {
      const result = await submitReport({
        nodeId: Number(nodeId),
        status,
        note: note.trim(),
        artifactUrl: artifactUrl.trim() || undefined,
        materialFeedback: materialFeedback.trim() || undefined,
        stageNodeIds: stageIds.length > 0 ? stageIds : undefined,
        progressedCriteria: criteriaIds.length > 0 ? criteriaIds : undefined,
        nextAction: nextAction === "" ? null : nextAction,
        reviewRequested,
      });
      setFeedback({ ok: true, text: "报告已提交，计划节点状态已更新。", result });
      setNote(""); setArtifactUrl(""); setMaterialFeedback(""); setDetailsOpen(false);
      setStageIds([]); setCriteriaIds([]); setNextAction(""); setReviewRequested(false); setRetroOpen(false);
      try { setTree(await getPlan(currentPlanId)); setLoadError(null); }
      catch (cause) { setLoadError(messageOf(cause, "刷新计划失败，请回工作台查看最新状态")); }
    } catch (cause) { setFeedback({ ok: false, text: messageOf(cause, "提交失败，原因不明") }); }
    finally { setPending(false); }
  }

  return (
    <div className="mx-auto w-full max-w-[1280px] px-5 pb-24 pt-20 text-white md:px-10 md:pt-24">
      <header className="mb-11 flex flex-wrap items-end justify-between gap-6 border-b border-white/[0.12] pb-8"><div><h1 className="text-[32px] font-semibold tracking-[-0.03em] md:text-[42px]">记录这一周</h1><p className="mt-3 max-w-[60ch] text-[14px] leading-7 text-white/50">选一个周检查点，写下实际发生的事。报告会更新节点状态，并留下可回看的执行记录。</p></div><Link href={back} className="inline-flex items-center gap-2 text-[12px] text-white/50 hover:text-white"><ArrowLeft className="size-4" />回到工作台</Link></header>
      {loadError && <p role="alert" className="mb-7 border-t border-red-400/30 pt-3 text-[13px] text-red-300">{loadError}</p>}
      {requestedNodeMissing && <p role="status" className="mb-7 text-[12px] text-amber-300">链接中的节点不是此计划的周检查点，请重新选择。</p>}
      {loading && <p role="status" className="py-12 text-[13px] text-white/45">正在读取计划与周检查点…</p>}
      {!loading && currentCard && (
        <section aria-label="当前复盘卡" className="mb-9 border border-white/[0.12] bg-white/[0.03] px-6 py-5">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-[14px] font-medium text-white/80">当前复盘卡</h2>
            <span className="font-mono text-[11px] text-white/35">确定性规则 · 不调模型</span>
          </div>
          <p className="mt-3 text-[15px] leading-7 text-white/90">{currentCard.conclusion}</p>
          <p className="mt-2 text-[12px] leading-6 text-white/50">{currentCard.reason}</p>
          <ul className="mt-4 space-y-1.5">
            {currentCard.choices.map((choice) => (
              <li key={choice.action} className="text-[12px] leading-6 text-white/70">· {choice.label}——{choice.detail}</li>
            ))}
          </ul>
        </section>
      )}

      {feedback?.ok && feedback.result ? <section role="status" className="mx-auto max-w-[740px] py-10"><div className="mb-8 flex size-12 items-center justify-center border border-emerald-300/50 text-emerald-300"><Check className="size-6" /></div><h2 className="text-[28px] font-medium">这一次，已经记下。</h2><p className="mt-3 text-[14px] text-white/50">{feedback.text}</p><p className="mt-2 text-[12px] text-amber-200/75">报告只记录执行情况，不等于阶段验收；如需达标，请回工作台逐条验收。</p><dl className="mt-9 divide-y divide-white/[0.1] border-y border-white/[0.12] text-[13px]"><div className="flex justify-between gap-6 py-4"><dt className="text-white/40">报告</dt><dd className="font-mono">#{feedback.result.report_id}</dd></div><div className="flex justify-between gap-6 py-4"><dt className="text-white/40">本次结果</dt><dd>{STATUS_LABELS[feedback.result.report_status]}</dd></div><div className="flex justify-between gap-6 py-4"><dt className="text-white/40">节点状态</dt><dd>{NODE_STATUS_LABELS[feedback.result.node_status_before] ?? feedback.result.node_status_before} → {NODE_STATUS_LABELS[feedback.result.node_status] ?? feedback.result.node_status}</dd></div></dl>
{feedback.result.review_card && (
  <div className="mt-9 border border-amber-300/25 bg-amber-500/[0.05] px-6 py-5 text-left">
    <div className="flex flex-wrap items-baseline justify-between gap-3">
      <h3 className="text-[15px] font-medium text-amber-100/90">复盘卡</h3>
      <span className="font-mono text-[11px] text-white/35">确定性规则 · 不调模型</span>
    </div>
    <p className="mt-3 text-[15px] leading-7 text-white/90">{feedback.result.review_card.conclusion}</p>
    <p className="mt-2 text-[12px] leading-6 text-white/55">{feedback.result.review_card.reason}</p>
    <ul className="mt-4 space-y-1.5">
      {feedback.result.review_card.choices.map((choice) => (
        <li key={choice.action} className="text-[12px] leading-6 text-white/75">· {choice.label}——{choice.detail}</li>
      ))}
    </ul>
    <Link href={`/workbench?plan_id=${currentPlanId}&report_id=${feedback.result.report_id}`} className="mt-5 inline-flex items-center gap-2 border border-amber-300/40 px-4 py-2 text-[12px] text-amber-100 hover:border-amber-300/70 hover:text-white">让 AI 根据这次复盘给建议<ArrowRight className="size-3.5" /></Link>
    <p className="mt-2 text-[11px] leading-5 text-white/45">到工作台后，下一句对话会带上这份报告与复盘卡；只是闲聊就不会产提案。</p>
  </div>
)}
<div className="mt-9 flex flex-wrap gap-7"><Link href={back} className="inline-flex items-center gap-2 text-[13px] text-white underline underline-offset-4">回工作台查看<ArrowRight className="size-4" /></Link><button type="button" onClick={() => { setFeedback(null); setNodeId(""); }} className="text-[13px] text-white/50 hover:text-white">再记一个检查点</button></div></section> : !loading && tree?.plan && <form onSubmit={onSubmit} className="grid gap-9 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <section className="min-w-0"><div className="mb-5 flex items-end justify-between border-b border-white/[0.1] pb-3"><h2 className="text-[16px] font-medium">选择检查点</h2><span className="font-mono text-[11px] text-white/35">{options.length} 个可选</span></div><p className="mb-5 max-w-[48ch] text-[12px] leading-6 text-white/40">计划 #{tree.plan.id} · {tree.plan.goal}</p>{options.length === 0 ? <p className="border-y border-white/[0.1] py-10 text-[13px] leading-6 text-white/45">此计划还没有周打卡检查点。阶段和任务不在报告选项中。</p> : <div className="max-h-[55vh] overflow-y-auto border-t border-white/[0.08]">{options.map((option) => <button key={option.id} type="button" onClick={() => setNodeId(String(option.id))} aria-pressed={nodeId === String(option.id)} className={`flex w-full items-start gap-4 border-b border-white/[0.08] px-2 py-4 text-left transition-colors hover:bg-white/[0.03] ${nodeId === String(option.id) ? "bg-white/[0.06]" : ""}`}><span className={`mt-1 size-4 shrink-0 border ${nodeId === String(option.id) ? "border-white bg-white" : "border-white/30"}`} /> <span className="min-w-0 flex-1"><span className="block text-[11px] text-white/35">{option.stage}</span><span className="mt-1 block text-[14px] leading-6 text-white/80">{option.label}</span></span><span className="shrink-0 text-[11px] text-white/35">{NODE_STATUS_LABELS[option.status] ?? option.status}</span></button>)}</div>}</section>
        <section className={`min-w-0 transition-opacity ${chosen ? "opacity-100" : "opacity-45"}`}><div className="mb-5 border-b border-white/[0.1] pb-3"><h2 className="text-[16px] font-medium">写下实际情况</h2></div>{chosen ? <p className="mb-6 text-[13px] text-white/55">正在记录：{chosen.label}</p> : <p className="mb-6 text-[13px] text-white/45">从左侧选定检查点后填写。</p>}<fieldset disabled={!chosen || pending} className="space-y-7"><div><legend className="mb-3 text-[12px] text-white/60">执行结果</legend><div className="grid grid-cols-2 gap-px border border-white/[0.12] bg-white/[0.12] sm:grid-cols-4">{(Object.keys(STATUS_LABELS) as ReportStatus[]).map((key) => <label key={key} className={`cursor-pointer bg-surface-raised px-3 py-4 transition-colors ${status === key ? "text-white" : "text-white/40 hover:text-white/70"}`}><input type="radio" name="report-status" value={key} checked={status === key} onChange={() => setStatus(key)} className="sr-only" /><span className={`mb-3 block h-0.5 w-6 ${status === key ? "bg-white" : "bg-white/10"}`} /><span className="block text-[13px] font-medium">{STATUS_LABELS[key]}</span></label>)}</div><p className="mt-2 text-[11px] text-white/35">{STATUS_HINTS[status]}</p></div><div><label htmlFor="note" className="mb-2 block text-[12px] text-white/60">一句话说明 · 必填</label><textarea id="note" value={note} onChange={(event) => setNote(event.target.value)} rows={3} required placeholder="本周实际做了什么？哪里与预期不同？" className="w-full resize-y border border-white/[0.16] bg-white/[0.03] p-4 text-[14px] leading-6 text-white outline-none placeholder:text-white/25 focus:border-white/50" /></div><div className="border-t border-white/[0.1] pt-4"><button type="button" onClick={() => setDetailsOpen(!detailsOpen)} aria-expanded={detailsOpen} className="flex w-full items-center justify-between text-left text-[12px] text-white/50 hover:text-white"><span>补充产物与资料评价 · 选填</span><ChevronDown className={`size-4 transition-transform ${detailsOpen ? "rotate-180" : ""}`} /></button>{detailsOpen && <div className="mt-5 space-y-5"><div><label htmlFor="artifact-url" className="mb-2 block text-[12px] text-white/55">产物链接</label><input id="artifact-url" type="url" value={artifactUrl} onChange={(event) => setArtifactUrl(event.target.value)} placeholder="代码、笔记或作品链接" className="w-full border-b border-white/[0.16] bg-transparent py-2 text-[13px] text-white outline-none placeholder:text-white/25 focus:border-white/50" /></div><div><label htmlFor="material-feedback" className="mb-2 block text-[12px] text-white/55">资料评价</label><input id="material-feedback" value={materialFeedback} onChange={(event) => setMaterialFeedback(event.target.value)} placeholder="哪些资料有效，哪些不适合？" className="w-full border-b border-white/[0.16] bg-transparent py-2 text-[13px] text-white outline-none placeholder:text-white/25 focus:border-white/50" /></div></div>}</div>
<div className="border-t border-white/[0.1] pt-4"><button type="button" data-rec="retro-toggle" onClick={() => setRetroOpen(!retroOpen)} aria-expanded={retroOpen} className="flex w-full items-center justify-between text-left text-[12px] text-white/50 hover:text-white"><span>成果复盘信息 · 选填</span><ChevronDown className={`size-4 transition-transform ${retroOpen ? "rotate-180" : ""}`} /></button>{retroOpen && <div className="mt-5 space-y-5"><div><span className="mb-2 block text-[12px] text-white/55">本周涉及的阶段</span>{stageOptions.length === 0 ? <p className="text-[12px] text-white/40">此计划还没有阶段。</p> : <div className="space-y-2">{stageOptions.map((stage) => (<label key={stage.id} className="flex cursor-pointer items-start gap-2.5 text-[13px] leading-6 text-white/80"><input type="checkbox" checked={stageIds.includes(stage.id)} onChange={(event) => { const next = event.target.checked ? [...stageIds, stage.id] : stageIds.filter((id) => id !== stage.id); setStageIds(next); if (!event.target.checked) setCriteriaIds((current) => current.filter((cid) => !stage.acceptance.criteria.some((criterion) => criterion.id === cid))); }} className="mt-1.5 size-3.5 accent-white" />{stage.title}</label>))}</div>}</div>{criteriaOptions.length > 0 && (<div><span className="mb-2 block text-[12px] text-white/55">哪些验收条件有进展</span><div className="space-y-2">{criteriaOptions.map((criterion) => (<label key={criterion.id} className="flex cursor-pointer items-start gap-2.5 text-[13px] leading-6 text-white/80"><input type="checkbox" checked={criteriaIds.includes(criterion.id)} onChange={(event) => setCriteriaIds(event.target.checked ? [...criteriaIds, criterion.id] : criteriaIds.filter((id) => id !== criterion.id))} className="mt-1.5 size-3.5 accent-white" /><span>{criterion.stage}｜{criterion.text}</span></label>))}</div></div>)}<div><label htmlFor="next-action" className="mb-2 block text-[12px] text-white/55">我自己觉得下一步</label><select id="next-action" value={nextAction} onChange={(event) => setNextAction(event.target.value as "" | ReportNextAction)} className="w-full border border-white/[0.16] bg-white/[0.03] px-3 py-2.5 text-[13px] text-white outline-none focus:border-white/50"><option value="">不填</option>{(Object.keys(NEXT_ACTION_LABELS) as ReportNextAction[]).map((key) => (<option key={key} value={key}>{NEXT_ACTION_LABELS[key]}</option>))}</select></div><label data-rec="review-requested" className="flex cursor-pointer items-start gap-2.5 text-[13px] leading-6 text-white/80"><input type="checkbox" checked={reviewRequested} onChange={(event) => setReviewRequested(event.target.checked)} className="mt-1.5 size-3.5 accent-white" />希望基于这次报告让 AI 提调整建议（只记意图，不会自动调用）</label></div>}</div>
</fieldset>{feedback && !feedback.ok && <p role="alert" className="mt-5 text-[12px] text-red-300">提交失败：{feedback.text}。填写内容还在，可重试。</p>}<div className="mt-9 flex justify-end border-t border-white/[0.1] pt-5"><button type="submit" disabled={pending || !chosen || !note.trim()} className="inline-flex min-h-10 items-center gap-3 bg-white px-6 text-[13px] font-semibold text-black hover:bg-white/85 disabled:cursor-not-allowed disabled:opacity-35">{pending ? "正在提交…" : "提交这次报告"}<ArrowRight className="size-4" /></button></div></section>
      </form>}
    </div>
  );
}

export default function ReportPage() { return <Suspense fallback={<div role="status" className="px-10 pt-24 text-[13px] text-white/50">正在打开报告页…</div>}><ReportForm /></Suspense>; }
