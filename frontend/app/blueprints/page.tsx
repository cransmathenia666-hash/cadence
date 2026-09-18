"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  decideProposal,
  listProposals,
  type BlueprintPayload,
  type Proposal,
} from "@/lib/api";

/**
 * T29：蓝图待批的**独立页**。
 *
 * 用户明确否掉了「并进计划页」那个方案：蓝图是一棵待你勾选的树，它跟计划表上
 * 「已经建好的树」是两回事，混在一页里最容易看错自己在改哪一个。
 *
 * 一版蓝图只在同一计划里存在一份待裁定的（新版会顶掉旧版，见决策 36），所以这页
 * 通常只有一两条。批准 = **按勾选建树**：勾中的进计划，没勾的直接丢弃。
 */

/** 勾选的规范形状：`"2"` = 第 3 个阶段整段；`"2.1"` = 其中第 2 件任务（下标从 0 起）。 */
function normalize(raw: string[], stages: BlueprintPayload["stages"]): string[] {
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
    // 逐条勾满就等于整段（后端两种情况建出来的东西完全一样），收敛成一种写法，
    // 免得「三件都勾着、阶段那格却空着」这种看着像漏选的假象
    if (total > 0 && ticked.size === total) {
      next.push(String(index));
      return;
    }
    for (const taskIndex of [...ticked].sort((a, b) => a - b)) {
      next.push(`${index}.${taskIndex}`);
    }
  });
  return next;
}

export default function BlueprintsPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [selections, setSelections] = useState<Record<number, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);

  // refresh 声明在 effect 之前——否则 lint 会拦「先用后声明」
  function refresh() {
    listProposals("plan_blueprint")
      .then(setProposals)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取蓝图时出了意外错误"),
      );
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onDecide(proposal: Proposal, approved: boolean) {
    setBusy(true);
    setError(null);
    const payload = proposal.payload as unknown as BlueprintPayload;
    const selected =
      selections[proposal.id] ?? (payload.stages ?? []).map((_, index) => String(index));
    try {
      const done = await decideProposal(proposal.id, {
        approved,
        reason: approved ? undefined : "这版树不对",
        selected,
      });
      setNotes((previous) => [
        approved
          ? `已批准：计划 #${done.built?.plan_id ?? payload.plan_id} 里建了 ` +
            `${done.built?.stages.length ?? 0} 个新阶段、${done.built?.tasks.length ?? 0} 件任务` +
            `${done.built?.notes.length ? `。${done.built.notes.join("；")}` : "。"}`
          : "已驳回，理由进了台账——想要别的可以回对话里再出一版。",
        ...previous,
      ]);
      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
      refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>蓝图待批（{proposals === null ? "…" : proposals.length} 版）</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/candidates">候选清单与规划对话</Link>
        {" · "}
        <Link href="/judge">判一份资料</Link>
        {" · "}
        <Link href="/proposals">待裁定提案</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          蓝图是在<Link href="/candidates">候选清单</Link>页的规划对话里出的：聊清意向后
          「够了，出方案」，它给你一棵树（阶段 → 任务）。**勾中的才会建进计划**，
          没勾的直接丢弃；同名阶段不会重复建（任务挂到已那条下面）。
          同一个计划只有一份待裁定蓝图，再出一版会顶掉这一版。
        </small>
      </p>

      {error !== null && (
        <p role="alert">
          <strong>失败：</strong>
          {error}
        </p>
      )}
      {notes.length > 0 && (
        <ul role="status">
          {notes.map((text, index) => (
            <li key={index}>
              <small>{text}</small>
            </li>
          ))}
        </ul>
      )}
      {proposals !== null && proposals.length === 0 && (
        <p role="status">
          没有待批的蓝图——去<Link href="/candidates">候选清单</Link>采纳一条方向、聊几句，
          让它出一版。
        </p>
      )}

      {(proposals ?? []).map((proposal) => {
        const payload = proposal.payload as unknown as BlueprintPayload;
        const stages = payload.stages ?? [];
        const selection = selections[proposal.id] ?? stages.map((_, index) => String(index));
        const whole = (index: number) => selection.includes(String(index));
        const tickedTask = (index: number, taskIndex: number) =>
          whole(index) || selection.includes(`${index}.${taskIndex}`);
        const tickedStages = stages.filter((_, index) =>
          selection.some((item) => item === String(index) || item.startsWith(`${index}.`)),
        ).length;
        const tickedTasks = stages.reduce(
          (total, stage, index) =>
            total +
            (stage.tasks ?? []).filter(
              (_, taskIndex) =>
                whole(index) || selection.includes(`${index}.${taskIndex}`),
            ).length,
          0,
        );

        function update(raw: string[]) {
          setSelections((previous) => ({
            ...previous,
            [proposal.id]: normalize(raw, stages),
          }));
        }

        function toggleStage(index: number) {
          update(whole(index) ? selection.filter((item) => item !== String(index)) : [...selection, String(index)]);
        }

        function toggleTask(index: number, taskIndex: number) {
          const path = `${index}.${taskIndex}`;
          if (whole(index)) {
            update([
              ...selection.filter((item) => item !== String(index)),
              ...(stages[index].tasks ?? [])
                .map((_, other) => `${index}.${other}`)
                .filter((other) => other !== path),
            ]);
            return;
          }
          update(
            selection.includes(path)
              ? selection.filter((item) => item !== path)
              : [...selection, path],
          );
        }

        return (
          <section key={proposal.id}>
            <h2>
              计划 #{payload.plan_id} 的待批蓝图
              <small>（提案 #{proposal.id}，提出于 {proposal.created_at.slice(0, 16).replace("T", " ")}）</small>
            </h2>
            <p>
              <small>
                目标：{payload.goal}
                <br />
                当前勾选：<strong>{tickedStages}</strong> / {stages.length} 个阶段、
                <strong>{tickedTasks}</strong> /{" "}
                {stages.reduce((total, item) => total + (item.tasks?.length ?? 0), 0)} 件任务
                （勾了阶段 = 连它的任务一起要；一件没勾的阶段不会建）
              </small>
            </p>
            <ol>
              {stages.map((stage, index) => (
                <li key={`${stage.title}-${index}`}>
                  <label>
                    <input type="checkbox" checked={whole(index)} onChange={() => toggleStage(index)} />
                    <strong>{stage.title}</strong>
                  </label>
                  {stage.why !== "" && (
                    <>
                      <br />
                      <small>{stage.why}</small>
                    </>
                  )}
                  <br />
                  <small>要交的东西：{stage.deliverable}</small>
                  {(stage.tasks ?? []).length > 0 && (
                    <ul>
                      {(stage.tasks ?? []).map((task, taskIndex) => (
                        <li key={`${task.title}-${taskIndex}`}>
                          <label>
                            <input
                              type="checkbox"
                              checked={tickedTask(index, taskIndex)}
                              onChange={() => toggleTask(index, taskIndex)}
                            />
                            {task.title}
                          </label>
                          <small>
                            {task.due_date === null ? "（没定日期）" : `（${task.due_date}）`}
                          </small>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
            <p>
              <button
                type="button"
                onClick={() => onDecide(proposal, true)}
                disabled={busy || selection.length === 0}
                title={selection.length === 0 ? "至少要勾一个阶段或任务" : undefined}
              >
                批准（按勾选建树）
              </button>{" "}
              <button type="button" onClick={() => onDecide(proposal, false)} disabled={busy}>
                驳回
              </button>
            </p>
          </section>
        );
      })}
    </main>
  );
}