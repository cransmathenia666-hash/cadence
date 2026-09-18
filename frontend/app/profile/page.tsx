"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createProfileItem,
  getProfile,
  PROFILE_CATEGORIES,
  updateProfileItem,
  voidProfileItem,
  type ProfileCategory,
  type ProfileView,
} from "@/lib/api";

const LOAD_FAILED = "取档案时出了意外错误";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

const CATEGORY_KEYS = Object.keys(PROFILE_CATEGORIES) as ProfileCategory[];

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);

  const [newCategory, setNewCategory] = useState<ProfileCategory>("long_axis");
  const [newContent, setNewContent] = useState("");

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editReason, setEditReason] = useState("");

  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");

  useEffect(() => {
    getProfile()
      .then((data) => {
        setProfile(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  async function refresh() {
    try {
      setProfile(await getProfile());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const created = await createProfileItem({ category: newCategory, content: newContent });
      setFeedback({ ok: true, text: `已补入档案 #${created.id}（${PROFILE_CATEGORIES[newCategory]}）` });
      setNewContent("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "补入档案失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onUpdate(id: number) {
    setPending(true);
    setFeedback(null);
    try {
      const updated = await updateProfileItem({ itemId: id, content: editContent, reason: editReason });
      setFeedback({
        ok: true,
        text: `已更新档案 #${id} → 新条目 #${updated.id}（旧条目已置为 superseded 留痕）`,
      });
      setEditingId(null);
      setEditContent("");
      setEditReason("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "更新档案失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onVoid(id: number) {
    setPending(true);
    setFeedback(null);
    try {
      await voidProfileItem(id, voidReason);
      setFeedback({ ok: true, text: `已作废档案 #${id}（状态置为 void，理由已记入台账）` });
      setVoidingId(null);
      setVoidReason("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "作废档案失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  const items = profile?.items ?? [];

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>长期个人档案底座</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            这是决策引擎判断「值不值得学」与推荐方向的唯一直相依据。提炼后的结论，不存原始冗长笔记。
          </p>
        </div>
        {profile && (
          <span className="badge badge-in_progress">
            有效档案 {items.length} 条 · 缺口 {profile.missing_categories.length} 类
          </span>
        )}
      </div>

      {loadError !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>加载失败：</strong>
          {loadError}
        </div>
      )}

      {feedback !== null && (
        <div className={`alert ${feedback.ok ? "alert-success" : "alert-danger"}`} role="status">
          {feedback.text}
        </div>
      )}

      {profile !== null && profile.missing_categories.length > 0 && (
        <div className="alert alert-warning" style={{ fontSize: "13px" }}>
          <strong>当前档案缺口分类：</strong>
          {profile.missing_categories
            .map((key) => PROFILE_CATEGORIES[key as ProfileCategory] ?? key)
            .join("、")}
          。缺失此类别的四问回答将被判定为「依据不足」，建议尽快补充。
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "20px" }}>
        {/* 左侧：录入新档案 */}
        <div className="card" style={{ height: "fit-content" }}>
          <h2>添加新档案条目</h2>
          <form onSubmit={onCreate} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div>
              <label htmlFor="new-category">档案类别：</label>
              <select
                id="new-category"
                value={newCategory}
                onChange={(event) => setNewCategory(event.target.value as ProfileCategory)}
                style={{ width: "100%" }}
              >
                {CATEGORY_KEYS.map((key) => (
                  <option key={key} value={key}>
                    {PROFILE_CATEGORIES[key]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="new-content">提炼结论（一两句话）：</label>
              <textarea
                id="new-content"
                value={newContent}
                onChange={(event) => setNewContent(event.target.value)}
                rows={3}
                placeholder="例如：每天能稳定投入 1.5 小时，精力主要集中在清晨与周末"
                required
                style={{ width: "100%" }}
              />
            </div>
            <div className="flex-between">
              <small style={{ color: "var(--text-muted)" }}>录入即刻生效</small>
              <button type="submit" className="primary" disabled={pending || newContent.trim() === ""}>
                {pending ? "正在保存…" : "保存进档案"}
              </button>
            </div>
          </form>
        </div>

        {/* 右侧：五类分类视图 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          {CATEGORY_KEYS.map((key) => {
            const categoryItems = items.filter((item) => item.category === key);
            return (
              <div key={key} className="card" style={{ margin: 0, padding: "16px 18px" }}>
                <div className="flex-between" style={{ marginBottom: "10px" }}>
                  <div className="flex-row gap-sm">
                    <span className="badge badge-in_progress">{key}</span>
                    <h3 style={{ margin: 0 }}>{PROFILE_CATEGORIES[key]}</h3>
                  </div>
                  <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                    {categoryItems.length} 条有效记录
                  </span>
                </div>

                {categoryItems.length === 0 ? (
                  <p style={{ color: "var(--text-muted)", fontSize: "12px", margin: 0 }}>
                    该类别暂无档案条目，四问涉及此项将依据不足。
                  </p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {categoryItems.map((item) => (
                      <div
                        key={item.id}
                        style={{
                          background: "var(--bg-subtle)",
                          border: "1px solid var(--border)",
                          borderRadius: "var(--radius-sm)",
                          padding: "10px 12px",
                        }}
                      >
                        <div className="flex-between" style={{ alignItems: "flex-start" }}>
                          <div style={{ fontSize: "13px", lineHeight: 1.5, color: "var(--text-main)", flex: 1 }}>
                            {item.content}
                          </div>
                          <div className="flex-row gap-sm" style={{ marginLeft: "12px" }}>
                            <button
                              type="button"
                              className="sm ghost"
                              onClick={() => {
                                setEditingId(item.id);
                                setEditContent(item.content);
                                setEditReason("");
                                setVoidingId(null);
                              }}
                              disabled={pending}
                            >
                              修改
                            </button>
                            <button
                              type="button"
                              className="sm ghost danger"
                              onClick={() => {
                                setVoidingId(item.id);
                                setVoidReason("");
                                setEditingId(null);
                              }}
                              disabled={pending}
                            >
                              作废
                            </button>
                          </div>
                        </div>

                        <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
                          条目 #{item.id} · 生效于 {item.valid_from?.slice(0, 10)}
                        </div>

                        {editingId === item.id && (
                          <div className="inline-edit-box">
                            <label style={{ fontSize: "11px" }}>修改后的内容：</label>
                            <textarea
                              value={editContent}
                              onChange={(event) => setEditContent(event.target.value)}
                              rows={2}
                              style={{ width: "100%" }}
                            />
                            <label style={{ fontSize: "11px" }}>修改理由（必填，记录不可逆变更）：</label>
                            <input
                              value={editReason}
                              onChange={(event) => setEditReason(event.target.value)}
                              placeholder="例如：作息变动，重新核算时间"
                              style={{ width: "100%" }}
                            />
                            <div className="flex-row gap-sm" style={{ marginTop: "4px" }}>
                              <button
                                type="button"
                                className="primary sm"
                                onClick={() => onUpdate(item.id)}
                                disabled={pending || editReason.trim() === "" || editContent.trim() === ""}
                              >
                                保存替换
                              </button>
                              <button type="button" className="sm" onClick={() => setEditingId(null)}>
                                取消
                              </button>
                            </div>
                          </div>
                        )}

                        {voidingId === item.id && (
                          <div className="inline-edit-box">
                            <label style={{ fontSize: "11px" }}>作废理由（必填，单向门操作）：</label>
                            <input
                              value={voidReason}
                              onChange={(event) => setVoidReason(event.target.value)}
                              placeholder="例如：原痛点已彻底解决，不再作为约束"
                              style={{ width: "100%" }}
                            />
                            <div className="flex-row gap-sm" style={{ marginTop: "4px" }}>
                              <button
                                type="button"
                                className="danger sm"
                                onClick={() => onVoid(item.id)}
                                disabled={pending || voidReason.trim() === ""}
                              >
                                确认作废
                              </button>
                              <button type="button" className="sm" onClick={() => setVoidingId(null)}>
                                取消
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
