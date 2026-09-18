"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { JudgmentView } from "@/components/judgment-view";
import {
  ApiError,
  askMaterial,
  listJudgments,
  getProfile,
  type AskResult,
  type JudgmentRecord,
  type ProfileView,
} from "@/lib/api";

/**
 * T29：判一份资料的**独立页**。
 *
 * 从 `/proposals` 拆出来的原因是用户走查时说的原话——**「学什么那一类的产物不要出现在
 * 『值不值得学』这个界面」**：那一页既要问资料、又被候选与蓝图的东西占着，两件事混在一起。
 * 现在这一页只装判资料的：四问输入 + 当次答案 + 一条**只读的历史记录**。
 *
 * 裁定环节不在这里：`material_judgment` 提案的批准只记账，留在 `/proposals` 那页；
 * 这里的历史只给人回看「上次那份资料当时怎么判的」，不能改也不能重裁。
 */
export default function JudgePage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<JudgmentRecord[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // 两个取数函数都声明在 effect 之前——否则 lint 会拦「先用后声明」；
  // setState 一律放进 .then 回调（effect 体内同步 setState 也会被拦）
  function refreshHistory() {
    listJudgments()
      .then((data) => setHistory(data.items))
      .catch((cause: unknown) =>
        setHistoryError(cause instanceof ApiError ? cause.message : "取历史时出了意外错误"),
      );
  }

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    refreshHistory();
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setError(null);
    setResult(null);
    try {
      const asked = await askMaterial(rawText);
      setResult(asked);
      refreshHistory(); // 刚判的这一份也算历史了（它在 /proposals 被裁定后会显示状态）
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "问模型失败，原因不明");
    } finally {
      setAsking(false);
    }
  }

  return (
    <main>
      <h1>判一份资料：值不值得学</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/candidates">候选清单（学什么方向）</Link>
        {" · "}
        <Link href="/blueprints">蓝图待批</Link>
        {" · "}
        <Link href="/proposals">待裁定提案</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          这一页只装判资料的：你发一份资料、模型按四问回答，下面留一条只读的历史。
          想找<strong>学什么方向</strong>去<Link href="/candidates">候选清单</Link>；
          要让模型出一棵树去<Link href="/blueprints">蓝图待批</Link>。
        </small>
      </p>

      {profile !== null && (
        <p>
          <small>
            判据来自你的长期档案：现有 <strong>{profile.items.length}</strong> 条
            {profile.items.length === 0 && "——先补档案，否则只会得到「依据不足」"}
          </small>
        </p>
      )}

      <form onSubmit={onSubmit}>
        <p>
          <label htmlFor="raw">我发现了什么（必填）：</label>
          <br />
          <textarea
            id="raw"
            rows={3}
            cols={60}
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            placeholder="例如：我看到一个 Rust 异步编程教程，要不要学？"
            required
          />
        </p>
        <button type="submit" disabled={asking}>
          {asking ? "正在问模型…（四问几秒到几十秒）" : "问一下"}
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
          <h2>这一份的四问结果</h2>
          <JudgmentView judgment={result.judgment} />
          <p>
            <small>
              已存成<strong>待裁定</strong>提案 #{result.proposal_id}（依据了{" "}
              {result.profile_basis.total} 条档案，调了 {result.calls} 次模型），
              去<Link href="/proposals">待裁定提案</Link>页批准或驳回——批准只记账。
            </small>
          </p>
        </section>
      )}

      <section>
        <h2>判过的资料（只读）</h2>
        <p>
          <small>
            这里只回看，不能改也不能重裁。裁定过的与还在待裁定的都列出来，
            状态写着「待裁定」的那些在<Link href="/proposals">待裁定提案</Link>页处理。
          </small>
        </p>
        {historyError !== null && (
          <p role="alert">
            <strong>取历史失败：</strong>
            {historyError}
          </p>
        )}
        {history !== null && history.length === 0 && (
          <p role="status">还没有判过资料。</p>
        )}
        <ol>
          {(history ?? []).map((item) => (
            <li key={item.id}>
              <p>
                <strong>{item.source_text}</strong>{" "}
                <small>
                  （提案 #{item.id}·
                  {item.status === "pending"
                    ? "待裁定"
                    : item.status === "accepted"
                      ? "已批准（只记账）"
                      : "已驳回"}
                  {item.decided_at !== null && ` · ${item.decided_at.slice(0, 16).replace("T", " ")}`}
                  ）
                </small>
              </p>
              {item.judgment !== undefined && <JudgmentView judgment={item.judgment} />}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}