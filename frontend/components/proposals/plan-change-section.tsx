import { ChevronRight } from "lucide-react";
import { planChangeTasks, type PlanChangePayload, type Proposal } from "@/lib/api";

export function PlanChangeSection({
  proposal,
  planName,
}: {
  proposal: Proposal;
  /** 计划 id → 计划名；解不出名字的地方才退回编号。 */
  planName?: (planId: number) => string | null;
}) {
  const payload = proposal.payload as unknown as PlanChangePayload;
  const tasks = planChangeTasks(payload);

  return (
    <div className="py-2 space-y-4">
      {/* 动作徽章 + 计划归属 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium text-green bg-green/10 px-2.5 py-0.5 rounded-full border border-green/20 uppercase tracking-wider">
          {payload.action === "update_node"
            ? "改节点字段"
            : payload.action === "add_task"
              ? "加任务"
              : "加阶段"}
        </span>
        <span className="text-[13px] text-white/60 font-medium">
          {planName?.(Number(payload.plan_id)) ?? `计划 #${payload.plan_id}`} · 改动建议
        </span>
      </div>

      {payload.summary && (
        <div className="text-[15px] font-semibold text-white/90">
          {payload.summary}
        </div>
      )}

      {/* 改节点字段：两段式对比 */}
      {payload.action === "update_node" && payload.fields && (
        <div className="text-[13px] text-white/70 space-y-2">
          <div>
            目标节点：
            <strong className="text-white/90 font-medium">
              {payload.node_title ?? `#${payload.node_id}`}
            </strong>
            <span className="text-white/50 text-[12px] ml-1">
              （{payload.level === "stage" ? "阶段" : "任务"}）
            </span>
          </div>
          <div className="space-y-1.5 pl-3 border-l-2 border-white/10">
            {Object.entries(payload.fields).map(([k, v]) => (
              <div key={k} className="text-[12px] flex items-center gap-2 flex-wrap">
                <span className="text-white/50">{k}：</span>
                <span className="line-through text-white/50">
                  {payload.before?.[k] ?? "（无）"}
                </span>
                <span className="text-white/50">→</span>
                <span className="text-white/90 font-medium">
                  {v ?? "（清空）"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 加任务：编号列表 */}
      {payload.action === "add_task" && (
        <div className="text-[13px] text-white/70 space-y-2">
          <div>
            所属阶段：
            <strong className="text-white/90 font-medium">
              {payload.stage_title ?? `阶段 #${payload.stage_id}`}
            </strong>
          </div>
          <div className="text-[12px] text-white/50">
            要加的任务（{tasks.length} 件）：
          </div>
          <ol className="space-y-1.5 pl-1">
            {tasks.map((task, idx) => (
              <li key={`${task.title}-${idx}`} className="flex items-center gap-2 text-[13px]">
                <span className="text-white/50 text-[12px] w-4">{idx + 1}.</span>
                <span className="text-white/90 font-medium">{task.title}</span>
                {task.due_date && (
                  <span className="text-[11px] text-white/50">· 截止日：{task.due_date}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* 加阶段：新阶段 + 任务编号列表 */}
      {payload.action === "add_stage" && payload.stage && (
        <div className="text-[13px] text-white/70 space-y-2">
          <div>
            新阶段（排最后）：
            <strong className="text-white/90 font-medium">{payload.stage.title}</strong>
            {payload.stage.deliverable && (
              <div className="text-[12px] text-white/50 mt-0.5">
                交付物：{payload.stage.deliverable}
              </div>
            )}
          </div>
          {tasks.length > 0 && (
            <div className="mt-2 space-y-1.5">
              <div className="text-[12px] text-white/50">
                一并建的任务（{tasks.length} 件）：
              </div>
              <ol className="space-y-1.5 pl-1">
                {tasks.map((task, idx) => (
                  <li key={`${task.title}-${idx}`} className="flex items-center gap-2 text-[13px]">
                    <span className="text-white/50 text-[12px] w-4">{idx + 1}.</span>
                    <span className="text-white/90 font-medium">{task.title}</span>
                    {task.due_date && (
                      <span className="text-[11px] text-white/50">· 截止日：{task.due_date}</span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {payload.why && (
        <details className="group cursor-pointer">
          <summary className="inline-flex items-center gap-1.5 text-[12px] text-white/50 hover:text-white/60 transition-colors select-none">
            <ChevronRight className="w-3 h-3 transition-transform group-open:rotate-90" />
            提炼依据
          </summary>
          <div className="mt-1.5 text-[12px] text-white/50">{payload.why}</div>
        </details>
      )}

      <div className="text-[11px] text-white/50 pt-1">
        来自计划跟进对话。批准将直接落地修改并留下不可逆台账。
      </div>
    </div>
  );
}
