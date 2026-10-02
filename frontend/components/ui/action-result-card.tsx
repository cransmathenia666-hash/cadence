import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";
import type {
  BlueprintPayload,
  PlanChangePayload,
  ProfileChangePayload,
  Proposal,
  ProposalDecision,
} from "@/lib/api";

type ActionResultCardProps = {
  proposal: Proposal;
  decision: ProposalDecision;
  onEnterWorkbench: (planId: number) => void;
};

const FIELD_LABELS: Record<string, string> = {
  title: "名称",
  deliverable: "交付物",
  due_date: "截止日",
};

function valueOrEmpty(value: string | null | undefined) {
  return value || "（清空）";
}

function MemoryResult({ decision }: { decision: ProposalDecision }) {
  const remembered = decision.remembered;
  switch (decision.effect) {
    case "memory_added":
      return <>已新增记忆「{remembered?.memory?.content ?? "记忆内容"}」</>;
    case "memory_superseded":
      return (
        <>
          已用新记忆「{remembered?.memory?.content ?? "新内容"}」取代旧记忆「
          {remembered?.before ?? "原内容"}」
        </>
      );
    case "memory_renewed":
      return <>已续期记忆「{remembered?.memory?.content ?? "记忆内容"}」</>;
    case "memory_voided":
      return <>已作废记忆「{remembered?.memory?.content ?? remembered?.before ?? "记忆内容"}」</>;
    default:
      return <>已批准记忆候选</>;
  }
}

export function ActionResultCard({
  proposal,
  decision,
  onEnterWorkbench,
}: ActionResultCardProps) {
  const blueprint = proposal.payload as unknown as BlueprintPayload;
  const planChange = proposal.payload as unknown as PlanChangePayload;
  const profileChange = proposal.payload as unknown as ProfileChangePayload;
  const isBlueprint = proposal.kind === "plan_blueprint";
  const isPlanChange = proposal.kind === "plan_change";
  const planId = isBlueprint
    ? (decision.built?.plan_id ?? blueprint.plan_id)
    : isPlanChange && Number.isInteger(planChange.plan_id)
      ? planChange.plan_id
      : null;

  return (
    <section
      aria-label={`提案 #${proposal.id} 的批准结果`}
      className="border-y border-green/30 py-8"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center border border-green/40">
          <Check className="size-4 text-green stroke-[2]" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <h3 className="text-[15px] font-semibold text-white/90">已批准并完成交接</h3>
            <span className="text-[12px] text-white/60">提案 #{proposal.id}</span>
          </div>

          {isBlueprint ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                已将蓝图建入计划：
                <span className="font-medium text-white/90">{blueprint.goal || "新计划"}</span>
                {planId !== null && <span className="ml-1 text-white/55">（计划 #{planId}）</span>}
              </p>
              {decision.landing_mode && (
                <p className="text-[12px] text-white/60">
                  规划分流：{decision.landing_mode === "new_plan"
                    ? "新方向，已创建正式计划"
                    : decision.landing_mode === "continue_plan"
                      ? "延续已有计划"
                      : "改版已有计划"}
                  {decision.planning_session_id !== null && decision.planning_session_id !== undefined
                    ? ` · 规划会话 #${decision.planning_session_id} 已转正`
                    : ""}
                </p>
              )}
              {decision.contract && (
                <p className="text-[12px] text-white/60">
                  成果契约已激活：{decision.contract.title}（版本号由后端分配）
                </p>
              )}
              {decision.built && (
                <>
                  <p>
                    新建 {decision.built.stages.length} 个阶段、{decision.built.tasks.length} 件任务。
                  </p>
                  {decision.built.stages.length > 0 && (
                    <ul className="list-disc space-y-1 pl-5 text-[12px] text-white/60">
                      {decision.built.stages.map((stage) => {
                        const tasks = decision.built?.tasks.filter((task) => task.stage_id === stage.id) ?? [];
                        return (
                          <li key={stage.id}>
                            阶段「{stage.title}」
                            {tasks.length > 0 && `：${tasks.map((task) => task.title).join("、")}`}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {decision.built.notes.length > 0 && (
                    <p className="text-[12px] text-white/50">说明：{decision.built.notes.join("；")}</p>
                  )}
                </>
              )}
              {planId !== null && (
                <button
                  type="button"
                  onClick={() => onEnterWorkbench(planId)}
                  className="mt-3 inline-flex items-center gap-2 rounded-md bg-white px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                >
                  进入工作台 <ArrowRight className="size-3.5" />
                </button>
              )}
            </div>
          ) : isPlanChange ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                {planChange.summary || "计划节点改动已落地。"}
                {planId !== null && <span className="ml-1 text-white/55">目标计划 #{planId}</span>}
              </p>
              {decision.updated && (
                <div className="space-y-1 text-[12px] text-white/60">
                  <p>已原地修改节点 #{decision.updated.node_id}（编号保持不变）：</p>
                  {decision.updated.changed.map((field) => (
                    <p key={field} className="pl-3">
                      {FIELD_LABELS[field] ?? field}：
                      {valueOrEmpty(decision.updated?.before[field])} → {valueOrEmpty(decision.updated?.after[field])}
                    </p>
                  ))}
                </div>
              )}
              {decision.added && decision.added.nodes.length > 0 && (
                <div className="text-[12px] text-white/60">
                  <p>已新建以下节点：</p>
                  <ul className="list-disc space-y-1 pl-5">
                    {decision.added.nodes.map((node) => (
                      <li key={node.id}>
                        {node.level === "stage" ? "阶段" : "任务"} #{node.id}「{node.title}」
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {planId !== null && (
                <button
                  type="button"
                  onClick={() => onEnterWorkbench(planId)}
                  className="mt-3 inline-flex items-center gap-2 rounded-md bg-white px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                >
                  进入工作台 <ArrowRight className="size-3.5" />
                </button>
              )}
            </div>
          ) : proposal.kind === "profile_change" ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                已写入长期档案（{decision.written?.category ?? profileChange.category}）：
                {decision.written?.content ?? profileChange.content}
              </p>
              <Link
                href="/profile"
                className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white/80 underline-offset-2 hover:text-white hover:underline"
              >
                查看档案 <ArrowRight className="size-3.5" />
              </Link>
            </div>
          ) : proposal.kind === "memory_change" ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p><MemoryResult decision={decision} /></p>
              <Link
                href="/memory"
                className="inline-flex items-center gap-1.5 text-[12px] font-medium text-white/80 underline-offset-2 hover:text-white hover:underline"
              >
                查看记忆 <ArrowRight className="size-3.5" />
              </Link>
            </div>
          ) : proposal.kind === "material_judgment" ? (
            <p className="mt-2 text-[13px] leading-relaxed text-white/70">
              资料评估已批准，本次只记账；不会创建计划或写入档案。
            </p>
          ) : (
            <p className="mt-2 text-[13px] leading-relaxed text-white/70">
              已批准并完成记录；本次没有计划或档案变更。
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
