"use client";

import { type BlueprintPayload, type Proposal } from "@/lib/api";

/**
 * 蓝图提案的正文：把 payload 里的那棵树画成可勾选的清单（T26 起，T29 从页面里抽出来）。
 *
 * 为什么抽出来：待裁定提案只有一页（SPEC 决策 29），蓝图、档案变更、资料判断都在这页上
 * 按 `kind` 分流渲染，勾选这套交互不该跟页面取数、裁定流程混在一个文件里。
 *
 * 勾选与提交的关系：**勾中的才建进计划，没勾的直接丢弃**——蓝图是版本化的，想要别的
 * 可以沿对话再出一版。
 */

/**
 * 勾选的规范形状：`"2"` = 第 3 个阶段整段；`"2.1"` = 其中第 2 件任务（下标从 0 起）。
 *
 * 为什么要收敛：`"4"`（整段）与 `"4.0","4.1","4.2"`（逐条勾满）在库里建出来的东西
 * **完全一样**，但显示会分叉——用户走查时就遇到「三件任务都勾着、阶段那格空着」，
 * 看着像漏选。所以选区只留一种写法：逐条勾满 → 收成整段；整段已在 → 丢掉它下面逐条
 * 的那些；这一段一件没勾 → 整段清掉。顺带把认不出 / 越界的路径丢掉（后端也会拒）。
 */
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

/** 这条蓝图当前该勾什么。没有记录时默认**整份都要**——不勾就点批准等于全采纳（后端也是这个口径）。 */
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

  /** 勾/取消一个阶段：整段都要，或整段全不要（逐条勾的那些一起清掉）。 */
  function toggleStage(index: number) {
    const others = selection.filter(
      (item) => item !== String(index) && !item.startsWith(`${index}.`),
    );
    onSelection(normalize(whole(index) ? others : [...others, String(index)], stages));
  }

  /** 勾/取消一件任务：整段被勾着时，先把它拆成逐条勾，再动这一条。 */
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
    <>
      <p>
        计划 #{payload.plan_id} 的这一版（{stages.length} 个阶段，{taskTotal} 件任务
        {payload.candidate_id !== undefined && `，出自候选 #${payload.candidate_id}`}）。
        <br />
        <small>目标：{payload.goal}</small>
      </p>
      <ol>
        {stages.map((item, index) => (
          <li key={`${item.title}-${index}`}>
            <label>
              <input
                type="checkbox"
                // 选区是规范形状（见 `normalize`），所以「整段」这一个标记就够判断
                checked={whole(index)}
                onChange={() => toggleStage(index)}
              />
              <strong>{item.title}</strong>
            </label>
            {item.why !== "" && (
              <>
                <br />
                <small>{item.why}</small>
              </>
            )}
            <br />
            <small>要交的东西：{item.deliverable}</small>
            {(item.tasks ?? []).length > 0 && (
              <ul>
                {(item.tasks ?? []).map((task, taskIndex) => (
                  <li key={`${task.title}-${taskIndex}`}>
                    <label>
                      <input
                        type="checkbox"
                        checked={tickedTask(index, taskIndex)}
                        onChange={() => toggleTask(index, taskIndex)}
                      />
                      {task.title}
                    </label>
                    <small>{task.due_date === null ? "（没定日期）" : `（${task.due_date}）`}</small>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      <p>
        <strong>
          当前勾选：{tickedStages} / {stages.length} 个阶段，{tickedTaskCount} / {taskTotal} 件任务
        </strong>
        <br />
        <small>
          勾了阶段 = 连它的任务一起要；一件任务都没勾的阶段不会建。同名阶段不会重复建——
          采纳候选时已经建了那个阶段，任务挂到它下面，「要交的东西」也按这一版里写的更新
          （改前改后进台账）。
        </small>
      </p>
    </>
  );
}