"use client";

// 知识库页（KB-05）：本机原始资料的受控入口。
//
// 构图：**左操作台、右覆盖台账**（与记忆页同一套语法）。首屏的主张是「覆盖账」——
// 头部的已覆盖统计 + 主栏那册逐篇台账，一眼回答「我的笔记被读到哪了」。扫描回执
// 是一张账单：读了多少、出了几条候选、哪里没读完，逐条如实列出，不假装看完。
//
// 两条口径照后端来，页面不自己发明：
// ① 文件的 covered 由后端按当前内容哈希对覆盖账算（改过的文件重扫会重新处理）；
// ② 扫描 failed / partial 是业务结果（照常 200 返回），读 `status` 把实话摆出来，
//    异常只留给「请求本身没发出去」。
//
// 绝不显示绝对路径、绝不渲染整篇文件：这两个口子后端本来就没给——页面只有相对路径、
// 大小、时间、覆盖状态与账面字段。

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  FileText,
  Library,
  Loader2,
  ShieldAlert,
} from "lucide-react";

import {
  ApiError,
  createKnowledgeRoot,
  getKnowledgeFiles,
  getKnowledgeRoots,
  getKnowledgeScans,
  startKnowledgeScan,
  updateKnowledgeRoot,
  type KnowledgeFileListing,
  type KnowledgeLimits,
  type KnowledgeRoot,
  type KnowledgeScanRecord,
  type KnowledgeScanReport,
} from "@/lib/api";
import { ActionSwapButton } from "@/components/motion/action-swap";
import {
  KnowledgeRootPanel,
  KnowledgeRootsRail,
  type RootDraft,
} from "@/components/knowledge/root-config";

const LOAD_ROOTS_FAILED = "取知识库配置时出了意外错误";
const LOAD_FILES_FAILED = "取文件清单时出了意外错误";

const SCAN_STATUS_LABELS: Record<string, string> = {
  running: "进行中",
  ok: "完成",
  partial: "有缺口",
  failed: "失败",
};

const SCAN_TRIGGER_LABELS: Record<string, string> = {
  manual: "手动",
  weekly: "每周",
};

/** 逐篇结果的状态：covered / reviewed_no_change 都算处理完，partial / failed 是没做成。 */
const FILE_OUTCOME_LABELS: Record<string, string> = {
  covered: "已覆盖",
  reviewed_no_change: "无新增",
  partial: "没读完",
  failed: "失败",
};

