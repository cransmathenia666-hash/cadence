"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  findCandidates,
  getProfile,
  listCandidates,
  verdictCandidate,
  type CandidateList,
  type FindResult,
  type ProfileView,
} from "@/lib/api";

/**
 * T14：候选清单的正式页面（「我不知道该学什么」的入口）。
 *
 * 与 `/ask` 时代那个临时入口的区别：
 * - **进来就有东西看**：挂载时用 `GET /api/candidates` 取最近一轮的候选（连你已经
 *   裁定过的也显示状态），不用重新问一次模型；重新问一次是「再要一轮」。
 * - 采纳 / 否决都在这里做完：采纳会在最新 active 计划里自动建一个同名阶段
 *   （后端的事），否决必须写理由——它成为下次的禁区。
 *
 * 取数、状态、错误在这里管；判定全在后端（SPEC 第 10 节：前端不做业务计算）。
 */

const KIND_LABELS: Record<string, string> = {
  concept: "概念",
  doc: "资料",
  project: "项目",
  course: "课程",
};

const STATUS_LABELS: Record<string, string> = {
  proposed: "待裁定",
  accepted: "已采纳",
  rejected: "已否决",
};

/** 一份清单里的一条候选：新问的和重看库里那轮的，都统一成这个形状。 */
type Row = {
  id: number;
  title: string;
  kind: string;
  why: string;
  depthTarget: string;
  isRecommended: boolean;
  status: string;
  rejectReason: string | null;
  /** 依据的档案 id。库里的候选没存这列，重看时是 null。 */
  basis: number[] | null;
};

function rowsFromFind(found: FindResult): Row[] {
  return found.candidates.map((item, index) => ({
    id: found.candidate_ids[index],
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.title === found.recommended_start,
    status: "proposed",
    rejectReason: null,
    basis: item.profile_item_ids,
  }));
}

