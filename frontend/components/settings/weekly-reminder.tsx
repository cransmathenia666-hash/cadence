"use client";

import { useEffect, useState } from "react";
import { ArrowRight, ChevronDown, RefreshCw, TriangleAlert } from "lucide-react";

import { ApiError, getNotify, runExport, updateNotify, type NotifyState } from "@/lib/api";

/**
 * 设置页的「每周提醒与导出」那一节（P4 触达）。
 *
 * 为什么融进设置页而不是单开一页：这里九成时间是「拧开关 + 看一眼状态」，
 * 而提醒里那三问的内容已经长在计划树上了——单开一页会是同一件事的第二个说法。
 * 导出跟提醒同一时刻发生、同一份数据，所以同住这一节。
 */

const messageOf = (cause: unknown, fallback: string) =>
  cause instanceof ApiError ? cause.message : fallback;

function moment(iso: string) {
  return iso.slice(0, 16).replace("T", " ");
}

function bytesLabel(bytes: number) {
  return bytes < 1024 ? `${bytes} 字节` : `${(bytes / 1024).toFixed(1)} KB`;
}

function weekLine(state: NotifyState) {
  if (!state.plan_id) return "还没有进行中的计划";
  if (state.due) return state.missed_week ? `要补发上周（${state.missed_week}）` : "这周有一封要发";
  return state.decision_reason;
}

