"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Plus, X } from "lucide-react";
import { ApiError, createProfileItem, getProfile, PROFILE_CATEGORIES, updateProfileItem, voidProfileItem, type ProfileCategory, type ProfileView } from "@/lib/api";

const CATEGORIES = Object.keys(PROFILE_CATEGORIES) as ProfileCategory[];
const messageOf = (cause: unknown, fallback: string) => cause instanceof ApiError ? cause.message : fallback;

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [category, setCategory] = useState<ProfileCategory>("long_axis");
  const [panel, setPanel] = useState<"create" | "edit" | "void" | null>(null);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    getProfile().then((data) => { setProfile(data); setLoadError(null); })
      .catch((cause: unknown) => setLoadError(messageOf(cause, "取档案时出了意外错误")));
  }, []);

  useEffect(() => {
    if (!panel) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.querySelector<HTMLElement>("textarea, input, button")?.focus();
    return () => previous?.focus();
  }, [panel]);

  function onPanelKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !pending) { setPanel(null); return; }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled)"));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }

  async function refresh() {
    try { setProfile(await getProfile()); setLoadError(null); }
    catch (cause) { setLoadError(messageOf(cause, "取档案时出了意外错误")); }
  }

  function openPanel(next: "create" | "edit" | "void", id: number | null = null, initial = "") {
    setPanel(next); setTargetId(id); setContent(initial); setReason(""); setFeedback(null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true); setFeedback(null);
    try {
      if (panel === "create") {
        const created = await createProfileItem({ category, content });
        setFeedback({ ok: true, text: `已补入「${PROFILE_CATEGORIES[category]}」档案 #${created.id}` });
      } else if (panel === "edit" && targetId !== null) {
        const updated = await updateProfileItem({ itemId: targetId, content, reason });
        setFeedback({ ok: true, text: `旧条目 #${targetId} 已由新条目 #${updated.id} 取代，理由已留痕。` });
      } else if (panel === "void" && targetId !== null) {
        await voidProfileItem(targetId, reason);
        setFeedback({ ok: true, text: `条目 #${targetId} 已作废，理由已留痕。` });
      }
      setPanel(null); setContent(""); setReason(""); await refresh();
    } catch (cause) { setFeedback({ ok: false, text: messageOf(cause, "保存档案失败，原因不明") }); }
    finally { setPending(false); }
  }

  const items = profile?.items ?? [];
  const visible = items.filter((item) => item.category === category);

  return (
    <div className="mx-auto w-full max-w-[1300px] px-5 pb-24 pt-20 text-white md:px-10 md:pt-24">
      <header className="mb-10 border-b border-white/[0.12] pb-8">
        <div className="flex flex-wrap items-end justify-between gap-5"><div><h1 className="text-[32px] font-semibold tracking-[-0.03em] md:text-[42px]">长期档案</h1><p className="mt-3 max-w-[64ch] text-[14px] leading-7 text-white/50">只存提炼后的结论，不堆原始笔记。这里的事实会成为判断资料与寻找方向的依据。</p></div><span className="font-mono text-[12px] tabular-nums text-white/40">{profile ? `${items.length} 条有效 · ${profile.missing_categories.length} 类待补` : "读取中"}</span></div>
      </header>

      {loadError && <p role="alert" className="mb-6 border-t border-red-400/30 pt-3 text-[13px] text-red-300">加载失败：{loadError}</p>}
      {feedback && <p role={feedback.ok ? "status" : "alert"} className={`mb-6 border-t pt-3 text-[13px] ${feedback.ok ? "border-emerald-400/30 text-emerald-300" : "border-red-400/30 text-red-300"}`}>{feedback.text}</p>}

      <div className="grid gap-8 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-12">
        <nav aria-label="档案分类" className="flex gap-2 overflow-x-auto border-b border-white/[0.1] pb-4 lg:sticky lg:top-24 lg:block lg:self-start lg:overflow-visible lg:border-b-0 lg:pb-0">
          {CATEGORIES.map((key) => {
            const count = items.filter((item) => item.category === key).length;
            return <button key={key} type="button" onClick={() => { setCategory(key); setPanel(null); }} aria-current={category === key ? "page" : undefined} className={`flex shrink-0 items-center justify-between gap-4 border-b px-1 py-3 text-left text-[13px] lg:w-full ${category === key ? "border-white text-white" : "border-white/[0.08] text-white/45 hover:text-white/80"}`}><span>{PROFILE_CATEGORIES[key]}</span><span className="font-mono text-[11px] tabular-nums opacity-60">{count || "—"}</span></button>;
          })}
        </nav>

        <main className="min-w-0">
          <div className="mb-8 flex flex-wrap items-end justify-between gap-5"><div><h2 className="text-[25px] font-medium tracking-[-0.02em]">{PROFILE_CATEGORIES[category]}</h2><p className="mt-2 text-[12px] text-white/40">{visible.length ? `${visible.length} 条正在作为判断依据` : "这个分类还没有有效结论，相关判断可能依据不足"}</p></div><button type="button" onClick={() => openPanel("create")} className="inline-flex items-center gap-2 border-b border-white/60 pb-1 text-[13px] text-white hover:border-white"><Plus className="size-4" />补充一条</button></div>
          {profile === null && !loadError && <p role="status" className="py-12 text-[13px] text-white/40">正在读取档案…</p>}
          {profile && visible.length === 0 && <div className="border-y border-white/[0.1] py-14"><p className="max-w-[42ch] text-[16px] leading-7 text-white/55">还没有可供判断的结论。补入一句真实、可复用的描述，之后的四问会引用它。</p></div>}
          <div className="border-t border-white/[0.12]">
            {visible.map((item) => <article key={item.id} className="group grid gap-3 border-b border-white/[0.09] py-6 md:grid-cols-[5rem_minmax(0,1fr)_8rem] md:gap-6"><div className="font-mono text-[11px] tabular-nums text-white/35">#{item.id}<span className="mt-1 block font-sans">{item.valid_from?.slice(0, 10)}</span></div><p className="max-w-[66ch] whitespace-pre-wrap text-[15px] leading-7 text-white/80">{item.content}</p><div className="flex gap-4 text-[12px] md:justify-end"><button type="button" onClick={() => openPanel("edit", item.id, item.content)} disabled={pending} className="text-white/45 hover:text-white disabled:opacity-40">修改</button><button type="button" onClick={() => openPanel("void", item.id)} disabled={pending} className="text-white/35 hover:text-red-300 disabled:opacity-40">作废</button></div></article>)}
          </div>
        </main>
      </div>

      {panel && <div className="fixed inset-0 z-40 flex justify-end bg-black/55" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) setPanel(null); }}><section ref={panelRef} onKeyDown={onPanelKeyDown} role="dialog" aria-modal="true" aria-labelledby="profile-panel-title" className="flex h-full w-full max-w-[470px] flex-col border-l border-white/[0.12] bg-surface-overlay px-6 pb-8 pt-20 shadow-2xl md:px-9"><div className="flex items-start justify-between gap-5 border-b border-white/[0.12] pb-6"><div><h2 id="profile-panel-title" className="text-[23px] font-medium">{panel === "create" ? "补入一条结论" : panel === "edit" ? `修改条目 #${targetId}` : `作废条目 #${targetId}`}</h2><p className="mt-2 text-[12px] leading-6 text-white/45">{panel === "create" ? `归入「${PROFILE_CATEGORIES[category]}」，保存后立即生效。` : "变更不可直接撤销，理由会写进台账。"}</p></div><button type="button" onClick={() => setPanel(null)} disabled={pending} aria-label="关闭编辑面板" className="p-1 text-white/45 hover:text-white"><X className="size-5" /></button></div><form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col pt-8"><div className="space-y-6">{panel !== "void" && <div><label htmlFor="profile-content" className="mb-2 block text-[12px] text-white/60">提炼结论</label><textarea id="profile-content" value={content} onChange={(event) => setContent(event.target.value)} rows={5} required placeholder="用一两句话写下可供后续判断的事实" className="w-full resize-y border border-white/[0.16] bg-white/[0.03] p-4 text-[14px] leading-6 text-white outline-none placeholder:text-white/25 focus:border-white/50" /></div>}{panel !== "create" && <div><label htmlFor="profile-reason" className="mb-2 block text-[12px] text-white/60">{panel === "void" ? "为什么不再使用这条结论？" : "修改理由"}</label><input id="profile-reason" value={reason} onChange={(event) => setReason(event.target.value)} required placeholder="例如：实际时间安排已经改变" className="w-full border border-white/[0.16] bg-white/[0.03] p-3 text-[14px] text-white outline-none placeholder:text-white/25 focus:border-white/50" /></div>}</div>{feedback && !feedback.ok && <p role="alert" className="mt-6 text-[12px] text-red-300">{feedback.text}。填写内容还在，可重试。</p>}<div className="mt-auto flex items-center justify-between gap-4 border-t border-white/[0.12] pt-6"><button type="button" onClick={() => setPanel(null)} disabled={pending} className="text-[13px] text-white/45 hover:text-white">取消</button><button type="submit" disabled={pending || (panel !== "void" && !content.trim()) || (panel !== "create" && !reason.trim())} className={`inline-flex min-h-10 items-center gap-2 px-5 text-[13px] font-medium disabled:opacity-40 ${panel === "void" ? "bg-red-900 text-white" : "bg-white text-black"}`}>{pending ? "正在保存…" : panel === "void" ? "确认作废" : panel === "edit" ? "保存替换" : "保存进档案"}<ArrowRight className="size-4" /></button></div></form></section></div>}
    </div>
  );
}
