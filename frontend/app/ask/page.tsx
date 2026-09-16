"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  askMaterial,
  getProfile,
  type AskResult,
  type Judgment,
  type ProfileView,
} from "@/lib/api";

/**
 * 四问判断的临时入口（T14 之前先用着）。
 *
 * 后端接口是 T12 的 `POST /api/requests`：你贴一句「我发现了一个资料，要不要学」，
 * 它回四问答案 + 一条**待裁定**提案的 id。这一页只做「发出去、显示出来」，
 * 不做样式、不做裁定——裁定属 T14。
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

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

export default function AskPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rawText, setRawText] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askMaterial(rawText));
    } catch (cause) {
      setError(messageOf(cause, "提交失败，原因不明"));
    } finally {
      setPending(false);
    }
  }

  return (
    <main>
      <h1>问一句：这个资料要不要学</h1>
      <p>
        <Link href="/">← 回计划表</Link>
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
        <button type="submit" disabled={pending}>
          {pending ? "正在问模型…（要几秒到几十秒）" : "问一下"}
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
    </main>
  );
}
