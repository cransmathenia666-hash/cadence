import { Check } from "lucide-react";
import { type BlueprintPayload, type Proposal } from "@/lib/api";
import { normalize } from "@/components/blueprint-body";

export function BlueprintSection({
  proposal,
  selection,
  onSelection,
}: {
  proposal: Proposal;
  selection: string[];
  onSelection: (value: string[]) => void;
}) {
  const payload = proposal.payload as unknown as BlueprintPayload;
  const stages = payload.stages ?? [];
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
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-white/40">
        <div>
          所属计划：
          <span className="text-white/70 font-medium">
            计划 #{payload.plan_id}
            {payload.candidate_id !== undefined && `（源自候选 #${payload.candidate_id}）`}
          </span>
        </div>
        <span className="opacity-30 hidden sm:inline">|</span>
        <div>
          核心目标：
          <span className="text-white/80">{payload.goal}</span>
        </div>
      </div>

      <div className="text-[12px] text-white/50">
        此蓝图来自对话式规划。勾选的项目才会新建入计划树，未勾选项直接舍弃。同名阶段将自动复用。
      </div>

      {/* 阶段/任务树 */}
      <div className="space-y-2.5">
        {stages.map((item, index) => {
          const isStageSelected = whole(index);
          return (
            <div
              key={`${item.title}-${index}`}
              className={`rounded-xl p-3 transition-colors ${
                isStageSelected ? "bg-white/[0.05]" : "bg-transparent"
              } hover:bg-white/[0.03]`}
            >
              <div
                className="flex items-center gap-2.5 cursor-pointer select-none"
                onClick={() => toggleStage(index)}
              >
                <div
                  className={`w-4 h-4 rounded-[4px] border flex items-center justify-center shrink-0 transition-colors ${
                    isStageSelected
                      ? "bg-white border-white text-black"
                      : "bg-white/[0.08] border-white/20"
                  }`}
                >
                  {isStageSelected && <Check className="w-3 h-3 text-black stroke-[3]" />}
                </div>
                <span className="text-[14px] font-semibold text-white/90">
                  阶段 {index + 1}：{item.title}
                </span>
              </div>

              {item.why && (
                <div className="text-[12px] text-white/40 pl-6 mt-1">
                  <span className="text-white/30">设置依据：</span>
                  {item.why}
                </div>
              )}
              <div className="text-[12px] text-white/50 pl-6 mt-0.5">
                <span className="text-white/30">阶段交付物：</span>
                {item.deliverable || <span className="text-white/30">未指明</span>}
              </div>

              {(item.tasks ?? []).length > 0 && (
                <div className="pl-6 mt-2.5 pt-2 border-t border-dashed border-white/[0.06] space-y-1.5">
                  <div className="text-[11px] uppercase tracking-wider text-white/30 font-medium mb-1">
                    拆解任务项（可单独选择）
                  </div>
                  {(item.tasks ?? []).map((task, taskIndex) => {
                    const isTaskSelected = tickedTask(index, taskIndex);
                    return (
                      <div
                        key={`${task.title}-${taskIndex}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleTask(index, taskIndex);
                        }}
                        className={`flex items-center gap-2.5 py-1 px-2 rounded-lg cursor-pointer transition-colors ${
                          isTaskSelected ? "bg-white/[0.03] text-white/90" : "text-white/50 hover:bg-white/[0.02]"
                        }`}
                      >
                        <div
                          className={`w-3.5 h-3.5 rounded-[3px] border flex items-center justify-center shrink-0 transition-colors ${
                            isTaskSelected
                              ? "bg-white border-white text-black"
                              : "bg-white/[0.08] border-white/20"
                          }`}
                        >
                          {isTaskSelected && <Check className="w-2.5 h-2.5 text-black stroke-[3]" />}
                        </div>
                        <span className="text-[13px]">{task.title}</span>
                        {task.due_date && (
                          <span className="text-[11px] text-white/35">
                            (建议到期日：{task.due_date})
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 实时勾选计数 */}
      <div className="pt-2 flex flex-wrap items-center justify-between text-[12px] text-white/50 border-t border-white/[0.04]">
        <div>
          <span className="text-white/40">已勾选：</span>
          <span className="text-white/80 font-medium">{tickedStages} / {stages.length}</span> 个阶段 ·{" "}
          <span className="text-white/80 font-medium">{tickedTaskCount} / {taskTotal}</span> 件任务
        </div>
        <div className="text-[11px] text-white/40">
          同名已有阶段将就地复用并挂载新任务
        </div>
      </div>
    </div>
  );
}
