import { useState } from "react";
import { Check, ArrowRight } from "lucide-react";
import {
  isBlueprintV2,
  type BlueprintContractDraft,
  type BlueprintPayload,
  type Proposal,
} from "@/lib/api";
import { normalize } from "@/components/blueprint-body";
import { BlueprintReviewPanel } from "@/components/proposals/blueprint-review-summary";

/** 证据类型令牌 → 中文名（与工作台 / 新建页同一套措辞）。 */
const EVIDENCE_KIND_LABELS: Record<string, string> = {
  repository: "代码仓库",
  link: "网页链接",
  document: "文档",
  demo: "演示 / 录屏",
  screenshot: "截图",
  text: "文字结果",
  other: "其他",
};

/** 批准前可以就地微调的四段契约正文（OC-07 的 contract_overrides 的最小子集）。 */
export type ContractTextDraft = {
  title: string;
  outcome: string;
  value: string;
  success_statement: string;
};

export function contractDraftOf(payload: BlueprintPayload): ContractTextDraft | null {
  const contract = payload.contract;
  if (!contract) return null;
  return {
    title: contract.title ?? "",
    outcome: contract.outcome ?? "",
    value: contract.value ?? "",
    success_statement: contract.success_statement ?? "",
  };
}

/** 草稿与蓝图原稿的差异 = 真正要发的 overrides；没改就返回 null（不发这个参数）。 */
export function contractOverridesOf(
  payload: BlueprintPayload,
  draft: ContractTextDraft | null,
): Record<string, string> | null {
  const contract = payload.contract;
  if (!contract || !draft) return null;
  const overrides: Record<string, string> = {};
  for (const field of ["title", "outcome", "value", "success_statement"] as const) {
    const next = draft[field].trim();
    // 空文本不发：清空必填字段会被后端整单拒绝；批准前由 contractDraftErrorOf 就地拦截。
    if (next !== "" && next !== String(contract[field] ?? "").trim()) {
      overrides[field] = next;
    }
  }
  return Object.keys(overrides).length > 0 ? overrides : null;
}

export function contractDraftErrorOf(draft: ContractTextDraft | null): string | null {
  if (!draft) return null;
  const labels: Record<keyof ContractTextDraft, string> = {
    title: "成果名称",
    outcome: "最终结果",
    value: "价值",
    success_statement: "成功标准",
  };
  const empty = (Object.keys(labels) as (keyof ContractTextDraft)[]).find(
    (field) => draft[field].trim() === "",
  );
  return empty ? `${labels[empty]}不能为空；请补回文字，或退回规划重新生成。` : null;
}

const DRAFT_FIELDS: { key: keyof ContractTextDraft; label: string }[] = [
  { key: "title", label: "成果名称" },
  { key: "outcome", label: "最终结果" },
  { key: "value", label: "价值" },
  { key: "success_statement", label: "成功标准" },
];

