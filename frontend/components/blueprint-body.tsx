"use client";

import { type BlueprintPayload, type Proposal } from "@/lib/api";

export function normalize(raw: string[], stages: BlueprintPayload["stages"]): string[] {
  const whole = new Set<number>();
  const tasks = new Map<number, Set<number>>();
  for (const item of raw) {
    const [head, tail] = String(item).trim().split(".");
    if (!/^\d+$/.test(head)) continue;
    const index = Number(head);
    if (index >= stages.length) continue;
    if (tail === undefined) {
      whole.add(index);
      continue;
    }
    if (!/^\d+$/.test(tail)) continue;
    const taskIndex = Number(tail);
    if (taskIndex >= (stages[index].tasks ?? []).length) continue;
    const bucket = tasks.get(index) ?? new Set<number>();
    bucket.add(taskIndex);
    tasks.set(index, bucket);
  }

  const next: string[] = [];
  stages.forEach((stage, index) => {
    if (whole.has(index)) {
      next.push(String(index));
      return;
    }
    const ticked = tasks.get(index);
    if (ticked === undefined || ticked.size === 0) return;
    const total = (stage.tasks ?? []).length;
    if (total > 0 && ticked.size === total) {
      next.push(String(index));
      return;
    }
    for (const taskIndex of [...ticked].sort((left, right) => left - right)) {
      next.push(`${index}.${taskIndex}`);
    }
  });
  return next;
}

export function selectionOf(
  proposal: Proposal,
  selections: Record<number, string[]>,
): string[] {
  const stored = selections[proposal.id];
  if (stored !== undefined) return stored;
  const payload = proposal.payload as unknown as BlueprintPayload;
  return (payload.stages ?? []).map((_, index) => String(index));
}

export function BlueprintBody({
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
    <div style={{ marginTop: "10px" }}>
      <div
        style={{
          background: "var(--bg-subtle)",
          padding: "10px 14px",
          borderRadius: "var(--radius-sm)",
          marginBottom: "14px",
          fontSize: "13px",
        }}
      >
        <div>
          <strong>所属计划：</strong>计划 #{payload.plan_id}
          {payload.candidate_id !== undefined && `（源自候选 #${payload.candidate_id}）`}
        </div>
        <div style={{ marginTop: "4px" }}>
          <strong>核心目标：</strong>
          <span>{payload.goal}</span>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {stages.map((item, index) => {
          const isStageSelected = whole(index);
          return (
            <div
              key={`${item.title}-${index}`}
              style={{
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "12px",
                background: isStageSelected ? "#fff" : "var(--bg-subtle)",
              }}
            >
              <div className="flex-row gap-sm" style={{ marginBottom: "6px" }}>
                <input
                  type="checkbox"
                  id={`stage-check-${proposal.id}-${index}`}
                  checked={isStageSelected}
                  onChange={() => toggleStage(index)}
                  style={{ width: "16px", height: "16px" }}
                />
                <label
                  htmlFor={`stage-check-${proposal.id}-${index}`}
                  style={{ fontSize: "14px", fontWeight: 600, cursor: "pointer", margin: 0 }}
                >
                  阶段 {index + 1}：{item.title}
                </label>
              </div>

              {item.why && (
                <div style={{ fontSize: "12px", color: "var(--text-muted)", marginLeft: "24px", marginBottom: "4px" }}>
                  <strong>设置依据：</strong>{item.why}
                </div>
              )}
              <div style={{ fontSize: "12px", color: "var(--text-main)", marginLeft: "24px", marginBottom: "8px" }}>
                <strong>阶段交付物：</strong>
                {item.deliverable || <span style={{ color: "var(--text-muted)" }}>未指明</span>}
              </div>

              {(item.tasks ?? []).length > 0 && (
                <div style={{ marginLeft: "24px", paddingTop: "6px", borderTop: "1px dashed var(--border)" }}>
                  <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", marginBottom: "4px" }}>
                    拆解任务项（可单独选择）：
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                    {(item.tasks ?? []).map((task, taskIndex) => {
                      const isTaskSelected = tickedTask(index, taskIndex);
                      return (
                        <div key={`${task.title}-${taskIndex}`} className="flex-row gap-sm">
                          <input
                            type="checkbox"
                            id={`task-check-${proposal.id}-${index}-${taskIndex}`}
                            checked={isTaskSelected}
                            onChange={() => toggleTask(index, taskIndex)}
                            style={{ width: "14px", height: "14px" }}
                          />
                          <label
                            htmlFor={`task-check-${proposal.id}-${index}-${taskIndex}`}
                            style={{ fontSize: "13px", fontWeight: 400, cursor: "pointer", margin: 0 }}
                          >
                            {task.title}
                          </label>
                          {task.due_date && (
                            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                              (建议到期日：{task.due_date})
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: "12px",
          padding: "8px 12px",
          background: "var(--primary-light)",
          borderRadius: "var(--radius-sm)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "12px",
          color: "var(--primary)",
        }}
      >
        <span>
          <strong>当前选区：</strong>{tickedStages} / {stages.length} 个阶段，{tickedTaskCount} / {taskTotal} 件任务
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          同名已有阶段将就地复用并挂载新任务
        </span>
      </div>
    </div>
  );
}
