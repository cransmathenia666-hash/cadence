"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, CornerDownRight, History, ScanSearch } from "lucide-react";

import { JudgmentView } from "@/components/judgment-view";
import { ApiError, askMaterial, listJudgments, getProfile, type AskResult, type JudgmentRecord, type ProfileView } from "@/lib/api";

export default function JudgePage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<JudgmentRecord[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    getProfile().then(setProfile).catch(() => setProfile(null));
    listJudgments().then((data) => setHistory(data.items)).catch((cause: unknown) =>
      setHistoryError(cause instanceof ApiError ? cause.message : "取历史时出了意外错误"),
    );
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setError(null);
    setResult(null);
    setSelectedId(null);
    try {
      setResult(await askMaterial(rawText));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "问模型失败，原因不明");
    } finally {
      setAsking(false);
    }
  }

  const selected = history?.find((item) => item.id === selectedId);

  return (
    <div className="mx-auto w-full max-w-[1320px] px-5 pb-24 pt-20 text-white md:px-10 md:pt-24">
      <header className="mb-12 grid gap-6 border-b border-white/[0.1] pb-8 lg:grid-cols-[minmax(0,1fr)_16rem] lg:items-end">
        <div>
          <h1 className="text-[32px] font-semibold tracking-[-0.03em] md:text-[42px]">一份资料，值不值得学？</h1>
          <p className="mt-3 max-w-[60ch] text-[14px] leading-7 text-white/50">把课程、书籍或项目交给四问判断。它会对照长期档案，给出深度、取舍和时间预算；结论仍由你裁定。</p>
        </div>
        <Link href="/profile" className="group flex items-center justify-between border-t border-white/[0.12] pt-4 text-[12px] text-white/55 hover:text-white">
          <span>判断依据 · {profile ? `${profile.items.length} 条有效档案` : "正在读取档案"}</span><ArrowRight className="size-4 transition-transform group-hover:translate-x-1" />
        </Link>
      </header>

      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <main className="min-w-0">
          <form onSubmit={onSubmit} className="border-b border-white/[0.12] pb-8">
            <label htmlFor="raw" className="mb-4 block text-[12px] font-medium text-white/60">你正在犹豫什么资料？</label>
            <textarea id="raw" rows={3} value={rawText} onChange={(event) => setRawText(event.target.value)} placeholder="例如：我看到一个 Rust 异步并发与网络协议实战课，要不要学？" disabled={asking} required className="w-full resize-y border-0 bg-transparent p-0 text-[19px] leading-8 text-white outline-none placeholder:text-white/25 focus:ring-0 md:text-[23px]" />
            <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
              <span className="text-[11px] text-white/35">判断 · 深度 · 取舍 · 时间，四问一次回答</span>
              <button type="submit" disabled={asking || !rawText.trim()} className="inline-flex min-h-10 items-center gap-3 bg-white px-5 text-[13px] font-semibold text-black hover:bg-white/85 disabled:cursor-not-allowed disabled:opacity-40">
                {asking ? "正在按四问评判…" : "开始评判"}<ArrowRight className="size-4" />
              </button>
            </div>
          </form>

          {error && <p role="alert" className="mt-6 border-t border-red-400/30 pt-4 text-[13px] text-red-300">评判失败：{error}。原文还在，可以重试。</p>}
          {asking && <div role="status" className="mt-12 flex items-center gap-3 text-[13px] text-white/50"><ScanSearch className="size-5 animate-pulse" />正在对照档案，请稍候…</div>}

          {result ? (
            <section className="mt-12" aria-labelledby="judgment-title">
              <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
                <div><h2 id="judgment-title" className="text-[22px] font-medium">四问判断</h2><p className="mt-1 text-[11px] text-white/40">依据 {result.profile_basis.total} 条档案 · 模型调用 {result.calls} 次</p></div>
                <Link href="/proposals" className="inline-flex items-center gap-2 text-[13px] text-white underline underline-offset-4 hover:text-white/65">去裁定并记账 #{result.proposal_id}<ArrowRight className="size-4" /></Link>
              </div>
              <JudgmentView judgment={result.judgment} />
              <p className="mt-5 max-w-[65ch] text-[12px] leading-6 text-white/40">判断已经生成待裁定提案。批准只留下决策记录，不会自动改变执行计划。</p>
            </section>
          ) : selected?.judgment ? (
            <section className="mt-12" aria-labelledby="history-detail-title">
              <div className="mb-7 flex items-start justify-between gap-4"><div><h2 id="history-detail-title" className="max-w-[65ch] text-[21px] font-medium leading-snug">{selected.source_text}</h2><p className="mt-2 text-[12px] text-white/40">{selected.decided_at?.slice(0, 10)} · {selected.status === "accepted" ? "已批准" : "已否决"} · 历史记录只读</p></div><button type="button" onClick={() => setSelectedId(null)} className="shrink-0 text-[12px] text-white/45 hover:text-white">收起</button></div>
              <JudgmentView judgment={selected.judgment} />
            </section>
          ) : (
            <div className="mt-14 flex min-h-32 items-start gap-4 text-white/35"><CornerDownRight className="mt-1 size-5 shrink-0" /><p className="max-w-[42ch] text-[14px] leading-7">写下你正在考虑的资料，判断会在这里展开；也可以从右侧翻看已裁定的记录。</p></div>
          )}
        </main>

        <aside className="min-w-0 border-t border-white/[0.12] pt-5 lg:border-l lg:border-t-0 lg:pl-7 lg:pt-0" aria-label="历史判断">
          <div className="mb-5 flex items-center gap-2 text-white/65"><History className="size-4" /><h2 className="text-[13px] font-medium">已裁定的判断</h2><span className="ml-auto font-mono text-[11px] text-white/35">{history?.length ?? "—"}</span></div>
          {historyError && <p role="alert" className="text-[12px] text-red-300">{historyError}</p>}
          {history === null && !historyError && <p role="status" className="text-[12px] text-white/35">正在读取历史…</p>}
          {history?.length === 0 && <p className="text-[12px] leading-6 text-white/40">还没有已裁定记录。完成一次评判并裁定后会出现在这里。</p>}
          <div className="divide-y divide-white/[0.08]">
            {history?.map((item) => <button key={item.id} type="button" onClick={() => { setResult(null); setSelectedId(selectedId === item.id ? null : item.id); }} aria-pressed={selectedId === item.id} className={`w-full py-4 text-left transition-colors hover:text-white ${selectedId === item.id ? "text-white" : "text-white/60"}`}><span className="block text-[13px] leading-6">{item.source_text}</span><span className="mt-2 block text-[11px] text-white/35">{item.decided_at?.slice(0, 10)} · {item.status === "accepted" ? "批准" : "否决"}</span></button>)}
          </div>
        </aside>
      </div>
    </div>
  );
}
