"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PlanTreeView } from "@/components/plan-tree";
import {
  ApiError,
  checkTask,
  getPlan,
  skipTask,
  submitDeliverable,
  type PlanTree,
} from "@/lib/api";

/**
 * T8：计划表页面（T23 起承担任务层的三个写动作）。
 *
 * 取数、加载态、错误态在这里管；"画成什么样"与动作按钮在 `components/plan-tree.tsx`。
 * 数据全部来自 `GET /api/plan`——落后量、当前阶段、进度、阶段是否完成都是后端算好的，
 * 前端不做任何业务计算（SPEC 第 10 节的铁律）。
 * 三个写动作（打勾 / 跳过 / 提交交付物）走完后重新取一次计划，让页面回到真实状态。
 */
export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 首次进入取一次计划：setState 放在 .then 回调里，而不是 effect 体内同步调用——
  // 否则会被 eslint 的 react-hooks/set-state-in-effect 拦下。
  useEffect(() => {
    getPlan()
      .then(setTree)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取计划时出了意外错误"),
      );
  }, []);

  /** 写动作的统一外壳：动完重新取计划；失败把中文原因摆到页面上。 */
  async function run(action: () => Promise<unknown>) {
    setActing(true);
    setActionError(null);
    try {
      await action();
      setTree(await getPlan());
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "操作失败，原因不明");
    } finally {
      setActing(false);
    }
  }

  return (
    <main>
      <h1>计划表</h1>
      <p>
        <Link href="/new">建计划 / 建阶段与任务</Link>
        {" · "}
        <Link href="/report">提交报告</Link>
        {" · "}
        <Link href="/candidates">候选清单（学什么方向）</Link>
        {" · "}
        <Link href="/proposals">待裁定提案（判一份资料）</Link>
        {" · "}
        <Link href="/providers">LLM 提供商</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      {error !== null && (
        <p role="alert">
          <strong>取计划失败：</strong>
          {error}
        </p>
      )}

      {error === null && tree === null && <p role="status">正在从后端取计划…</p>}

      {tree !== null && (
        <PlanTreeView
          tree={tree}
          busy={acting}
          error={actionError}
          onTask={(taskId, action, reason) =>
            run(() => (action === "check" ? checkTask(taskId) : skipTask(taskId, reason ?? "")))
          }
          onDeliverable={(stageId, url, note) => run(() => submitDeliverable(stageId, url, note))}
        />
      )}
    </main>
  );
}