/** 一次根目录取数：文件清单 + 扫描历史。纯取数不碰视图，落视图由调用方在回调里做。 */
async function fetchRootData(rootId: number): Promise<{ files: KnowledgeFileListing; scans: KnowledgeScanRecord[] }> {
  const [files, history] = await Promise.all([
    getKnowledgeFiles(rootId),
    getKnowledgeScans(rootId, 12),
  ]);
  return { files, scans: history.scans };
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 本地显示用的时间（后端给的是带时区的 ISO 串，截到分就够台账读）。 */
function formatWhen(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function LibraryPage() {
  const [roots, setRoots] = useState<KnowledgeRoot[] | null>(null);
  // 读取配额的上限：后端随根目录清单一起给，界面只按它画比例（不自己抄一份数字）。
  const [limits, setLimits] = useState<KnowledgeLimits | null>(null);
  const [selectedRootId, setSelectedRootId] = useState<number | null>(null);
  const [listing, setListing] = useState<KnowledgeFileListing | null>(null);
  const [scans, setScans] = useState<KnowledgeScanRecord[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [scanMode, setScanMode] = useState<"incremental" | "full">("incremental");
  const [scanPending, setScanPending] = useState(false);
  const [lastReport, setLastReport] = useState<KnowledgeScanReport | null>(null);

  // 配置面：null = 在看台账；有值 = 正在添加 / 编辑某个根，主栏让给它。
  const [draft, setDraft] = useState<RootDraft | null>(null);
  const [configBusy, setConfigBusy] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // 异步切换根目录的竞态闸：只有最后一次请求有权落视图（页面重做模板的老规矩）。
  const filesSeq = useRef(0);

  /** 配置动过之后重取根目录：优先选中刚动过的那个，否则退回第一个可用的。 */
  async function reloadAfterConfig(preferId: number | null) {
    const data = await getKnowledgeRoots();
    setRoots(data.roots);
    setLimits(data.limits);
    const usable = data.roots.filter((root) => root.enabled);
    const pick =
      (preferId === null ? undefined : usable.find((root) => root.id === preferId)) ??
      usable[0] ??
      null;
    setSelectedRootId(pick ? pick.id : null);
    setLastReport(null);
    setActionError(null);
  }

  function openAdd() {
    setConfigError(null);
    setDraft({ id: null, alias: "", path: "", enabled: true });
  }

  function openEdit(root: KnowledgeRoot) {
    setConfigError(null);
    setDraft({ id: root.id, alias: root.alias, path: root.path ?? "", enabled: root.enabled });
  }

  async function saveDraft() {
    if (draft === null) return;
    const alias = draft.alias.trim();
    const path = draft.path.trim();
    setConfigBusy(true);
    setConfigError(null);
    try {
      const saved =
        draft.id === null
          ? await createKnowledgeRoot({ alias, path, enabled: draft.enabled })
          : await updateKnowledgeRoot(draft.id, { alias, path, enabled: draft.enabled });
      setDraft(null);
      await reloadAfterConfig(saved.id);
    } catch (cause) {
      setConfigError(messageOf(cause, "保存时出了意外错误"));
    } finally {
      setConfigBusy(false);
    }
  }

  useEffect(() => {
    let alive = true;
    getKnowledgeRoots()
      .then((data) => {
        if (!alive) return;
        setRoots(data.roots);
        setLimits(data.limits);
        // 默认选中第一个可用的根；全被停用就不选，主栏把原因摆出来。
        const firstUsable = data.roots.find((root) => root.enabled) ?? null;
        setSelectedRootId(firstUsable ? firstUsable.id : null);
      })
      .catch((cause: unknown) => {
        if (alive) setLoadError(messageOf(cause, LOAD_ROOTS_FAILED));
      });
    return () => {
      alive = false;
    };
  }, []);

  // 取数写在 .then 回调里：effect 里同步 setState 会被 react-hooks/set-state-in-effect 拦。
  useEffect(() => {
    if (selectedRootId === null) return;
    const seq = ++filesSeq.current;
    fetchRootData(selectedRootId)
      .then((data) => {
        if (filesSeq.current !== seq) return;
        setListing(data.files);
        setScans(data.scans);
        setLoadError(null);
      })
      .catch((cause: unknown) => {
        if (filesSeq.current !== seq) return;
        setLoadError(messageOf(cause, LOAD_FILES_FAILED));
      });
  }, [selectedRootId]);

  async function onScan() {
    if (selectedRootId === null || scanPending) return;
    setScanPending(true);
    setActionError(null);
    try {
      // 失败是业务结果：报告照常回来，状态与缺口都画在回执里。
      const report = await startKnowledgeScan({ rootId: selectedRootId, mode: scanMode });
      setLastReport(report);
      // 覆盖账变了：清单与历史重取一遍（带着竞态闸，慢回来的旧响应落不了视图）。
      const seq = ++filesSeq.current;
      const data = await fetchRootData(selectedRootId);
      if (filesSeq.current === seq) {
        setListing(data.files);
        setScans(data.scans);
        setLoadError(null);
      }
    } catch (cause) {
      setActionError(messageOf(cause, "扫描没跑成，原因不明"));
    } finally {
      setScanPending(false);
    }
  }

  const usableRoots = (roots ?? []).filter((root) => root.enabled);
  // 台账只认「当前选中根」的那份清单：切换根目录的瞬间旧清单按归属失配自动让位，
  // 不需要在 effect 里同步清状态（react-hooks/set-state-in-effect）。
  const currentListing = listing !== null && listing.root.id === selectedRootId ? listing : null;
  const files = currentListing?.files ?? [];
  const coveredCount = files.filter((file) => file.covered).length;
  const selectedRoot = roots?.find((root) => root.id === selectedRootId) ?? null;

  return (
    <div className="min-h-full px-5 pb-16 pt-20 md:px-8 lg:px-12">
      <div className="mx-auto w-full max-w-[1440px]">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-3 border-b border-white/[0.1] pb-5">
          <div className="flex items-center gap-3">
            <Library className="size-5 text-white/70" aria-hidden="true" />
            <div>
              <h1 className="text-[26px] font-semibold tracking-tight text-white">知识库</h1>
              <p className="mt-0.5 max-w-[64ch] text-xs leading-5 text-white/50">
                本机笔记只进受控扫描：逐篇读原文，只产带出处的档案候选——写不写，由你在提案页裁定。
              </p>
            </div>
          </div>
          {currentListing !== null && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-l border-white/[0.1] pl-5">
              <Metric label="已覆盖" value={`${coveredCount}/${files.length}`} />
              <Metric label="待处理" value={String(files.length - coveredCount)} accent={files.length > coveredCount ? "amber" : "white"} />
              <Metric label="可用根" value={String(usableRoots.length)} />
            </div>
          )}
        </header>

        {loadError !== null && (
          <div
            role="alert"
            className="mb-5 flex items-start gap-3 rounded-2xl border border-red-300/15 bg-red-400/10 px-4 py-3 text-sm text-red-200"
          >
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <span>{loadError}</span>
          </div>
        )}
        {actionError !== null && (
          <div
            role="alert"
            className="mb-5 flex items-start gap-3 rounded-2xl border border-red-300/15 bg-red-400/10 px-4 py-3 text-sm text-red-200"
          >
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <span>{actionError}</span>
          </div>
        )}

        <div className="grid gap-10 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="min-w-0 border-b border-white/[0.1] pb-8 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-8">
            <div className="space-y-8">
              <KnowledgeRootsRail
                roots={roots}
                selectedRootId={selectedRootId}
                locked={scanPending}
                onSelect={(root) => {
                  setDraft(null);
                  setSelectedRootId(root.id);
                  setLastReport(null);
                  setActionError(null);
                }}
                onAdd={openAdd}
                onEdit={openEdit}
                onImported={() => void reloadAfterConfig(null)}
              />

              <RailBlock title="扫描" hint="只产候选提案，不直接改档案；改过的文件会重新处理">
                <div className="flex gap-1" role="group" aria-label="扫描模式">
                  {(
                    [
                      { value: "incremental", label: "增量" },
                      { value: "full", label: "全部重扫" },
                    ] as const
                  ).map((mode) => (
                    <button
                      key={mode.value}
                      type="button"
                      aria-pressed={scanMode === mode.value}
                      disabled={scanPending}
                      onClick={() => setScanMode(mode.value)}
                      className={`flex-1 rounded-md border px-2 py-1.5 text-xs transition-colors disabled:opacity-40 ${
                        scanMode === mode.value
                          ? "border-white/80 bg-white/[0.08] text-white"
                          : "border-white/[0.14] text-white/45 hover:border-white/40 hover:text-white/80"
                      }`}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-[11px] text-white/40">
                  {scanMode === "incremental" ? "跳过当前版本已覆盖的文件，只读新的与改过的。" : "整个根从头再读一遍，覆盖账按最新内容重算。"}
                </p>
                <div className="mt-3">
                  <ActionSwapButton
                    items={[
                      { id: "idle", label: "开始扫描", icon: <FileText className="size-3.5" aria-hidden="true" /> },
                      { id: "processing", label: "扫描中", icon: <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> },
                    ]}
                    value={scanPending ? "processing" : "idle"}
                    animation="roll"
                    variant="primary"
                    size="md"
                    onClick={() => void onScan()}
                    disabled={selectedRootId === null || scanPending}
                    className="w-full rounded-md bg-white text-[13px] text-black hover:bg-white/90"
                    title={selectedRootId === null ? "先选一个可用的根目录" : undefined}
                  />
                </div>
                <p className="mt-2.5 text-[11px] leading-5 text-white/45">
                  逐篇读原文并提炼，整库一次扫完可能要等十几分钟——
                  <span className="text-white/70">别关页面</span>，完成后下方会给出这次的账与缺口。
                </p>
                {lastReport !== null && limits !== null && (
                  <div className="mt-3 space-y-2.5 border-t border-white/[0.08] pt-3">
                    <Meter label="文件读取次数" value={lastReport.read_calls} max={limits.max_read_calls} />
                    <Meter label="读取字符" value={lastReport.chars_read} max={limits.max_total_chars} />
                  </div>
                )}
              </RailBlock>

              <RailBlock title="扫描历史" hint="这个根的历次扫描，新的在前">
                {scans.length === 0 ? (
                  <p className="text-[11px] leading-5 text-white/40">
                    {selectedRootId === null ? "还没有可用的根目录。" : "还没有扫描记录。选好根目录后点「开始扫描」。"}
                  </p>
                ) : (
                  <ul className="space-y-1.5 border-l border-white/[0.1] pl-2.5">
                    {scans.map((scan) => (
                      <li key={scan.scan_id} className="text-[11px] leading-5 text-white/45">
                        <span className="text-white/60">#{scan.scan_id}</span>
                        {" · "}
                        {SCAN_TRIGGER_LABELS[scan.trigger] ?? scan.trigger}
                        {" · "}
                        <span className={scan.status === "failed" ? "text-red-200/80" : scan.status === "partial" ? "text-amber-200/80" : "text-white/60"}>
                          {SCAN_STATUS_LABELS[scan.status] ?? scan.status}
                        </span>
                        {scan.status !== "failed" && (
                          <> · {scan.files_considered} 篇 → {scan.candidates_created} 条候选</>
                        )}
                        <span className="block text-white/35">{formatWhen(scan.created_at)}</span>
                        {scan.error && <span className="mt-0.5 block text-red-200/65">{scan.error}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </RailBlock>
            </div>
          </aside>

          <main className="min-w-0">
            {draft !== null ? (
              <KnowledgeRootPanel
                draft={draft}
                busy={configBusy}
                error={configError}
                onChange={(patch) =>
                  setDraft((current) => (current === null ? current : { ...current, ...patch }))
                }
                onSave={() => void saveDraft()}
                onCancel={() => {
                  setDraft(null);
                  setConfigError(null);
                }}
                onDeleted={() => {
                  setDraft(null);
                  void reloadAfterConfig(null);
                }}
              />
            ) : roots !== null && roots.length === 0 ? (
              <EmptyLibrary onAdd={openAdd} />
            ) : roots !== null && selectedRootId === null ? (
              <DisabledLibrary roots={roots} onAdd={openAdd} />
            ) : (
              <section className="space-y-9" aria-busy={scanPending}>
                {lastReport !== null && <ScanReceipt report={lastReport} limits={limits} />}

                <section>
                  <div className="flex items-baseline justify-between border-b border-white/[0.14] pb-2.5">
                    <h2 className="text-sm font-medium text-white">
                      文件台账
                      <span className="ml-2 text-xs font-normal text-white/45">{currentListing === null ? "…" : `${files.length} 篇`}</span>
                    </h2>
                    <span className="text-[11px] text-white/40">
                      {selectedRoot ? `根「${selectedRoot.alias}」` : ""}
                      {selectedRoot ? " · 只认 .md / .markdown / .txt" : ""}
                    </span>
                  </div>

                  {currentListing === null && loadError === null && (
                    <p role="status" className="py-8 text-[13px] text-white/50">正在读取文件清单…</p>
                  )}
                  {currentListing !== null && files.length === 0 && (
                    <div className="border-b border-white/[0.08] py-12">
                      <p className="max-w-[46ch] text-[14px] leading-7 text-white/55">
                        这个根目录下没有可读的文件。只认 Markdown 与纯文本；配置、数据库与可执行文件一律不读。
                      </p>
                    </div>
                  )}
                  {currentListing !== null && files.length > 0 && (
                    <>
                      <div className="hidden grid-cols-[minmax(0,1fr)_6rem_9rem_6rem] gap-4 border-b border-white/[0.06] pb-2 pt-4 text-[10px] uppercase tracking-[0.14em] text-white/35 md:grid">
                        <span>路径</span>
                        <span className="text-right">大小</span>
                        <span className="text-right">修改于</span>
                        <span className="text-right">覆盖</span>
                      </div>
                      <ul>
                        {files.map((file) => (
                          <li
                            key={file.relative_path}
                            className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 border-b border-white/[0.06] py-3 transition-colors hover:bg-white/[0.02] md:grid-cols-[minmax(0,1fr)_6rem_9rem_6rem] md:gap-4"
                          >
                            <span className="break-all font-mono text-[12.5px] leading-5 text-white/80">
                              {file.relative_path}
                            </span>
                            <span className="text-right font-mono text-[12px] tabular-nums text-white/45 md:text-left">
                              {formatSize(file.size)}
                            </span>
                            <span className="col-span-2 text-[11px] tabular-nums text-white/35 md:col-span-1 md:text-right">
                              {formatWhen(file.modified_at)}
                            </span>
                            <span className="col-span-2 text-[11px] md:col-span-1 md:text-right">
                              {file.covered ? (
                                <span className="inline-flex items-center justify-end gap-1.5 text-green/90">
                                  <CheckCircle2 className="size-3.5" aria-hidden="true" />已覆盖
                                </span>
                              ) : (
                                <span className="text-white/40">未覆盖</span>
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </section>
              </section>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

/** 还没配任何根时的空态：说清怎么加，不假装页面坏了。 */
function EmptyLibrary({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex min-h-[52vh] flex-col items-start justify-center border-b border-white/10 py-16">
      <Library className="mb-8 size-9 stroke-[1.4] text-white/45" aria-hidden="true" />
      <h2 className="text-[clamp(1.75rem,3vw,2.5rem)] font-medium tracking-[-0.025em]">还没有知识库</h2>
      <p className="mt-4 max-w-[52ch] text-[14px] leading-7 text-white/65">
        选一个本机目录加进来（笔记、资料都行）。扫描只读它里面的 Markdown 与纯文本，
        逐篇读原文、只产带出处的档案候选——写不写由你在提案页裁定，它一个文件都不会改。
      </p>
      <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3">
        <button
          type="button"
          onClick={onAdd}
          className="rounded-md bg-white px-4 py-2 text-[13px] text-black transition-colors hover:bg-white/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/70"
        >
          添加知识库
        </button>
        <span className="max-w-[40ch] text-[12px] leading-5 text-white/40">
          也可以继续用本机环境变量
          <span className="mx-1 font-mono text-[11.5px] text-white/60">CADENCE_KNOWLEDGE_ROOTS</span>
          配「别名=绝对路径」——加进来之后能一键搬到界面里管。
        </span>
      </div>
    </div>
  );
}

/** 根都配了但没有一个可用：把后端写好的原因逐个摆出来。 */
function DisabledLibrary({ roots, onAdd }: { roots: KnowledgeRoot[]; onAdd: () => void }) {
  return (
    <div className="flex min-h-[52vh] flex-col items-start justify-center border-b border-white/10 py-16">
      <ShieldAlert className="mb-8 size-9 stroke-[1.4] text-white/45" aria-hidden="true" />
      <h2 className="text-[clamp(1.75rem,3vw,2.5rem)] font-medium tracking-[-0.025em]">当前没有可用的知识库</h2>
      <ul className="mt-5 space-y-2">
        {roots.map((root) => (
          <li key={`${root.source}-${root.id}`} className="max-w-[60ch] text-[13px] leading-6 text-white/60">
            <span className="text-white/85">{root.alias}</span>
            {root.error && <span className="text-amber-200/75"> · {root.error}</span>}
          </li>
        ))}
      </ul>
      <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3">
        <button
          type="button"
          onClick={onAdd}
          className="rounded-md bg-white px-4 py-2 text-[13px] text-black transition-colors hover:bg-white/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/70"
        >
          换个目录加一个
        </button>
        <span className="max-w-[44ch] text-[12px] leading-5 text-white/40">
          停用的那些可以在左栏点开改名或换目录，改好就恢复。
        </span>
      </div>
    </div>
  );
}

/** 一次扫描的账单：状态、四个账面数字、配额、缺口与逐篇下场。 */
function ScanReceipt({ report, limits }: { report: KnowledgeScanReport; limits: KnowledgeLimits | null }) {
  const headline =
    report.status === "ok"
      ? "这次扫描全部处理完"
      : report.status === "failed"
        ? "这次扫描失败了"
        : "扫描完成，但有没做完的";
  const handledFiles = report.files.filter((file) => file.status === "covered" || file.status === "reviewed_no_change");

  return (
    <section aria-label="最近一次扫描结果">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-white/[0.14] pb-2.5">
        <h2 className={`text-sm font-medium ${report.status === "failed" ? "text-red-200" : report.status === "partial" ? "text-amber-100" : "text-white"}`}>
          {headline}
        </h2>
        <span className="text-[11px] tabular-nums text-white/40">
          #{report.scan_id} · {SCAN_TRIGGER_LABELS[report.trigger] ?? report.trigger} · 根「{report.root_alias}」
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
        <Account label="处理文件" value={`${report.files_considered} 篇`} />
        <Account label="产出候选" value={`${report.candidates_created} 条`} accent={report.candidates_created > 0} />
        <Account
          label="读取次数"
          value={limits === null ? `${report.read_calls}` : `${report.read_calls}/${limits.max_read_calls}`}
        />
        <Account label="读取字符" value={`${report.chars_read.toLocaleString()}`} />
      </dl>

      {report.candidates_created > 0 && (
        <Link
          href="/proposals"
          className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-white/75 underline-offset-4 hover:text-white hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
        >
          去提案页裁定这 {report.candidates_created} 条候选
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      )}

      {report.unread_gaps.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-amber-200/80">缺口 · 这次没做完的</h3>
          <ul className="mt-2 space-y-1 border-l border-amber-300/30 pl-3">
            {report.unread_gaps.map((gap, index) => (
              <li key={`${index}-${gap.slice(0, 12)}`} className="text-[12px] leading-5 text-amber-100/80">
                {gap}
              </li>
            ))}
          </ul>
        </div>
      )}

      {handledFiles.length > 0 && (
        <details className="group mt-5">
          <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[12px] text-white/55 hover:text-white">
            <ChevronDown className="size-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
            逐篇下场（{handledFiles.length} 篇处理完{report.files.length !== handledFiles.length ? `，共 ${report.files.length} 篇进入扫描` : ""}）
          </summary>
          <ul className="mt-2.5 space-y-1.5 border-l border-white/[0.1] pl-3">
            {report.files.map((file) => (
              <li key={file.relative_path} className="text-[12px] leading-5">
                <span className="font-mono text-[11.5px] text-white/70">{file.relative_path}</span>
                <span className={`ml-2 ${file.status === "failed" ? "text-red-200/80" : file.status === "partial" ? "text-amber-200/80" : "text-green/85"}`}>
                  {FILE_OUTCOME_LABELS[file.status] ?? file.status}
                </span>
                {file.candidates > 0 && <span className="ml-1 text-white/45">· {file.candidates} 条候选</span>}
                {file.error && <span className="mt-0.5 block text-white/45">{file.error}</span>}
                {file.dropped.map((drop, index) => (
                  <span key={`${index}-${drop.why.slice(0, 10)}`} className="mt-0.5 block text-white/40">
                    拦下一张卡：{drop.why}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function Metric({ label, value, accent = "white" }: { label: string; value: string; accent?: "white" | "amber" }) {
  const color = accent === "amber" ? "text-amber-200" : "text-white";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.16em] text-white/50">{label}</div>
      <div className={`mt-0.5 text-lg font-medium tracking-tight tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function Account({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] text-white/45">{label}</dt>
      <dd className={`mt-0.5 font-mono text-[15px] tabular-nums ${accent ? "text-green" : "text-white/85"}`}>{value}</dd>
    </div>
  );
}

/** 一次扫描用掉的读取配额：过顶变琥珀色，提醒「这次读不完了是正常的」。 */
function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px] text-white/45">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-white/70">
          {value.toLocaleString()}
          <span className="text-white/35"> / {max.toLocaleString()}</span>
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.08]">
        <div
          className={`h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none ${pct >= 100 ? "bg-amber-300/80" : "bg-white/45"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function RailBlock({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="text-sm font-medium text-white/85">{title}</h3>
      <p className="mb-3 mt-0.5 text-[11px] leading-5 text-white/40">{hint}</p>
      {children}
    </section>
  );
}
