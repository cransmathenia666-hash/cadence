"use client";

import { useEffect, useState } from "react";

import { ApiError, getPlan, type PlanTree } from "@/lib/api";

/**
 * 切片 1 的验收视图：证明浏览器能直接读到 FastAPI 的数据。
 *
 * 这一页**故意**只把后端返回的 JSON 原样打出来——它要验的是「两个进程真的能对话」，
 * 不是界面好不好看。正式的界面（节点树、落后量、当前阶段）在 T8 做。
 *
 * 为什么是 `"use client"`：Next.js 的页面默认在服务器上渲染，而这一页要在**浏览器**里
 * 发请求（这样才能用浏览器开发者工具的 Network 面板看到那条请求，也才走 CORS）。
 * 用到 `useState`/`useEffect` 的组件必须是客户端组件。
 */
export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPlan()
      .then(setTree)
      .catch((cause: unknown) => {
        setError(cause instanceof ApiError ? cause.message : "取计划时出了意外错误");
      });
  }, []);

  return (
    <main>
      <h1>cadence 前端 ↔ 后端 联通验证</h1>
      <p>
        这一页确认浏览器能直接读到后端的数据。正式的界面在 T8，这里只把返回的 JSON 原样显示。
      </p>

      {error !== null && (
        <p role="alert">
          <strong>取数据失败：</strong>
          {error}
        </p>
      )}

      {error === null && tree === null && <p role="status">正在从后端取计划…</p>}

      {tree !== null && (
        <>
          {tree.plan === null && (
            <p role="status">
              后端连上了，但库里还没有计划。先去后端的 <code>/docs</code> 建一个，再刷新这页。
            </p>
          )}
          <pre>{JSON.stringify(tree, null, 2)}</pre>
        </>
      )}
    </main>
  );
}
