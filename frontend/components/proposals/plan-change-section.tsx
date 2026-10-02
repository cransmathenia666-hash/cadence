import { ChevronRight } from "lucide-react";
import { planChangeTasks, type PlanChangePayload, type Proposal } from "@/lib/api";

const FIELD_LABELS: Record<string, string> = {
  title: "名称",
  deliverable: "交付物",
  due_date: "截止日",
};

export function PlanChangeSection({
  proposal,
  planName,
}: {
  proposal: Proposal;
  planName?: (planId: number) => string | null;
}) {
  const payload = proposal.payload as unknown as PlanChangePayload;
  const tasks = planChangeTasks(payload);
  const actionLabel = payload.action === "update_node"
    ? "修改现有节点"
    : payload.action === "add_task"
      ? "增加任务"
      : payload.action === "add_stage"
        ? "增加阶段"
        : "待识别的改动";

  return (
    <section aria-label="计划改动内容" className="max-w-[84ch]">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-white/15 pb-5">
        <h3 className="text-[16px] font-semibold text-white">{actionLabel}</h3>
        <p className="text-[13px] text-white/65">{planName?.(Number(payload.plan_id)) ?? `计划 #${payload.plan_id}`}</p>
      </div>

      {payload.summary && <p className="max-w-[70ch] py-7 text-[clamp(1.2rem,2vw,1.6rem)] font-medium leading-[1.45] text-white [overflow-wrap:anywhere]">{payload.summary}</p>}

      {payload.action === "update_node" && payload.fields && (
        <div>
          <p className="mb-4 text-[13px] text-white/70">目标：{payload.level === "stage" ? "阶段" : "任务"}「<span className="text-white">{payload.node_title ?? `#${payload.node_id}`}</span>」</p>
          <div role="table" aria-label="拟修改的字段" className="border-t border-white/10">
            <div role="row" className="grid grid-cols-[minmax(70px,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] gap-4 border-b border-white/10 py-3 text-[12px] text-white/60">
              <span role="columnheader">字段</span><span role="columnheader">原来</span><span role="columnheader">批准后</span>
            </div>
            {Object.entries(payload.fields).map(([field, next]) => (
              <div role="row" key={field} className="grid grid-cols-[minmax(70px,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] gap-4 border-b border-white/10 py-4 text-[14px] leading-6">
                <span role="cell" className="text-white/65">{FIELD_LABELS[field] ?? field}</span>
                <span role="cell" className="min-w-0 text-white/60 [overflow-wrap:anywhere]">{payload.before?.[field] || "（无）"}</span>
                <span role="cell" className="min-w-0 font-medium text-white [overflow-wrap:anywhere]">{next || "（清空）"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {payload.action === "add_task" && (
        <div className="border-t border-white/10 pt-5">
          <p className="text-[13px] text-white/65">加入阶段「<span className="text-white">{payload.stage_title ?? `#${payload.stage_id}`}</span>」</p>
          <TaskLines tasks={tasks} />
        </div>
      )}

      {payload.action === "add_stage" && payload.stage && (
        <div className="border-t border-white/10 pt-5">
          <p className="text-[12px] text-white/60">排在计划最后的新阶段</p>
          <p className="mt-2 text-[20px] font-medium leading-7 text-white">{payload.stage.title}</p>
          {payload.stage.deliverable && <p className="mt-4 text-[13px] leading-6 text-white/75">交付物：{payload.stage.deliverable}</p>}
          {payload.stage.why && <p className="mt-2 text-[13px] leading-6 text-white/65">阶段依据：{payload.stage.why}</p>}
          <TaskLines tasks={tasks} />
        </div>
      )}

      {payload.why && (
        <details className="group mt-8 border-t border-white/10 pt-4">
          <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
            查看建议依据
          </summary>
          <p className="mt-4 text-[14px] leading-7 text-white/75">{payload.why}</p>
        </details>
      )}
      <p className="mt-7 text-[12px] leading-5 text-white/65">来自计划跟进对话；批准后直接修改计划并留下台账。</p>
    </section>
  );
}

function TaskLines({ tasks }: { tasks: ReturnType<typeof planChangeTasks> }) {
  if (tasks.length === 0) return null;
  return (
    <div className="mt-8">
      <h4 className="mb-3 text-[13px] font-medium text-white/75">一并加入的任务 · {tasks.length} 件</h4>
      <ol className="border-t border-white/10">
        {tasks.map((task, index) => (
          <li key={`${task.title}-${index}`} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-white/10 py-4">
            <span className="w-6 shrink-0 text-[12px] tabular-nums text-white/60">{String(index + 1).padStart(2, "0")}</span>
            <span className="min-w-0 flex-1 text-[15px] leading-6 text-white/90 [overflow-wrap:anywhere]">{task.title}</span>
            {task.due_date && <span className="text-[12px] text-white/65">建议截止 {task.due_date}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
