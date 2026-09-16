"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  askMaterial,
  findCandidates,
  getProfile,
  verdictCandidate,
  type AskResult,
  type FindResult,
  type Judgment,
  type ProfileView,
} from "@/lib/api";

/**
 * 决策入口的临时页（T14 之前的入口，两种形态都由它转）。
 *
 * - 「判断资料」走 T12 的 `POST /api/requests`（kind=evaluate）：回四问答案 + 一条待裁定提案。
 * - 「找该学什么」走 T13 的同一个接口（kind=search）：回 3–5 条带排序的候选 + 建议起点，
 *   每条可以就地采纳 / 否决；否决要写理由，理由会成为下次的禁区。
 *
 * 这一页只做「发出去、显示出来、表个态」，不做样式——正式页面属 T14。
 *
 * 为什么答案里要显示档案 id：后端要求每条答案指回长期档案的具体字段，
 * 所以这里把 id 显示出来，你一眼能看出它是**真有依据**还是在含糊其辞。
 */

const QUESTION_LABELS: Record<keyof Judgment, string> = {
  worth_learning: "① 值不值得学",
  depth_target: "② 学到什么程度",
  intensity: "③ 板块分级",
  time_budget: "④ 时间预算",
};

const CATEGORY_LABELS: Record<string, string> = {
  life_habit: "生活习惯",
  life_log: "生活记录",
  current_state: "当前状态",
  short_term_goal: "短期目标+痛点",
  long_axis: "长期主线",
};

const KIND_LABELS: Record<string, string> = {
  concept: "概念",
  doc: "资料",
  project: "项目",
  course: "课程",
};

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 两种形态共用一份「输入 + 状态」，免得页面里到处是 if。 */
type Mode = "evaluate" | "search";

