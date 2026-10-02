import type { Proposal } from "@/lib/api";

/** 契约修正提案（P3 OC-09）的 payload：对话里聊出、批准才激活新版契约。 */
type ContractChangePayload = {
  plan_id: number | null;
  contract_id: number | null;
  /** 拟改的契约字段（只列要改的键）。 */
  changes: Record<string, unknown>;
  /** 后端拼好的与现版差异一行话。 */
  diff_summary: string;
  why: string | null;
  dialogue_id: number | null;
  report_id: number | null;
};

const FIELD_LABELS: Record<string, string> = {
  title: "成果名称",
  outcome: "最终结果",
  value: "价值",
  success_statement: "成功标准",
  acceptance_criteria: "验收条件",
  evidence_requirements: "证据要求",
  constraints: "约束",
  stop_conditions: "停止条件",
};

/**
 * 「契约修正」提案的正文：改哪几段、与现版差在哪、为什么改。
 * 批准＝激活新一版契约（旧版留痕、受影响验收失效）；驳回＝终态 rejected。
 */
export function ContractChangeSection({ proposal, planName }: { proposal: Proposal; planName?: (planId: number) => string | null }) {
  const payload = proposal.payload as unknown as ContractChangePayload;
  const changedKeys = Object.keys(payload.changes ?? {});

  return (
    <section aria-label="契约修正内容" className="space-y-7">
      <div className="max-w-[75ch] space-y-2 text-[13px] leading-6 text-white/70">
        <p>
          落点：
          <span className="font-medium text-white">
            {payload.plan_id !== null && payload.plan_id !== undefined
              ? (planName?.(Number(payload.plan_id)) ?? `计划 #${payload.plan_id}`)
              : "未知计划"}
          </span>
          的成果契约（当前第 {payload.contract_id ?? "—"} 号版本之上拟改）。
        </p>
        <p className="text-white/60">批准会激活新一版契约：旧版留痕可查，受影响的当前验收记录按规则失效；驳回则什么都不改。</p>
        {payload.report_id !== null && payload.report_id !== undefined && (
          <p className="text-white/50">来自报告 #{payload.report_id} 的复盘回流对话。</p>
        )}
      </div>

      <div className="border-t border-white/10 pt-6">
        <h3 className="text-[15px] font-semibold text-white/90">与现版的差异</h3>
        <p className="mt-3 max-w-[75ch] text-[13px] leading-6 text-amber-100/90">{payload.diff_summary || "（后端未给差异摘要）"}</p>
      </div>

      <div className="border-t border-white/10 pt-6">
        <h3 className="text-[15px] font-semibold text-white/90">拟改字段 · {changedKeys.length} 处</h3>
        <dl className="mt-4 divide-y divide-white/[0.08] border-y border-white/[0.08]">
          {changedKeys.map((key) => (
            <div key={key} className="py-4">
              <dt className="text-[12px] text-white/55">{FIELD_LABELS[key] ?? key}</dt>
              <dd className="mt-2 max-w-[75ch] whitespace-pre-wrap text-[13px] leading-6 text-white/85">
                {typeof payload.changes[key] === "string"
                  ? String(payload.changes[key])
                  : JSON.stringify(payload.changes[key], null, 2)}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {payload.why && (
        <div className="border-t border-white/10 pt-6">
          <h3 className="text-[15px] font-semibold text-white/90">为什么要改</h3>
          <p className="mt-3 max-w-[75ch] border-l border-white/20 pl-4 text-[14px] leading-7 text-white/70">{payload.why}</p>
        </div>
      )}
    </section>
  );
}
