"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PlanDialogue } from "@/components/plan-dialogue";
import { PlanTreeView } from "@/components/plan-tree";
import {
  ApiError,
  checkTask,
  closePlan,
  getPlan,
  listPlans,
  pausePlan,
  reopenPlan,
  skipTask,
  submitDeliverable,
  voidPlan,
  type PlanSummary,
  type PlanTree,
} from "@/lib/api";

/**
 * T8 计划表页面；T23 起承担任务层的写动作；T24 起承担**多计划切换与生命周期**；
 * T27 起生命周期拆成四态（暂停 / 收尾 / 作废）并补上**历史计划**这个出口。
 *
 * T28 起页面底部还有一块**计划级对话**（`components/plan-dialogue.tsx`）：蓝图落地之后
 * 接着聊这个计划——那一段只说话与提炼档案提案，改不了计划结构。
 *
 * 取数、加载态、错误态在这里管；"画成什么样"与动作按钮在 `components/plan-tree.tsx`。
 * 数据全部来自 `GET /api/plan`（T24 起带 `plan_id`）——落后量、当前阶段、进度、
 * 阶段是否完成都是后端算好的，前端不做任何业务计算（SPEC 第 10 节的铁律）。
 *
 * 生命周期三扇门的分界是**能不能回头**：暂停是暂时不做（能「继续做」）、收尾是做完了
 * （能「重开」），作废是「这件事根本不该做」——单向门，只给理由不留退路。
 * 三种都不再进行中，所以都从切换器挪进「历史计划」，理由与时间一直留在库里。
 */

/** 计划状态的说法。四态见 SPEC 决策 33（T27 拆分）。 */
const PLAN_STATUS_LABELS: Record<string, string> = {
  active: "进行中",
  paused: "暂时不做",
  closed: "做完了",
  void: "已作废",
};
export default function Home() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  /** 所有计划（含暂停 / 收尾 / 作废）——历史段从它里面挑出不再进行中的那些。 */
  const [allPlans, setAllPlans] = useState<PlanSummary[]>([]);
  /** 正在看哪个计划；null = 让后端给最新建的那个 active 计划。 */
  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** 正在填作废理由的那个计划。 */
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");

  // 首次进入：取计划列表（一份只含进行中的、一份全的）；之后每次切换计划重新取计划树。
  // setState 一律放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）。
  useEffect(() => {
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
    listPlans(true)
      .then(setAllPlans)
      .catch(() => setAllPlans([]));
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

  /** 写动作的统一外壳：动完重新取计划与两份列表；失败把中文原因摆到页面上。 */
  async function run(action: () => Promise<unknown>) {
    setActing(true);
    setActionError(null);
    try {
      await action();
      setTree(await getPlan(selectedId ?? undefined));
      setPlans(await listPlans());
      setAllPlans(await listPlans(true));
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "操作失败，原因不明");
    } finally {
      setActing(false);
    }
  }

  /** 不再进行中的那些，按状态排在历史段里。 */
  const endedPlans = allPlans.filter((item) => item.status !== "active");

  return (
    <main>
      <h1>计划表</h1>
      <p>
        <Link href="/new">建计划 / 建阶段与任务</Link>
        {" · "}
        <Link href="/report">提交报告</Link>
        {" · "}
        <Link href="/candidates">候选清单与规划对话</Link>
        {" · "}
        <Link href="/judge">判一份资料</Link>
        {" · "}
        <Link href="/blueprints">蓝图待批</Link>
        {" · "}
        <Link href="/proposals">待裁定提案</Link>
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
          <summary>计划管理（暂停 / 收尾 / 作废）</summary>
          <p>
            <small>
              暂停 = 暂时不做（随时能「继续做」回来，理由可选）；收尾 = 做完了（能「重开」，
              理由可选）；作废 = <strong>这件事根本不该做</strong>——它是单向门，
              <strong>理由必填</strong>，之后只能新建计划重做。三者都会从上面的下拉里消失，
              理由与时间留在下面的「历史计划」里。
            </small>
          </p>
          <ul>
            {plans.map((item) => (
              <li key={item.id}>
                #{item.id}「{item.goal}」{" "}
                <button type="button" onClick={() => run(() => closePlan(item.id))} disabled={acting}>
                  收尾
                </button>{" "}
                <button type="button" onClick={() => run(() => pausePlan(item.id))} disabled={acting}>
                  暂停
                </button>{" "}
                {voidingId === item.id ? (
                  <>
                    <input
                      aria-label={`作废计划 #${item.id} 的理由`}
                      value={voidReason}
                      onChange={(event) => setVoidReason(event.target.value)}
                      placeholder="为什么这件事根本不该做（必填）"
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

        <details>
          <summary>历史计划（{endedPlans.length}）</summary>
          <p>
            <small>
              不再进行中的计划都在这里。暂停的给「继续做」、收尾的给「重开」——两者都是
              同一个动作（放回进行中），做完就从这一段挪回上面的下拉里。
            </small>
          </p>
          {endedPlans.length === 0 ? (
            <p role="status">还没有进了历史的计划。</p>
          ) : (
            <ul>
              {endedPlans.map((item) => (
                <li key={item.id}>
                  #{item.id}「{item.goal}」〔{PLAN_STATUS_LABELS[item.status] ?? item.status}〕
                  {item.ended_at !== null && (
                    <> {item.ended_at.slice(0, 16).replace("T", " ")}</>
                  )}
                  {item.ended_reason !== null && <> —— {item.ended_reason}</>}{" "}
                  {item.status === "paused" && (
                    <button
                      type="button"
                      onClick={() => run(() => reopenPlan(item.id))}
                      disabled={acting}
                    >
                      继续做
                    </button>
                  )}
                  {item.status === "closed" && (
                    <button
                      type="button"
                      onClick={() => run(() => reopenPlan(item.id))}
                      disabled={acting}
                    >
                      重开
                    </button>
                  )}
                  {item.status === "void" && (
                    <small>作废是单向门，要重新做就新建一个计划</small>
                  )}
                </li>
              ))}
            </ul>
          )}
        </details>
      </section>

      {error !== null && (
        <p role="alert">
          <strong>取计划失败：</strong>
          {error}
        </p>
      )}

      {error === null && tree === null && <p role="status">正在从后端取计划…</p>}

      {tree !== null && tree.plan !== null && tree.behind_reason !== null && (
        <section>
          <h2>落后了，提醒一句</h2>
          <p>
            <strong>{tree.behind_reason}。</strong>
          </p>
          <p>
            <small>
              T29 起这类提醒不再是要你裁定的条目——怎么做由你自己定，下面三个方向只是建议：
            </small>
          </p>
          <ul>
            {(tree.advice ?? []).map((item) => (
              <li key={item.kind}>
                <strong>{item.label}</strong>：{item.detail}
              </li>
            ))}
          </ul>
        </section>
      )}

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

      {/* T28：蓝图落地之后接着聊。key 带上计划号——换计划时把组件整个换掉，
          免得上一段对话的消息串到另一个计划底下。 */}
      {tree !== null && tree.plan !== null && (
        <PlanDialogue key={tree.plan.id} planId={tree.plan.id} />
      )}
    </main>
  );
}
