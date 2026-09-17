"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PlanTreeView } from "@/components/plan-tree";
import {
  ApiError,
  checkTask,
  closePlan,
  getPlan,
  listPlans,
  skipTask,
  submitDeliverable,
  voidPlan,
  type PlanSummary,
  type PlanTree,
} from "@/lib/api";

/**
 * T8 计划表页面；T23 起承担任务层的写动作；T24 起承担**多计划切换与生命周期**。
 *
 * 取数、加载态、错误态在这里管；"画成什么样"与动作按钮在 `components/plan-tree.tsx`。
 * 数据全部来自 `GET /api/plan`（T24 起带 `plan_id`）——落后量、当前阶段、进度、
 * 阶段是否完成都是后端算好的，前端不做任何业务计算（SPEC 第 10 节的铁律）。
 *
 * 计划管理（收尾 / 作废）是 T24 新开的门：垃圾计划由此清场，作废要写理由（进台账）。
 */
export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  /** 正在看哪个计划；null = 让后端给最新建的那个 active 计划。 */
  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** 正在填作废理由的那个计划。 */
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");

  // 首次进入：取计划列表；之后每次切换计划重新取计划树。
  // setState 一律放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）。
  useEffect(() => {
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
  }, []);

  useEffect(() => {
    getPlan(selectedId ?? undefined)
      .then((data) => {
        setTree(data);
        setError(null);
      })
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取计划时出了意外错误"),
      );
  }, [selectedId]);

  /** 写动作的统一外壳：动完重新取计划与计划列表；失败把中文原因摆到页面上。 */
  async function run(action: () => Promise<unknown>) {
    setActing(true);
    setActionError(null);
    try {
      await action();
      setTree(await getPlan(selectedId ?? undefined));
      setPlans(await listPlans());
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

      <section>
        <h2>看哪个计划</h2>
        {plans.length === 0 ? (
          <p role="status">还没有进行中的计划——去「建计划」建一个。</p>
        ) : (
          <p>
            <label htmlFor="plan-switch">计划：</label>
            <select
              id="plan-switch"
              value={selectedId === null ? "" : String(selectedId)}
              onChange={(event) =>
                setSelectedId(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              <option value="">（最新建的那个）</option>
              {plans.map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id}：{item.goal}（{item.stages_finished}/{item.stages} 阶段完成）
                </option>
              ))}
            </select>
          </p>
        )}

        <details>
          <summary>计划管理（收尾 / 作废）</summary>
          <p>
            <small>
              收尾 = 做完了，进历史；作废 = 不算数了（试建的垃圾计划用这个），**理由必填**。
              两者都会从上面的下拉里消失，历史仍留在库里。
            </small>
          </p>
          <ul>
            {plans.map((item) => (
              <li key={item.id}>
                #{item.id}「{item.goal}」{" "}
                <button type="button" onClick={() => run(() => closePlan(item.id))} disabled={acting}>
                  收尾
                </button>{" "}
                {voidingId === item.id ? (
                  <>
                    <input
                      aria-label={`作废计划 #${item.id} 的理由`}
                      value={voidReason}
                      onChange={(event) => setVoidReason(event.target.value)}
                      placeholder="为什么作废（必填）"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        run(async () => {
                          await voidPlan(item.id, voidReason);
                          setVoidingId(null);
                          setVoidReason("");
                          if (selectedId === item.id) setSelectedId(null);
                        })
                      }
                      disabled={acting || voidReason.trim() === ""}
                    >
                      确认作废
                    </button>{" "}
                    <button type="button" onClick={() => setVoidingId(null)} disabled={acting}>
                      取消
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setVoidingId(item.id);
                      setVoidReason("");
                    }}
                    disabled={acting}
                  >
                    作废…
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      </section>

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
