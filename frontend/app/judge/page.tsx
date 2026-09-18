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

export default function JudgePage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<JudgmentRecord[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

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
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "问模型失败，原因不明");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>判一份资料：值不值得学</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            输入你偶然看到的课程、书籍或开源项目，决策引擎结合你的长期档案进行严谨的四问评估。
          </p>
        </div>
        {profile !== null && (
          <span className="badge badge-not_started">
            当前长期档案 {profile.items.length} 条
          </span>
        )}
      </div>

      <div className="card">
        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <div>
            <label htmlFor="raw" style={{ fontWeight: 600 }}>
              输入待评估资料或主题：
            </label>
            <textarea
              id="raw"
              rows={3}
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="例如：我看到一个 Rust 异步并发与网络协议实战课，要不要学？"
              disabled={asking}
              style={{ width: "100%" }}
              required
            />
          </div>
          <div className="flex-between">
            <small style={{ color: "var(--text-muted)" }}>
              四问将输出：①值不值得学 ②目标深度 ③板块取舍 ④时间预算
            </small>
            <button type="submit" className="primary" disabled={asking || rawText.trim() === ""}>
              {asking ? (
                <>
                  <span className="spinner" />
                  <span>正在严格按四问评判…</span>
                </>
              ) : (
                "开始评判"
              )}
            </button>
          </div>
        </form>

        {error !== null && (
          <div className="alert alert-danger" style={{ marginTop: "14px" }}>
            <strong>评判失败：</strong>
            {error}
          </div>
        )}
      </div>

      {result !== null && (
        <div className="card" style={{ borderColor: "var(--primary)" }}>
          <div className="card-header">
            <div className="flex-row gap-sm">
              <span className="badge badge-done">四问评判结果</span>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                （依据 {result.profile_basis.total} 条档案 · 耗时/调用 {result.calls} 次）
              </span>
            </div>
            <Link href="/proposals" className="btn sm primary">
              前往待裁定页记账 (#{result.proposal_id}) →
            </Link>
          </div>

          <JudgmentView judgment={result.judgment} />

          <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "10px 0 0" }}>
            此评判已生成待裁定提案。四问评判的批准仅作决策留痕记账，不会自动变更你的执行计划。
          </p>
        </div>
      )}

      <div style={{ marginTop: "24px" }}>
        <div className="flex-between" style={{ marginBottom: "12px" }}>
          <h2>已裁定的资料评估历史（只读回看）</h2>
          <small>仅展示已裁定生效的判断记录</small>
        </div>

        {historyError !== null && (
          <div className="alert alert-danger">{historyError}</div>
        )}

        {history === null && (
          <div className="card" style={{ textAlign: "center", padding: "30px" }}>
            <span className="spinner" style={{ width: "18px", height: "18px" }} />
            <p style={{ color: "var(--text-muted)", marginTop: "8px" }}>读取历史评估中…</p>
          </div>
        )}

        {history !== null && history.length === 0 && (
          <div className="card" style={{ textAlign: "center", padding: "30px" }}>
            <p style={{ color: "var(--text-muted)", margin: 0 }}>暂无历史判断记录。</p>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {(history ?? []).map((item) => (
            <div key={item.id} className="card" style={{ margin: 0, padding: "14px 18px" }}>
              <div className="flex-between" style={{ marginBottom: "8px" }}>
                <span style={{ fontWeight: 600 }}>{item.source_text}</span>
                <div className="flex-row gap-sm">
                  <span className={`badge ${item.status === "accepted" ? "badge-done" : "badge-stuck"}`}>
                    {item.status === "accepted" ? "裁定通过" : "裁定否决"}
                  </span>
                  <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                    {item.decided_at?.slice(0, 10)}
                  </span>
                </div>
              </div>
              {item.judgment && <JudgmentView judgment={item.judgment} />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