export default function AskPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [mode, setMode] = useState<Mode>("evaluate");
  const [rawText, setRawText] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [found, setFound] = useState<FindResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** 候选的裁决结果，按候选 id 存：裁完就地显示，不动整份清单。 */
  const [verdicts, setVerdicts] = useState<Record<number, string>>({});
  /** 正在填否决理由的那条；null = 没人在填。 */
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [verdictError, setVerdictError] = useState<string | null>(null);

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
  }, []);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setResult(null);
    setFound(null);
    setVerdicts({});
    setRejectingId(null);
    setVerdictError(null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    setFound(null);
    setVerdicts({});
    setRejectingId(null);
    setVerdictError(null);
    try {
      if (mode === "search") {
        setFound(await findCandidates(rawText));
      } else {
        setResult(await askMaterial(rawText));
      }
    } catch (cause) {
      setError(messageOf(cause, "提交失败，原因不明"));
    } finally {
      setPending(false);
    }
  }

  async function onVerdict(candidateId: number, accept: boolean, reason?: string) {
    setPending(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accept, reason);
      setVerdicts((previous) => ({ ...previous, [candidateId]: done.status }));
      setRejectingId(null);
      setRejectReason("");
    } catch (cause) {
      setVerdictError(messageOf(cause, "表态失败，原因不明"));
    } finally {
      setPending(false);
    }
  }

  return (
    <main>
      <h1>{mode === "search" ? "找一句：我不知道该学什么" : "问一句：这个资料要不要学"}</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <button
          type="button"
          onClick={() => switchMode("evaluate")}
          disabled={pending || mode === "evaluate"}
        >
          判断一个资料
        </button>{" "}
        <button
          type="button"
          onClick={() => switchMode("search")}
          disabled={pending || mode === "search"}
        >
          我不知道该学什么
        </button>
      </p>

      {profile !== null && (
        <p>
          <small>
            判据来自你的长期档案：现有 <strong>{profile.items.length}</strong> 条
            {profile.missing_categories.length > 0 && (
              <>
                ；空着的类别：
                {profile.missing_categories
                  .map((name) => CATEGORY_LABELS[name] ?? name)
                  .join("、")}
                （涉及它们的问答只会得到「依据不足」）
              </>
            )}
            {profile.items.length === 0 && "——先补档案，否则两种问法都问不出东西"}
          </small>
        </p>
      )}

      <form onSubmit={onSubmit}>
        <p>
          <label htmlFor="raw">
            {mode === "search" ? "我的处境/想法（必填）：" : "我发现了什么（必填）："}
          </label>
          <br />
          <textarea
            id="raw"
            rows={3}
            cols={60}
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            placeholder={
              mode === "search"
                ? "例如：我不知道该学什么，方向是后端 + 能上线的项目"
                : "例如：我看到一个 Rust 异步编程教程，要不要学？"
            }
            required
          />
        </p>
        <button type="submit" disabled={pending}>
          {pending
            ? "正在问模型…（四问几秒到几十秒；候选清单更长，可能一两分钟）"
            : mode === "search"
              ? "给我一份候选清单"
              : "问一下"}
        </button>
      </form>

      {error !== null && (
        <p role="alert">
          <strong>失败：</strong>
          {error}
        </p>
      )}

      {result !== null && (
        <section>
          <h2>四问结果</h2>
          {(Object.keys(QUESTION_LABELS) as (keyof Judgment)[]).map((key) => {
            const item = result.judgment[key];
            return (
              <p key={key}>
                <strong>{QUESTION_LABELS[key]}：</strong>
                {item.answer}
                <br />
                <small>
                  依据：
                  {item.profile_item_ids.length === 0
                    ? "（无）"
                    : item.profile_item_ids.map((id) => `#${id}`).join("、")}
                </small>
              </p>
            );
          })}
          <p>
            <small>
              已存成<strong>待裁定</strong>提案 #{result.proposal_id}
              （这次依据了 {result.profile_basis.total} 条档案，调了 {result.calls} 次模型）。
              档案和计划都还没改——裁定入口在 T14。
            </small>
          </p>
        </section>
      )}

      {found !== null && (
        <section>
          <h2>候选清单（{found.candidates.length} 条，按优先级）</h2>
          <p>
            <small>
              来源：{found.source.name}
              {found.source.networked ? "（联网）" : "（不联网，只给路线建议，链接要自己找）"}
              ；这次依据了 {found.profile_basis.total} 条档案，调了 {found.calls} 次模型。
              {found.banned_titles.length > 0 && (
                <>
                  <br />
                  本次禁区（你否决过的，模型不许再推）：
                  {found.banned_titles.join("、")}
                </>
              )}
            </small>
          </p>

          {verdictError !== null && (
            <p role="alert">
              <strong>表态失败：</strong>
              {verdictError}
            </p>
          )}

          <ol>
            {found.candidates.map((item, index) => {
              const candidateId = found.candidate_ids[index];
              const status = verdicts[candidateId];
              const isStart = item.title === found.recommended_start;
              return (
                <li key={candidateId}>
                  <p>
                    <strong>{item.title}</strong>
                    <small>
                      （{KIND_LABELS[item.kind] ?? item.kind}·建议深度 {item.depth_target}
                      {isStart && "·建议从这里开始"}）
                    </small>
                    <br />
                    {item.why}
                    <br />
                    <small>
                      依据：
                      {item.profile_item_ids.length === 0
                        ? "（无）"
                        : item.profile_item_ids.map((id) => `#${id}`).join("、")}
                      {" · "}
                      候选 #{candidateId}
                    </small>
                  </p>

                  {status === undefined ? (
                    <p>
                      <button
                        type="button"
                        onClick={() => onVerdict(candidateId, true)}
                        disabled={pending}
                      >
                        采纳
                      </button>{" "}
                      {rejectingId === candidateId ? (
                        <>
                          <button
                            type="button"
                            onClick={() => onVerdict(candidateId, false, rejectReason)}
                            disabled={pending || rejectReason.trim() === ""}
                          >
                            确认否决
                          </button>{" "}
                          <button
                            type="button"
                            onClick={() => setRejectingId(null)}
                            disabled={pending}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setRejectingId(candidateId);
                            setRejectReason("");
                          }}
                          disabled={pending}
                        >
                          否决
                        </button>
                      )}
                    </p>
                  ) : (
                    <p role="status">
                      <small>
                        已{status === "accepted" ? "采纳" : "否决"}（否决的理由已进台账，
                        下次「找」不会再出现这条）。
                      </small>
                    </p>
                  )}

                  {rejectingId === candidateId && (
                    <p>
                      <label htmlFor={`reason-${candidateId}`}>否决理由（必填，进台账）：</label>
                      <input
                        id={`reason-${candidateId}`}
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

          <p>
            <small>
              <strong>建议先从「{found.recommended_start}」开始：</strong>
              {found.start_reason}
              <br />
              候选已存成 <strong>proposed</strong>（待裁定），档案和计划都没改。
            </small>
          </p>
        </section>
      )}
    </main>
  );
}
