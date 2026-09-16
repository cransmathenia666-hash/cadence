"use client";

import Link from "next/link";
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

/**
 * 长期档案页：档案录入（此前档案只有读、没有写，两条主线是当年直接改库塞进去的）。
 *
 * 一页装下三件事：看五类现状与缺口、补一条、改 / 作废一条。
 * 与 P2 那几个页面同一个态度：不拆组件、不做样式，先把「档案能自己维护」走通。
 *
 * 两条边界在这页的体现：
 * - **前端不算业务规则**：类别词表、理由必填、历史行不许改，都归后端判，
 *   这页只把参数送过去、把后端那句中文 `detail` 显示出来。
 * - **档案是结论不是资料库**：输入框旁边的小字提醒你写提炼后的一两句话，
 *   不是把原始笔记搬进来。
 */

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

  // 新增表单
  const [newCategory, setNewCategory] = useState<ProfileCategory>("long_axis");
  const [newContent, setNewContent] = useState("");

  // 行内编辑：改内容必须写理由（台账要求）
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editReason, setEditReason] = useState("");

  // 两步作废：先点「作废」填理由，再确认——防手滑，也不弹系统对话框
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");

  // setState 放在 .then 回调里而不是 effect 体内同步调用，
  // 否则会被 eslint 的 react-hooks/set-state-in-effect 拦下。
  useEffect(() => {
    getProfile()
      .then((data) => {
        setProfile(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  /** 每次写完都重新取一遍：五类缺口的增减跟着后端走。 */
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
      setFeedback({
        ok: true,
        text: `已补入 #${created.id}（${PROFILE_CATEGORIES[created.category]}）`,
      });
      setNewContent("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "补入失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  function startEdit(itemId: number, content: string) {
    setEditingId(itemId);
    setVoidingId(null);
    setEditContent(content);
    setEditReason("");
  }

  async function onSaveEdit(event: FormEvent<HTMLFormElement>, itemId: number) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const updated = await updateProfileItem({
        itemId,
        content: editContent,
        reason: editReason,
      });
      setFeedback({
        ok: true,
        text: `已取代：#${updated.superseded} → #${updated.id}（旧值与理由留在台账）`,
      });
      setEditingId(null);
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "保存失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onVoid(itemId: number) {
    setPending(true);
    setFeedback(null);
    try {
      await voidProfileItem(itemId, voidReason);
      setFeedback({ ok: true, text: `已作废 #${itemId}（理由与旧值留在台账）` });
      setVoidingId(null);
      setVoidReason("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "作废失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  const items = profile?.items ?? [];

  return (
    <main>
      <h1>长期档案</h1>
      <p>
        <Link href="/">← 回计划表</Link>
      </p>

      {loadError !== null && (
        <p role="alert">
          <strong>取档案失败：</strong>
          {loadError}
        </p>
      )}

      {loadError === null && profile === null && <p role="status">正在取档案…</p>}

      {feedback !== null && (
        <p role={feedback.ok ? "status" : "alert"}>
          <strong>{feedback.ok ? "成功：" : "失败："}</strong>
          {feedback.text}
        </p>
      )}

      <section>
        <h2>补一条</h2>
        <form onSubmit={onCreate}>
          <p>
            <label htmlFor="new-category">类别：</label>
            <select
              id="new-category"
              value={newCategory}
              onChange={(event) => setNewCategory(event.target.value as ProfileCategory)}
            >
              {CATEGORY_KEYS.map((key) => (
                <option key={key} value={key}>
                  {PROFILE_CATEGORIES[key]}
                </option>
              ))}
            </select>
          </p>
          <p>
            <label htmlFor="new-content">内容（一两句话的提炼结论）：</label>
            <textarea
              id="new-content"
              value={newContent}
              onChange={(event) => setNewContent(event.target.value)}
              rows={2}
              placeholder="例如：每天能稳定投入 2 小时，工作日晚上精力一般"
              required
            />
            <br />
            <small>这里是给 AI 判断用的「结论」，不是资料库——原始笔记不用搬进来。</small>
          </p>
          <button type="submit" disabled={pending}>
            {pending ? "提交中…" : "补入档案"}
          </button>
        </form>
      </section>

      <section>
        <h2>五类现状</h2>

        {profile !== null && profile.missing_categories.length > 0 && (
          <p role="status">
            <strong>还空着：</strong>
            {profile.missing_categories
              .map(
                (key) =>
                  PROFILE_CATEGORIES[key as ProfileCategory] ?? key,
              )
              .join("、")}
            。四问里靠这些类别判断的问题只能答「依据不足」——想让 AI 答得实，先补这几类。
          </p>
        )}

        {profile !== null && items.length === 0 && (
          <p role="status">档案还一条都没有。上面补一条试试。</p>
        )}

        {CATEGORY_KEYS.map((key) => {
          const categoryItems = items.filter((item) => item.category === key);
          return (
            <div key={key}>
              <h3>
                {PROFILE_CATEGORIES[key]}
                <small>（{key}，{categoryItems.length} 条）</small>
              </h3>
              {categoryItems.length === 0 && <p role="status">这一类还空着。</p>}
              <ul>
                {categoryItems.map((item) => (
                  <li key={item.id}>
                    <p>
                      #{item.id}（{item.valid_from} 起生效）：{item.content}
                    </p>

                    <p>
                      <button
                        type="button"
                        onClick={() => startEdit(item.id, item.content)}
                        disabled={pending}
                      >
                        改
                      </button>{" "}
                      {voidingId === item.id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => onVoid(item.id)}
                            disabled={pending || voidReason.trim() === ""}
                          >
                            确认作废
                          </button>{" "}
                          <button
                            type="button"
                            onClick={() => setVoidingId(null)}
                            disabled={pending}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setVoidingId(item.id);
                            setVoidReason("");
                            setEditingId(null);
                          }}
                          disabled={pending}
                        >
                          作废
                        </button>
                      )}
                    </p>

                    {voidingId === item.id && (
                      <p>
                        <label htmlFor={`void-reason-${item.id}`}>作废理由（必填，进台账）：</label>
                        <input
                          id={`void-reason-${item.id}`}
                          value={voidReason}
                          onChange={(event) => setVoidReason(event.target.value)}
                          placeholder="例如：这条信息过期了"
                        />
                      </p>
                    )}

                    {editingId === item.id && (
                      <form onSubmit={(event) => onSaveEdit(event, item.id)}>
                        <fieldset>
                          <legend>改 #{item.id}（旧值会被取代、留痕，不删除）</legend>
                          <p>
                            <label htmlFor={`edit-content-${item.id}`}>新内容：</label>
                            <textarea
                              id={`edit-content-${item.id}`}
                              value={editContent}
                              onChange={(event) => setEditContent(event.target.value)}
                              rows={2}
                              required
                            />
                          </p>
                          <p>
                            <label htmlFor={`edit-reason-${item.id}`}>为什么改（必填，进台账）：</label>
                            <input
                              id={`edit-reason-${item.id}`}
                              value={editReason}
                              onChange={(event) => setEditReason(event.target.value)}
                              placeholder="例如：方向变了 / 信息过期了"
                              required
                            />
                          </p>
                          <button type="submit" disabled={pending}>
                            {pending ? "保存中…" : "保存"}
                          </button>{" "}
                          <button
                            type="button"
                            onClick={() => setEditingId(null)}
                            disabled={pending}
                          >
                            取消
                          </button>
                        </fieldset>
                      </form>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}

        {items.some((item) => !(item.category in PROFILE_CATEGORIES)) && (
          <div>
            <h3>其他类别</h3>
            <p>
              <small>下面这些不在约定词表里（多半是早期手工写库留下的），照常显示、不丢。</small>
            </p>
            <ul>
              {items
                .filter((item) => !(item.category in PROFILE_CATEGORIES))
                .map((item) => (
                  <li key={item.id}>
                    #{item.id}（{item.category}）：{item.content}
                  </li>
                ))}
            </ul>
          </div>
        )}
      </section>
    </main>
  );
}