export function WeeklyReminder() {
  const [state, setState] = useState<NotifyState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<"save" | "export" | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getNotify()
      .then((data) => {
        setState(data);
        setDraft(data.to_addr ?? "");
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, "读每周提醒的状态时出了意外错误")));
  }, []);

  async function save(input: { enabled?: boolean; toAddr?: string }, note: string) {
    setPending("save");
    setFeedback(null);
    try {
      const next = await updateNotify(input);
      setState(next);
      setDraft(next.to_addr ?? "");
      setFeedback({ ok: true, text: note });
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "没保存成，原因不明") });
    } finally {
      setPending(null);
    }
  }

  async function onExport() {
    setPending("export");
    setFeedback(null);
    try {
      const result = await runExport();
      setState(await getNotify());
      setFeedback({ ok: true, text: `已导出 ${result.files.length} 个文件到 ${result.dir}` });
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "导出失败，原因不明") });
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;

  return (
    <section className="mt-20 border-t border-white/[0.14] pt-7" aria-labelledby="weekly-reminder-title">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 id="weekly-reminder-title" className="text-[21px] font-medium">
            每周提醒
          </h2>
          <p className="mt-2 max-w-[62ch] text-[12px] leading-6 text-white/45">
            每周一早上把「当前阶段 / 本周推到哪 / 建议怎么调」发到你的邮箱。手机打不开本地页面，所以邮件只做提醒、不放操作链接；电脑关着错过的那次，下次开机会补发。
          </p>
        </div>
        {state && <span className="font-mono text-[11px] text-white/40">下次 {moment(state.next_send_at)}</span>}
      </div>

      {loadError && (
        <p role="alert" className="mt-6 border-t border-red-400/30 pt-3 text-[13px] text-red-300">
          读取失败：{loadError}
        </p>
      )}
      {!state && !loadError && <p className="mt-6 text-[12px] text-white/35">正在读取提醒的状态…</p>}

      {state && (
        <>
          {feedback && (
            <p
              role={feedback.ok ? "status" : "alert"}
              className={`mt-6 border-t pt-3 text-[13px] ${feedback.ok ? "border-emerald-400/30 text-emerald-300" : "border-red-400/30 text-red-300"}`}
            >
              {feedback.text}
            </p>
          )}

          <div className="mt-8 flex flex-wrap items-center gap-x-8 gap-y-4">
            <label className="flex cursor-pointer items-center gap-3 text-[13px] text-white/80">
              <input
                type="checkbox"
                checked={state.enabled}
                disabled={busy}
                onChange={(event) =>
                  save(
                    { enabled: event.target.checked, toAddr: draft },
                    event.target.checked ? "每周提醒打开了。" : "每周提醒关掉了。",
                  )
                }
                className="size-4 accent-white disabled:opacity-40"
              />
              {state.enabled ? "每周提醒已打开" : "打开每周提醒"}
            </label>
            <label className="flex flex-wrap items-center gap-3 text-[12px] text-white/50">
              收件邮箱
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="you@example.com"
                aria-label="收件邮箱"
                className="provider-input w-[17rem]"
              />
            </label>
            <button
              type="button"
              onClick={() => save({ toAddr: draft }, "收件邮箱已保存。")}
              disabled={busy || draft === (state.to_addr ?? "")}
              className="inline-flex min-h-9 items-center gap-2 border border-white/25 px-4 text-[12px] text-white/80 hover:border-white/60 disabled:opacity-35"
            >
              保存邮箱
            </button>
          </div>

          <dl className="mt-9 grid gap-x-9 sm:grid-cols-3">
            <Fact label="下次发送" value={`${moment(state.next_send_at)}（每周一 09:00）`} />
            <Fact label="本周" value={weekLine(state)} />
            <Fact
              label="通道"
              value={state.smtp_configured ? "邮件" : "空实现 · 还没配 SMTP"}
            />
          </dl>

          {state.notice && (
            <p className="mt-6 flex items-start gap-2 text-[12px] leading-6 text-amber-200/85">
              <TriangleAlert className="mt-1 size-3.5 shrink-0" aria-hidden />
              {state.notice}
            </p>
          )}
          {!state.smtp_configured && state.smtp_missing_env.length > 0 && (
            <p className="mt-2 font-mono text-[11px] text-white/35">
              SMTP 还缺：{state.smtp_missing_env.join(" · ")}
            </p>
          )}

          <details className="group mt-8 border-t border-white/[0.08] pt-5">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[12px] text-white/55 hover:text-white">
              <span className="min-w-0 truncate">本周这封会说什么 · {state.preview.subject}</span>
              <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <p className="mt-4 whitespace-pre-wrap text-[13px] leading-7 text-white/65">
              {state.preview.body}
            </p>
          </details>

          <div className="mt-8 border-t border-white/[0.08] pt-5">
            <h3 className="text-[13px] font-medium text-white/60">最近发送</h3>
            {state.recent.length === 0 ? (
              <p className="mt-3 text-[12px] leading-6 text-white/40">
                还没有发过。打开开关后第一个周一（或手动跑一次任务）会留下第一行。
              </p>
            ) : (
              <ul className="mt-1 divide-y divide-white/[0.07]">
                {state.recent.map((record) => (
                  <li key={record.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3 text-[12px]">
                    <span className="font-mono text-white/45">{moment(record.sent_at)}</span>
                    <span className="text-white/70">{record.kind_label}</span>
                    <span className="text-white/40">{record.channel_label}</span>
                    <span className={record.ok ? "text-emerald-300/90" : "text-red-300"}>
                      {record.ok
                        ? record.channel === "email"
                          ? "已发送"
                          : "已记账 · 没真发"
                        : `失败：${record.error ?? "原因不明"}`}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-right text-white/30">{record.subject}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="mt-8 border-t border-white/[0.08] pt-5">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <h3 className="text-[13px] font-medium text-white/60">导出四个只读文件</h3>
              <button
                type="button"
                onClick={onExport}
                disabled={busy}
                className="inline-flex min-h-9 items-center gap-2 border border-white/25 px-4 text-[12px] text-white/80 hover:border-white/60 disabled:opacity-35"
              >
                {pending === "export" ? (
                  <RefreshCw className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <ArrowRight className="size-3.5" aria-hidden />
                )}
                {pending === "export" ? "正在导出…" : "手动导出"}
              </button>
            </div>
            <p className="mt-2 max-w-[62ch] text-[12px] leading-6 text-white/45">
              计划-当前 · 决策台账 · 档案-当前 · 周检查点（每周提醒的同一时刻会自动导一次）。导出只写文件，库里的数据一行都不动；你在文件上改的字，下次导出会被覆盖。
            </p>
            {state.export.last_at ? (
              <>
                <p className="mt-4 font-mono text-[11px] text-white/35">
                  最近导出 {moment(state.export.last_at)} → {state.export.dir}
                </p>
                <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-2 text-[12px] text-white/55">
                  {state.export.files.slice(0, 8).map((file) => (
                    <li key={file.name}>
                      {file.name}
                      <span className="ml-2 font-mono text-[11px] text-white/30">{bytesLabel(file.bytes)}</span>
                    </li>
                  ))}
                </ul>
                {state.export.files.length > 8 && (
                  <p className="mt-2 text-[11px] text-white/35">
                    还有 {state.export.files.length - 8} 个更早的快照在同一个目录里。
                  </p>
                )}
              </>
            ) : (
              <p className="mt-4 text-[12px] text-white/40">还没导出过。</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-b border-white/[0.07] py-4">
      <dt className="mb-2 text-[11px] text-white/35">{label}</dt>
      <dd className="break-words text-[14px] leading-6 text-white/75">{value}</dd>
    </div>
  );
}
