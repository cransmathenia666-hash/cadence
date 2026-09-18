"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { JudgmentView } from "@/components/judgment-view";
import {
  ApiError,
  decideProposal,
  listProposals,
  type MaterialJudgmentPayload,
  type ProfileChangePayload,
  type Proposal,
} from "@/lib/api";

/**
 * T29：待裁定提案页——**只装待裁定的**。
 *
 * 用户走查时的原话是「学什么那一类的产物不要出现在『值不值得学』这个界面」：原先这一页
 * 既管判资料、又管候选、又管蓝图，三件事挤在一页。现在各归各位——判资料去 `/judge`
 * （含只读历史）、蓝图去 `/blueprints`、找方向去 `/candidates`。
 *
 * 剩下两类的「批准」含义完全不同，页面上要写清：
 * - `material_judgment` 资料判断：批准**只记账**（判断本身已是结论，不改计划或档案）；
 * - `profile_change` 档案变更（计划对话里聊出来的）：批准**真的把这条写进长期档案**。
 *
 * 规则自动产的两类（`stage_advance` / `plan_replan`）已在 T29 整类删除；
 * 阶段推进与落后的处理回到计划页——落后显示成一句提醒，收尾有按钮。
 */

const CATEGORY_LABELS: Record<string, string> = {
  life_habit: "生活习惯（睡眠/运动/作息）",
  life_log: "生活记录（日程/课程/近况）",
  current_state: "当前状态（精力/时间/压力）",
  short_term_goal: "短期目标 + 当下痛点",
  long_axis: "长期主线（职业方向）",
};

const KIND_TITLES: Record<string, string> = {
  material_judgment: "资料判断",
  profile_change: "档案变更",
};

export default function ProposalsPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）；
  // refresh 声明在 effect 之前——否则 lint 会拦「先用后声明」
  function refresh() {
    listProposals()
      .then(setProposals)
      .catch((cause: unknown) =>
        setError(cause instanceof ApiError ? cause.message : "取提案时出了意外错误"),
      );
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onDecide(proposal: Proposal, approved: boolean, reason?: string) {
    setBusy(true);
    setError(null);
    try {
      const done = await decideProposal(proposal.id, { approved, reason });
      setNotes((previous) => [
        approved
          ? done.effect === "profile_written"
            ? `已批准：把「${done.written?.content ?? ""}」写进了长期档案（${done.written?.category ?? ""}）` +
              "——去「长期档案」页能看到它；旧条目一条没动（这是新增，不是取代）。"
            : "已批准，只记账：这项不会改计划或档案。"
          : "已驳回，理由进了台账。",
        ...previous,
      ]);
      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposal.id));
      setRejectingId(null);
      refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "裁定失败，原因不明");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>待裁定提案（{proposals === null ? "…" : proposals.length} 条）</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/candidates">候选清单与规划对话</Link>
        {" · "}
        <Link href="/judge">判一份资料</Link>
        {" · "}
        <Link href="/blueprints">蓝图待批</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          这一页只装<strong>待裁定</strong>的两类：<strong>资料判断</strong>（批准只记账）与
          <strong>档案变更</strong>（批准真的写进长期档案）。判资料去
          <Link href="/judge">判一份资料</Link>页、蓝图去
          <Link href="/blueprints">蓝图待批</Link>页。
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
      {proposals !== null && proposals.length === 0 && <p role="status">没有待裁定的提案。</p>}

      <ol>
        {(proposals ?? []).map((proposal) => (
          <li key={proposal.id}>
            <p>
              <strong>{KIND_TITLES[proposal.kind] ?? proposal.kind}</strong>
              <small>
                （提案 #{proposal.id}，提出于 {proposal.created_at.slice(0, 16).replace("T", " ")}）
              </small>
            </p>
            {proposal.reason !== null && <p>{proposal.reason}</p>}

            <ProposalBody proposal={proposal} />

            <p>
              <button type="button" onClick={() => onDecide(proposal, true)} disabled={busy}>
                批准
              </button>{" "}
              {rejectingId === proposal.id ? (
                <>
                  <button
                    type="button"
                    onClick={() => onDecide(proposal, false, reasons[proposal.id])}
                    disabled={busy || (reasons[proposal.id] ?? "").trim() === ""}
                  >
                    确认驳回
                  </button>{" "}
                  <button type="button" onClick={() => setRejectingId(null)} disabled={busy}>
                    取消
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => setRejectingId(proposal.id)} disabled={busy}>
                  驳回
                </button>
              )}
            </p>
            {rejectingId === proposal.id && (
              <p>
                <label htmlFor={`reason-${proposal.id}`}>驳回理由（必填，进台账）：</label>
                <input
                  id={`reason-${proposal.id}`}
                  value={reasons[proposal.id] ?? ""}
                  onChange={(event) =>
                    setReasons((previous) => ({ ...previous, [proposal.id]: event.target.value }))
                  }
                  placeholder="例如：这份判断依据太单薄 / 这只是暂时的"
                />
              </p>
            )}
          </li>
        ))}
      </ol>
    </main>
  );
}

/** 按 `kind` 分流渲染 payload——形状是后端定的，这里只按期转一次类型。 */
function ProposalBody({ proposal }: { proposal: Proposal }) {
  if (proposal.kind === "material_judgment") {
    const payload = proposal.payload as unknown as MaterialJudgmentPayload;
    return (
      <>
        <p>
          <small>资料原文：{payload.source_text}</small>
        </p>
        {payload.judgment !== undefined && <JudgmentView judgment={payload.judgment} />}
        <p>
          <small>
            批准只记账：这份判断本身已是结论，不会改计划或档案。想落成新方向，去
            <Link href="/candidates">候选清单</Link>页采纳一条候选。
          </small>
        </p>
      </>
    );
  }

  const payload = proposal.payload as unknown as ProfileChangePayload;
  return (
    <>
      <p>
        类别 <strong>{payload.category}</strong>
        {CATEGORY_LABELS[payload.category] !== undefined &&
          `（${CATEGORY_LABELS[payload.category]}）`}
        <br />
        要写进档案的内容：{payload.content}
      </p>
      {payload.why !== undefined && (
        <p>
          <small>为什么该这么记：{payload.why}</small>
        </p>
      )}
      <p>
        <small>
          这条来自<strong>计划对话</strong>
          {payload.plan_id !== undefined && `（计划 #${payload.plan_id}）`}里聊出的变化。
          <strong>批准 = 真的写进长期档案</strong>（新增一条；同类别一字不差的重复会被拒）；
          驳回只留痕，档案一个字不动。
        </small>
      </p>
    </>
  );
}