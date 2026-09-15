"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PlanTreeView } from "@/components/plan-tree";
import { ApiError, getPlan, type PlanTree } from "@/lib/api";

/**
 * T8：计划表页面。
 *
 * 取数、加载态、错误态在这里管；"画成什么样"在 `components/plan-tree.tsx`。
 * 数据全部来自 `GET /api/plan`——落后量、当前阶段、进度都是后端算好的，
 * 前端不做任何业务计算（SPEC 第 10 节的铁律）。
 */
export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);

  // setState 放在 .then 回调里，而不是 effect 体内同步调用——
  // 否则会被 eslint 的 react-hooks/set-state-in-effect 拦下。
  useEffect(() => {
    getPlan()
      .then(setTree)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取计划时出了意外错误"),
      );
  }, []);

  return (
    <main>
      <h1>计划表</h1>
      <p>
        <Link href="/new">建计划 / 建节点</Link>
        {" · "}
        <Link href="/report">提交报告</Link>
      </p>

      {error !== null && (
        <p role="alert">
          <strong>取计划失败：</strong>
          {error}
        </p>
      )}

      {error === null && tree === null && <p role="status">正在从后端取计划…</p>}

      {tree !== null && <PlanTreeView tree={tree} />}
    </main>
  );
}
