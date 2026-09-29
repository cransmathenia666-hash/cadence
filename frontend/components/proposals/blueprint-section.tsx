import { useState } from "react";
import { Check, ChevronDown, ChevronRight } from "lucide-react";
import { type BlueprintPayload, type Proposal } from "@/lib/api";
import { normalize } from "@/components/blueprint-body";

type BlueprintStage = BlueprintPayload["stages"][number];

export function BlueprintSection({
  proposal,
  selection,
  onSelection,
  planName,
}: {
  proposal: Proposal;
  selection: string[];
  onSelection: (value: string[]) => void;
  planName?: (planId: number) => string | null;
}) {
  const payload = proposal.payload as unknown as BlueprintPayload;
  const stages = payload.stages ?? [];
  // 长蓝图默认收起：阶段行只留「勾选框 + 阶段名 + 任务数徽标」，任务列表按阶段展开
  const [openStages, setOpenStages] = useState<ReadonlySet<number>>(new Set());
  const stageOpen = (index: number) => openStages.has(index);
  const toggleStageOpen = (index: number) =>
    setOpenStages((previous) => {
      const next = new Set(previous);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  const stageExpandable = (item: BlueprintStage) =>
    (item.tasks ?? []).length > 0 || Boolean(item.why || item.deliverable);

  const whole = (index: number) => selection.includes(String(index));
  const tickedTask = (index: number, taskIndex: number) =>
    whole(index) || selection.includes(`${index}.${taskIndex}`);

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

  return (
    <div className="space-y-4">
      {/* 计划归属 + 核心目标一行元数据开头 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-white/50">
        <div>
          所属计划：
          <span className="text-white/70 font-medium">
            {planName?.(Number(payload.plan_id)) ?? `计划 #${payload.plan_id}`}
          </span>
        </div>
        <span className="opacity-30 hidden sm:inline">|</span>
        <div>
          核心目标：
          <span className="text-white/80">{payload.goal}</span>
        </div>
      </div>

      <div className="text-[12px] text-white/70">
        勾选要纳入计划的阶段或任务；未勾选项不会创建。同名阶段将自动复用。
      </div>

      {/* 阶段/任务树：阶段行收成摘要，任务列表按阶段折叠展开 */}
      <div className="space-y-2.5">
        {stages.map((item, index) => {
          const isStageSelected = whole(index);
          const taskCount = (item.tasks ?? []).length;
          const expandable = stageExpandable(item);
          const open = expandable && stageOpen(index);
          return (
            <div
              key={`${item.title}-${index}`}
              className={`rounded-xl p-3 transition-colors ${
                isStageSelected ? "bg-white/[0.05]" : "bg-transparent"
              } hover:bg-white/[0.03]`}
            >
              {/* 阶段行：勾选框（切换勾选）+ 阶段名（切换展开）+ 任务数徽标 */}
              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={isStageSelected}
                  aria-label={`勾选阶段 ${index + 1}：${item.title}`}
                  onClick={() => toggleStage(index)}
                  className="shrink-0 cursor-pointer rounded-[4px] focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/50"
                >
                  <div
                    className={`w-4 h-4 rounded-[4px] border flex items-center justify-center transition-colors ${
                      isStageSelected
                        ? "bg-white border-white text-black"
                        : "bg-white/[0.08] border-white/20"
                    }`}
                  >
                    {isStageSelected && <Check className="w-3 h-3 text-black stroke-[3]" />}
                  </div>
                </button>

                {expandable ? (
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={`blueprint-stage-${proposal.id}-${index}`}
                    onClick={() => toggleStageOpen(index)}
                    className="group/stage flex min-w-0 flex-1 items-center gap-2.5 text-left cursor-pointer select-none rounded-lg focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/50"
                  >
                    <span className="text-[14px] font-semibold text-white/90 truncate">
                      阶段 {index + 1}：{item.title}
                    </span>
                    {taskCount > 0 && (
                      <span className="shrink-0 px-2 py-0.5 rounded-full bg-white/[0.06] border border-white/[0.06] text-[11px] font-medium text-white/70">
                        {taskCount} 项任务
                      </span>
                    )}
                    <ChevronDown
                      className={`w-3.5 h-3.5 shrink-0 ml-auto text-white/50 transition-transform duration-200 motion-reduce:transition-none group-hover/stage:text-white/70 ${
                        open ? "rotate-180" : ""
                      }`}
                    />
                  </button>
                ) : (
                  <div className="flex min-w-0 flex-1 items-center gap-2.5">
                    <span className="text-[14px] font-semibold text-white/90 truncate">
                      阶段 {index + 1}：{item.title}
                    </span>
                  </div>
                )}
              </div>

              {/* 折叠区：依据与交付物 + 任务列表；收起时不参与焦点 */}
              {expandable && (
                <div
                  id={`blueprint-stage-${proposal.id}-${index}`}
                  className="grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none"
                  style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
                  inert={!open}
                >
                  <div className="overflow-hidden">
                    {(item.why || item.deliverable) && (
                      <details className="group cursor-pointer pl-6 mt-1">
                        <summary className="inline-flex items-center gap-1.5 text-[12px] text-white/50 hover:text-white/60 transition-colors select-none">
                          <ChevronRight className="w-3 h-3 transition-transform group-open:rotate-90" />
                          依据与交付物
                        </summary>
                        <div className="mt-1.5 space-y-0.5">
                          {item.why && (
                            <div className="text-[12px] text-white/50">
                              <span className="text-white/50">设置依据：</span>
                              {item.why}
                            </div>
                          )}
                          <div className="text-[12px] text-white/50">
                            <span className="text-white/50">阶段交付物：</span>
                            {item.deliverable || <span className="text-white/50">未指明</span>}
                          </div>
                        </div>
                      </details>
                    )}

                    {taskCount > 0 && (
                      <div className="pl-6 mt-2.5 pt-2 border-t border-dashed border-white/[0.06] space-y-1.5">
                        <div className="text-[11px] uppercase tracking-wider text-white/50 font-medium mb-1">
                          拆解任务项（可单独选择）
                        </div>
                        {(item.tasks ?? []).map((task, taskIndex) => {
                          const isTaskSelected = tickedTask(index, taskIndex);
                          return (
                            <button
                              key={`${task.title}-${taskIndex}`}
                              type="button"
                              role="checkbox"
                              aria-checked={isTaskSelected}
                              aria-label={`勾选任务：${task.title}`}
                              onClick={() => toggleTask(index, taskIndex)}
                              className={`flex w-full items-center gap-2.5 py-1 px-2 rounded-lg cursor-pointer text-left transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/50 ${
                                isTaskSelected
                                  ? "bg-white/[0.03] text-white/90"
                                  : "text-white/70 hover:bg-white/[0.02]"
                              }`}
                            >
                              <div
                                className={`w-3.5 h-3.5 rounded-[3px] border flex items-center justify-center shrink-0 transition-colors ${
                                  isTaskSelected
                                    ? "bg-white border-white text-black"
                                    : "bg-white/[0.08] border-white/20"
                                }`}
                              >
                                {isTaskSelected && (
                                  <Check className="w-2.5 h-2.5 text-black stroke-[3]" />
                                )}
                              </div>
                              <span className="text-[13px]">{task.title}</span>
                              {task.due_date && (
                                <span className="text-[11px] text-white/50">
                                  (建议到期日：{task.due_date})
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 实时勾选计数（决策依据，保持常显） */}
      <div className="pt-2 flex flex-wrap items-center text-[12px] text-white/70 border-t border-white/[0.04]">
        <div>
          <span className="text-white/70">已勾选：</span>
          <span className="text-white/90 font-medium">{tickedStages} / {stages.length}</span> 个阶段 ·{" "}
          <span className="text-white/90 font-medium">{tickedTaskCount} / {taskTotal}</span> 件任务
        </div>
      </div>
    </div>
  );
}