function ContractSummary({
  contract,
  draft,
  onDraftChange,
  proposalId,
}: {
  contract: BlueprintContractDraft;
  draft: ContractTextDraft | null;
  onDraftChange: (next: ContractTextDraft) => void;
  proposalId: number;
}) {
  // 编辑态直接展示草稿（所见即所批）：不能用空值回退原稿，否则用户清空必填项时会看错。
  const title = draft ? draft.title : contract.title;
  const outcome = draft ? draft.outcome : contract.outcome;
  const value = draft ? draft.value : contract.value;
  const success = draft ? draft.success_statement : contract.success_statement;

  return (
    <section aria-label="成果契约" className="border-t border-white/10 pt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-[15px] font-semibold text-white/90">成果契约</h3>
        <p className="text-[12px] text-white/55">
          批准即激活为这个计划的正式契约 · 版本号由后端在激活时分配
        </p>
      </div>

      <h4 className="mt-5 max-w-[30ch] text-[clamp(1.25rem,2vw,1.6rem)] font-semibold leading-snug tracking-[-0.02em] text-white [overflow-wrap:anywhere]">
        {title}
      </h4>

      <dl className="mt-5 grid gap-x-10 gap-y-4 text-[13px] leading-6 sm:grid-cols-2">
        <div>
          <dt className="text-[11px] text-white/55">最终结果（outcome）</dt>
          <dd className="mt-1 max-w-[70ch] text-white/80">{outcome}</dd>
        </div>
        <div>
          <dt className="text-[11px] text-white/55">价值（value）</dt>
          <dd className="mt-1 max-w-[70ch] text-white/80">{value}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-[11px] text-white/55">成功标准（做到什么算够）</dt>
          <dd className="mt-1 max-w-[75ch] text-white/80">{success}</dd>
        </div>
      </dl>

      <div className="mt-6 grid gap-x-10 gap-y-6 xl:grid-cols-2">
        <div>
          <h5 className="text-[12px] font-medium text-white/70">
            验收条件 · {contract.acceptance_criteria.length} 条
          </h5>
          <ol className="mt-3 border-t border-white/[0.08]">
            {contract.acceptance_criteria.map((criterion) => (
              <li key={criterion.id} className="border-b border-white/[0.08] py-2.5">
                <div className="flex items-start gap-3 text-[13px] leading-6">
                  <span className="mt-1 shrink-0 font-mono text-[11px] tabular-nums text-white/40">{criterion.id}</span>
                  <span className="min-w-0 flex-1 text-white/80">{criterion.text}</span>
                  <span className={`shrink-0 text-[11px] ${criterion.required ? "text-amber-200/80" : "text-white/45"}`}>
                    {criterion.required ? "必需" : "可选"}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div>
          <h5 className="text-[12px] font-medium text-white/70">
            证据要求 · {contract.evidence_requirements.length} 条
          </h5>
          <ol className="mt-3 border-t border-white/[0.08]">
            {contract.evidence_requirements.map((requirement) => (
              <li key={requirement.id} className="border-b border-white/[0.08] py-2.5">
                <div className="flex items-start gap-3 text-[13px] leading-6">
                  <span className="mt-1 shrink-0 font-mono text-[11px] tabular-nums text-white/40">{requirement.id}</span>
                  <span className="min-w-0 flex-1 text-white/80">
                    <span className="font-medium">{EVIDENCE_KIND_LABELS[requirement.kind] ?? requirement.kind}</span>
                    {requirement.description && (
                      <span className="text-white/65">｜{requirement.description}</span>
                    )}
                  </span>
                  <span className={`shrink-0 text-[11px] ${requirement.required ? "text-amber-200/80" : "text-white/45"}`}>
                    {requirement.required ? "必需" : "可选"}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>

      {(contract.constraints?.length || contract.stop_conditions?.length) ? (
        <div className="mt-6 grid gap-x-10 gap-y-4 text-[13px] leading-6 sm:grid-cols-2">
          {contract.constraints && contract.constraints.length > 0 && (
            <div>
              <h5 className="text-[12px] font-medium text-white/70">约束</h5>
              <ul className="mt-2 space-y-1 text-white/70">
                {contract.constraints.map((item, index) => <li key={index}>· {item}</li>)}
              </ul>
            </div>
          )}
          {contract.stop_conditions && contract.stop_conditions.length > 0 && (
            <div>
              <h5 className="text-[12px] font-medium text-white/70">停止条件</h5>
              <ul className="mt-2 space-y-1 text-white/70">
                {contract.stop_conditions.map((item, index) => <li key={index}>· {item}</li>)}
              </ul>
            </div>
          )}
        </div>
      ) : null}

      {draft && (
        <details className="mt-6">
          <summary className="w-fit cursor-pointer rounded-sm py-1 text-[12px] text-white/60 underline decoration-white/25 underline-offset-4 hover:text-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            微调契约文字（可选）
          </summary>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            {DRAFT_FIELDS.map((field) => (
              <label key={field.key} className="block text-[12px]">
                <span className="mb-1.5 block text-white/55">{field.label}</span>
                <textarea
                  value={draft[field.key]}
                  onChange={(event) => onDraftChange({ ...draft, [field.key]: event.target.value })}
                  rows={field.key === "title" ? 1 : 2}
                  aria-label={`契约${field.label}（批准前可微调）`}
                  className="w-full resize-y rounded-md border border-white/[0.1] bg-transparent px-3 py-2 text-[13px] leading-6 text-white/85 outline-none transition-colors placeholder:text-white/35 focus:border-white/35"
                  placeholder={contract[field.key]}
                />
              </label>
            ))}
          </div>
          <p className="mt-2 max-w-[75ch] text-[12px] leading-5 text-white/50">
            只有改动过的段落会随批准提交（提案 #{proposalId} 的原稿与最终快照都会留档）。
            验收条件与证据要求不在这里改——需要调整就点下方「补充信息，回规划对话」，让它重新出一版。
          </p>
        </details>
      )}
    </section>
  );
}

export function BlueprintSection({
  proposal,
  selection,
  onSelection,
  planName,
  contractDraft,
  onContractDraftChange,
}: {
  proposal: Proposal;
  selection: string[];
  onSelection: (value: string[]) => void;
  planName?: (planId: number) => string | null;
  /** v2 蓝图的契约文字草稿（ProposalCard 管状态）；v1 恒为 null。 */
  contractDraft?: ContractTextDraft | null;
  onContractDraftChange?: (next: ContractTextDraft) => void;
}) {
  const payload = proposal.payload as unknown as BlueprintPayload;
  const stages = payload.stages ?? [];
  const review = payload.review;
  const v2 = isBlueprintV2(proposal.payload);
  const contract = v2 ? (payload.contract as BlueprintContractDraft) : null;
  const [focusedStage, setFocusedStage] = useState(0);
  const currentIndex = Math.min(focusedStage, Math.max(0, stages.length - 1));
  const current = stages[currentIndex];
  const whole = (index: number) => selection.includes(String(index));
  const tickedTask = (index: number, taskIndex: number) =>
    whole(index) || selection.includes(`${index}.${taskIndex}`);

  /** 承接的契约条件：id → 契约原文（v2 才有对照可查）。 */
  const criterionText = (id: string): string =>
    contract?.acceptance_criteria.find((item) => item.id === id)?.text ?? id;

  function toggleStage(index: number) {
    const others = selection.filter(
      (item) => item !== String(index) && !item.startsWith(`${index}.`),
    );
    onSelection(normalize(whole(index) ? others : [...others, String(index)], stages));
  }

  function toggleTask(index: number, taskIndex: number) {
    const path = `${index}.${taskIndex}`;
    if (whole(index)) {
      const rest = (stages[index].tasks ?? [])
        .map((_, other) => `${index}.${other}`)
        .filter((other) => other !== path);
      onSelection(normalize([...selection.filter((item) => item !== String(index)), ...rest], stages));
      return;
    }
    onSelection(
      normalize(
        selection.includes(path)
          ? selection.filter((item) => item !== path)
          : [...selection, path],
        stages,
      ),
    );
  }

  const tickedStages = stages.filter((_, index) => whole(index)).length;
  const taskTotal = stages.reduce((total, item) => total + (item.tasks ?? []).length, 0);
  const tickedTaskCount = stages.reduce(
    (total, item, index) =>
      total + (item.tasks ?? []).filter((_, taskIndex) => tickedTask(index, taskIndex)).length,
    0,
  );

  // 落点与批准后果（后端 payload 给的事实；landing_mode 不传，由后端按会话落点自动分流）
  const landingFact =
    payload.plan_id === null || payload.plan_id === undefined
      ? "新方向：批准那一刻会创建正式计划、激活这份契约并建树（现在没有任何计划被改动）"
      : `延续${planName?.(Number(payload.plan_id)) ? `计划「${planName?.(Number(payload.plan_id))}」` : `计划 #${payload.plan_id}`}：批准会在这个计划上激活新一版契约并建树`;

  return (
    <section aria-label="蓝图纳入范围" className="space-y-8">
      <div className="max-w-[75ch] space-y-2 text-[13px] leading-6 text-white/70">
        {!v2 && (
          <p
            role="note"
            className="border-l border-amber-400/70 bg-amber-500/[0.06] px-4 py-3 text-[13px] leading-6 text-amber-100/90"
          >
            旧版蓝图（v1，没有成果契约）——只读。后端会拒绝直接批准：
            「这是旧版蓝图（没有成果契约），不能直接批准——请在规划对话里重新生成 v2 蓝图后再来」。
            可以驳回它，回到规划对话重新生成。
          </p>
        )}
        <p>
          落点：
          {payload.plan_id !== null && payload.plan_id !== undefined ? (
            <span className="font-medium text-white">
              {planName?.(Number(payload.plan_id)) ?? `计划 #${payload.plan_id}`}
            </span>
          ) : (
            <span className="font-medium text-white">新方向（还没有正式计划）</span>
          )}
        </p>
        {v2 && <p className="text-white/60">{landingFact}。</p>}
        <div className="flex items-center gap-2">
          <span>生成模式</span>
          {payload.mode === "enhanced" ? (
            <span className="rounded-md bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
              增强复核 · 多 agent 审查
            </span>
          ) : (
            <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[11px] text-white/70">标准生成</span>
          )}
          {v2 && (
            <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[11px] text-white/70">蓝图 v2 · 含成果契约</span>
          )}
        </div>
        <p>勾选要纳入计划的阶段或任务；未勾选项不会创建。同名阶段将自动复用。</p>
      </div>

      {contract && (
        <ContractSummary
          contract={contract}
          draft={contractDraft ?? null}
          onDraftChange={(next) => onContractDraftChange?.(next)}
          proposalId={proposal.id}
        />
      )}

      {review && <BlueprintReviewPanel review={review} />}

      <div className="grid min-w-0 gap-8 border-t border-white/10 pt-6 xl:grid-cols-[minmax(190px,0.8fr)_minmax(0,1.6fr)] xl:gap-10">
        <div>
          <h3 className="mb-4 text-[13px] font-semibold text-white/75">阶段范围</h3>
          <ol className="border-t border-white/[0.08]">
            {stages.map((item, index) => {
              const selected = whole(index);
              const focused = index === currentIndex;
              return (
                <li key={`${item.title}-${index}`} className="border-b border-white/[0.08]">
                  <div className="flex items-stretch gap-3 py-3">
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={selected}
                      aria-label={`勾选整个阶段 ${index + 1}：${item.title}`}
                      data-rec={`stage-${index}`}
                      onClick={() => toggleStage(index)}
                      className="flex w-6 shrink-0 items-center justify-center self-start py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                    >
                      <span className={`flex h-4 w-4 items-center justify-center rounded-[3px] border ${selected ? "border-white bg-white text-black" : "border-white/40"}`}>
                        {selected && <Check className="h-3 w-3 stroke-[3]" />}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setFocusedStage(index)}
                      aria-current={focused ? "true" : undefined}
                      aria-controls={`blueprint-reading-${proposal.id}`}
                      className="group flex min-w-0 flex-1 items-start justify-between gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                    >
                      <span>
                        <span className={`block text-[13px] tabular-nums ${focused ? "text-white" : "text-white/50"}`}>{String(index + 1).padStart(2, "0")}</span>
                        <span className={`mt-1 block text-[14px] leading-5 ${focused ? "font-semibold text-white" : "text-white/70 group-hover:text-white"}`}>{item.title}</span>
                        <span className="mt-1 block text-[11px] text-white/50">{(item.tasks ?? []).length} 件任务</span>
                      </span>
                      <ArrowRight className={`mt-2 h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none ${focused ? "text-white" : "text-white/40"}`} />
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <div id={`blueprint-reading-${proposal.id}`} className="min-w-0 border-t border-white/10 pt-5 xl:border-l xl:border-t-0 xl:pl-8 xl:pt-0">
          {current ? (
            <>
              <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[12px] tabular-nums text-white/55">阶段 {currentIndex + 1} / {stages.length}</p>
                  <h3 className="mt-2 text-[clamp(1.4rem,2vw,2rem)] font-semibold leading-snug tracking-[-0.02em] text-white">{current.title}</h3>
                </div>
                <span className="text-[12px] text-white/65">{whole(currentIndex) ? "整个阶段已纳入" : "可逐项选择任务"}</span>
              </div>

              {current.purpose && (
                <div className="mb-5">
                  <span className="block text-[11px] text-white/55">这一阶段的目的</span>
                  <p className="mt-1 max-w-[70ch] text-[13px] leading-6 text-white/80">{current.purpose}</p>
                </div>
              )}
              {(current.why_now || current.why || current.deliverable) && (
                <div className="mb-8 grid gap-5 border-y border-white/[0.08] py-5 text-[13px] leading-6 sm:grid-cols-2">
                  {(current.why_now || current.why) && (
                    <div>
                      <span className="block text-[11px] text-white/55">为什么是现在</span>
                      <p className="mt-1 text-white/75">{current.why_now || current.why}</p>
                    </div>
                  )}
                  <div>
                    <span className="block text-[11px] text-white/55">阶段交付物</span>
                    <p className="mt-1 text-white/75">{current.deliverable || "未指明"}</p>
                  </div>
                </div>
              )}

              {(current.acceptance_criteria?.length || current.evidence_requirements?.length || current.contract_criterion_ids?.length) ? (
                <div className="mb-8 grid gap-x-8 gap-y-5 text-[13px] leading-6 sm:grid-cols-2">
                  {current.acceptance_criteria && current.acceptance_criteria.length > 0 && (
                    <div>
                      <h4 className="text-[12px] font-medium text-white/70">阶段验收条件</h4>
                      <ul className="mt-2 space-y-1.5 text-white/75">
                        {current.acceptance_criteria.map((criterion) => (
                          <li key={criterion.id} className="flex items-start gap-2">
                            <span className="mt-1 shrink-0 font-mono text-[10px] tabular-nums text-white/40">{criterion.id}</span>
                            <span className="min-w-0 flex-1">{criterion.text}</span>
                            {!criterion.required && <span className="shrink-0 text-[11px] text-white/45">可选</span>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {current.evidence_requirements && current.evidence_requirements.length > 0 && (
                    <div>
                      <h4 className="text-[12px] font-medium text-white/70">阶段证据要求</h4>
                      <ul className="mt-2 space-y-1.5 text-white/75">
                        {current.evidence_requirements.map((requirement) => (
                          <li key={requirement.id} className="flex items-start gap-2">
                            <span className="mt-1 shrink-0 font-mono text-[10px] tabular-nums text-white/40">{requirement.id}</span>
                            <span className="min-w-0 flex-1">
                              {EVIDENCE_KIND_LABELS[requirement.kind] ?? requirement.kind}
                              {requirement.description && <span className="text-white/60">｜{requirement.description}</span>}
                              {!requirement.required && <span className="ml-1 text-[11px] text-white/45">（可选）</span>}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {current.contract_criterion_ids && current.contract_criterion_ids.length > 0 && (
                    <div className="sm:col-span-2">
                      <h4 className="text-[12px] font-medium text-white/70">承接的成果契约条件</h4>
                      <ul className="mt-2 space-y-1.5 text-white/75">
                        {current.contract_criterion_ids.map((id) => (
                          <li key={id} className="flex items-start gap-2">
                            <span className="mt-1 shrink-0 font-mono text-[10px] tabular-nums text-white/40">{id}</span>
                            <span className="min-w-0 flex-1">{criterionText(id)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ) : null}

              {(current.tasks ?? []).length > 0 ? (
                <div>
                  <p className="mb-3 text-[12px] font-medium text-white/70">任务明细 · 可单独勾选</p>
                  <ol className="border-t border-white/[0.08]">
                    {(current.tasks ?? []).map((task, taskIndex) => {
                      const selected = tickedTask(currentIndex, taskIndex);
                      return (
                        <li key={`${task.title}-${taskIndex}`} className="border-b border-white/[0.08]">
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={selected}
                            aria-label={`勾选任务：${task.title}`}
                            onClick={() => toggleTask(currentIndex, taskIndex)}
                            className="flex w-full items-start gap-3 py-4 text-left hover:bg-white/[0.02] focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                          >
                            <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border ${selected ? "border-white bg-white text-black" : "border-white/40"}`}>
                              {selected && <Check className="h-3 w-3 stroke-[3]" />}
                            </span>
                            <span className="min-w-0 flex-1 text-[14px] leading-6 text-white/85">{task.title}</span>
                            {task.due_date && <span className="shrink-0 text-[11px] text-white/55">建议 {task.due_date}</span>}
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ) : <p className="text-[13px] text-white/60">这一阶段没有拆解任务。</p>}
            </>
          ) : <p className="text-[13px] text-white/60">没有可选择的阶段。</p>}
        </div>
      </div>

      <p className="border-t border-white/10 pt-4 text-[13px] text-white/75" aria-live="polite">
        已纳入 <strong className="font-semibold text-white">{tickedStages} / {stages.length}</strong> 个完整阶段 · <strong className="font-semibold text-white">{tickedTaskCount} / {taskTotal}</strong> 件任务
      </p>
    </section>
  );
}