function rowsFromStored(stored: CandidateList): Row[] {
  return stored.candidates.map((item) => ({
    id: item.id,
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.is_recommended === 1,
    status: item.status,
    rejectReason: item.reject_reason,
    basis: null,
  }));
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

export default function CandidatesPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  /** 这一屏显示的是哪一轮：新问的那次带来源与禁区，从库里取的只知道原文。 */
  const [fresh, setFresh] = useState<FindResult | null>(null);
  const [storedText, setStoredText] = useState<string | null>(null);

  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  /** 每条裁定后的回执文案，按候选 id 存。 */
  const [notes, setNotes] = useState<Record<number, string>>({});
  /** 正在填否决理由的那条；null = 没人在填。 */
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    listCandidates()
      .then((stored) => {
        setRows(rowsFromStored(stored));
        setStoredText(stored.raw_text);
      })
      .catch((cause: unknown) => setAskError(messageOf(cause, "取候选清单时出了意外错误")));
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAskError(null);
    setNotes({});
    setVerdictError(null);
    setRejectingId(null);
    try {
      const found = await findCandidates(rawText);
      setFresh(found);
      setStoredText(null);
      setRows(rowsFromFind(found));
    } catch (cause) {
      setAskError(messageOf(cause, "问模型失败，原因不明"));
    } finally {
      setAsking(false);
    }
  }

  async function onVerdict(candidateId: number, accept: boolean, reason?: string) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accept, reason);
      setRows((previous) =>
        (previous ?? []).map((row) =>
          row.id === candidateId
            ? { ...row, status: done.status, rejectReason: reason ?? null }
            : row,
        ),
      );
      setNotes((previous) => ({
        ...previous,
        [candidateId]:
          done.status === "accepted"
            ? `已采纳，并在计划 #${done.plan_id} 里自动建了阶段 #${done.node_id}——回计划表就能看到。`
            : "已否决。理由进了台账，下次「找」不会再出现这条。",
      }));
      setRejectingId(null);
      setRejectReason("");
    } catch (cause) {
      setVerdictError(messageOf(cause, "表态失败，原因不明"));
    } finally {
      setVerdicting(false);
    }
  }

  const pendingCount = (rows ?? []).filter((row) => row.status === "proposed").length;

  return (
    <main>
      <h1>候选清单：我不知道该学什么</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/proposals">待裁定提案</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          这里问的是<strong>「学什么方向」</strong>；要判断某一份具体资料值不值得学，
          去<Link href="/proposals">待裁定提案</Link>页问。
        </small>
      </p>

      {profile !== null && (
        <p>
          <small>
            判据来自你的长期档案：现有 <strong>{profile.items.length}</strong> 条
            {profile.items.length === 0 && "——先补档案，否则问不出东西"}
          </small>
        </p>
      )}

      <form onSubmit={onSubmit}>
        <p>
          <label htmlFor="raw">我的处境 / 想法（必填）：</label>
          <br />
          <textarea
            id="raw"
            rows={3}
            cols={60}
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            placeholder="例如：我不知道该学什么，方向是后端 + 能上线的项目"
            required
          />
        </p>
        <button type="submit" disabled={asking || verdicting}>
          {asking ? "正在问模型…（候选清单可能要一两分钟）" : "再要一轮候选清单"}
        </button>
      </form>

      {askError !== null && (
        <p role="alert">
          <strong>失败：</strong>
          {askError}
        </p>
      )}

      {verdictError !== null && (
        <p role="alert">
          <strong>表态失败：</strong>
          {verdictError}
        </p>
      )}

      {rows !== null && rows.length === 0 && (
        <p role="status">还没有候选：上面填一句处境，让模型给一份清单。</p>
      )}

      {rows !== null && rows.length > 0 && (
        <section>
          <h2>
            候选（{rows.length} 条，待裁定 {pendingCount} 条
            {fresh === null ? "，最近一轮" : "，刚问出来的"}）
          </h2>
          <p>
            <small>
              {fresh !== null ? (
                <>
                  来源：{fresh.source.name}
                  {fresh.source.networked ? "（联网）" : "（不联网，只给路线建议，链接要自己找）"}
                  ；依据 {fresh.profile_basis.total} 条档案，调了 {fresh.calls} 次模型。
                  {fresh.banned_titles.length > 0 && (
                    <>
                      <br />
                      本次禁区（你否决过的，模型不许再推）：{fresh.banned_titles.join("、")}
                    </>
                  )}
                </>
              ) : (
                <>
                  {storedText === null ? "库里的候选" : `上一轮问的是「${storedText}」`}
                  ；依据的档案 id 只在问的那当刻的响应里（候选表没存这一列），
                  重看时看不到，要看依据就用上面「再要一轮」重新问。
                </>
              )}
            </small>
          </p>

          <ol>
            {rows.map((row) => {
              const isDecided = row.status !== "proposed";
              return (
                <li key={row.id}>
                  <p>
                    <strong>{row.title}</strong>
                    <small>
                      （{KIND_LABELS[row.kind] ?? row.kind}·建议深度 {row.depthTarget}
                      {row.isRecommended && "·建议从这里开始"}）
                    </small>
                    <br />
                    {row.why}
                    <br />
                    <small>
                      {row.basis !== null && (
                        <>
                          依据：
                          {row.basis.length === 0
                            ? "（无）"
                            : row.basis.map((id) => `#${id}`).join("、")}
                          {" · "}
                        </>
                      )}
                      候选 #{row.id} · {STATUS_LABELS[row.status] ?? row.status}
                      {row.rejectReason !== null && `（理由：${row.rejectReason}）`}
                    </small>
                  </p>

                  {isDecided ? (
                    <p role="status">
                      <small>{notes[row.id] ?? "这条已经裁定过了。"}</small>
                    </p>
                  ) : (
                    <p>
                      <button
                        type="button"
                        onClick={() => onVerdict(row.id, true)}
                        disabled={verdicting}
                      >
                        采纳（自动在计划里建阶段）
                      </button>{" "}
                      {rejectingId === row.id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => onVerdict(row.id, false, rejectReason)}
                            disabled={verdicting || rejectReason.trim() === ""}
                          >
                            确认否决
                          </button>{" "}
                          <button
                            type="button"
                            onClick={() => setRejectingId(null)}
                            disabled={verdicting}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setRejectingId(row.id);
                            setRejectReason("");
                          }}
                          disabled={verdicting}
                        >
                          否决
                        </button>
                      )}
                    </p>
                  )}

                  {rejectingId === row.id && (
                    <p>
                      <label htmlFor={`reason-${row.id}`}>否决理由（必填，进台账）：</label>
                      <input
                        id={`reason-${row.id}`}
                        value={rejectReason}
                        onChange={(event) => setRejectReason(event.target.value)}
                        placeholder="例如：和主线无关 / 现在不需要"
                      />
                    </p>
                  )}
                </li>
              );
            })}
          </ol>

          {fresh !== null && (
            <p>
              <small>
                <strong>建议先从「{fresh.recommended_start}」开始：</strong>
                {fresh.start_reason}
              </small>
            </p>
          )}
        </section>
      )}
    </main>
  );
}
