"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createNode,
  createPlan,
  getPlan,
  type NodeLevel,
  type PlanTree,
} from "@/lib/api";

/**
 * 建计划与建节点。
 *
 * 这两个写接口在 P1 时是"为了能在 /docs 里手工建计划"才加的，前端一直没有入口。
 * 这一页给它们一个入口——两级的结构校验仍然全在后端，前端只负责把参数送过去、
 * 把后端的错误显示出来（方案 C 的 detail 直接可读）。
 */

const LOAD_FAILED = "取计划时出了意外错误";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

export default function NewPage() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);

  // 建计划的表单
  const [goal, setGoal] = useState("");

  // 建节点的表单
  const [level, setLevel] = useState<NodeLevel>("checkpoint");
  const [title, setTitle] = useState("");
  const [deliverable, setDeliverable] = useState("");
  const [parentId, setParentId] = useState("");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    getPlan()
      .then((data) => {
        setTree(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  /** 建完之后重新取一次，页面上的树就跟着变了。 */
  async function refresh() {
    try {
      setTree(await getPlan());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  const planId = tree?.plan?.id ?? null;
  const stages = tree?.stages ?? [];

  async function onCreatePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const created = await createPlan(goal);
      setFeedback({ ok: true, text: `已建计划 #${created.id}：${created.goal}` });
      setGoal("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建计划失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onCreateNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (planId === null) return;
    setPending(true);
    setFeedback(null);
    try {
      const created = await createNode({
        planId,
        level,
        title,
        // 只有检查点需要"所属阶段"；阶段带 parentId 会被后端判为 400
        parentId: level === "checkpoint" ? Number(parentId) : null,
        // 只有阶段用交付物
        deliverable: level === "stage" ? deliverable : null,
        dueDate: dueDate === "" ? null : dueDate,
      });
      setFeedback({
        ok: true,
        text: `已建${level === "stage" ? "阶段" : "检查点"} #${created.id}：${created.title}`,
      });
      setTitle("");
      setDeliverable("");
      setDueDate("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建节点失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  return (
    <main>
      <h1>建计划 / 建节点</h1>
      <p>
        <Link href="/">← 回计划表</Link>
      </p>

      {loadError !== null && (
        <p role="alert">
          <strong>取计划失败：</strong>
          {loadError}
        </p>
      )}

      {feedback !== null && (
        <p role={feedback.ok ? "status" : "alert"}>
          <strong>{feedback.ok ? "成功：" : "失败："}</strong>
          {feedback.text}
        </p>
      )}

      <section>
        <h2>建一个计划</h2>
        <form onSubmit={onCreatePlan}>
          <p>
            <label htmlFor="goal">目标（必填）：</label>
            <input
              id="goal"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              required
            />
          </p>
          <button type="submit" disabled={pending}>
            {pending ? "提交中…" : "建计划"}
          </button>
        </form>
      </section>

      <section>
        <h2>在这个计划里建节点</h2>
        {planId === null ? (
          <p role="status">还没有计划，先在上面建一个。</p>
        ) : (
          <>
            {/*
              必须写明目标计划是哪一个：建节点时前端只把 plan_id 送给后端，
              而 GET /api/plan 不传参数时拿的是"最新建的那个有效计划"。
              不写出来的话，你根本不知道节点会落进哪个计划——这是用户实际踩到的坑。
            */}
            <p>
              将建在 <strong>计划 #{planId}「{tree?.plan?.goal}」</strong> 里。
              <br />
              <small>
                界面目前只能操作「最新建的那个有效计划」；同时管多个计划还没做（契约里也没有「列出计划」的接口）。
              </small>
            </p>
            <form onSubmit={onCreateNode}>
            <fieldset>
              <legend>层级</legend>
              <label>
                <input
                  type="radio"
                  name="level"
                  value="stage"
                  checked={level === "stage"}
                  onChange={() => setLevel("stage")}
                />
                阶段（可验证的交付物）
              </label>
              <label>
                <input
                  type="radio"
                  name="level"
                  value="checkpoint"
                  checked={level === "checkpoint"}
                  onChange={() => setLevel("checkpoint")}
                />
                检查点（周检查点）
              </label>
            </fieldset>

            <p>
              <label htmlFor="title">标题（必填）：</label>
              <input
                id="title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
            </p>

            {level === "stage" ? (
              <p>
                <label htmlFor="deliverable">交付物（可选）：</label>
                <input
                  id="deliverable"
                  value={deliverable}
                  onChange={(event) => setDeliverable(event.target.value)}
                />
              </p>
            ) : (
              <p>
                <label htmlFor="parent">所属阶段（必填）：</label>
                <select
                  id="parent"
                  value={parentId}
                  onChange={(event) => setParentId(event.target.value)}
                  required
                >
                  <option value="" disabled>
                    请选择…
                  </option>
                  {stages.map((stage) => (
                    <option key={stage.id} value={stage.id}>
                      {stage.title}
                    </option>
                  ))}
                </select>
              </p>
            )}

            <p>
              {/*
                用 type="date" 而不是文本框：后端只认零填充的 ISO 日期
                （2026-09-30），2026-9-3 会被 422 拒掉；这个控件正好只产出前者。
              */}
              <label htmlFor="due">计划完成日（可选）：</label>
              <input
                id="due"
                type="date"
                value={dueDate}
                onChange={(event) => setDueDate(event.target.value)}
              />
            </p>

            <button type="submit" disabled={pending}>
              {pending ? "提交中…" : "建节点"}
            </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}
