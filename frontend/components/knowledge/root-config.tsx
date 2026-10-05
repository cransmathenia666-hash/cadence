"use client";

// 知识库根目录的配置面（2026-10-05）：以前要先去改本机 .env 才能加一个库，现在在界面里加。
//
// 两条口径：
// ① **绝对路径只在这一处出现**。后端只对「界面里配的根」回路径，扫描账、文件清单与候选
//    出处一律只有别名与相对路径——这里显示它是为了让你确认自己选的是哪个目录。
// ② 环境变量 <CODE>CADENCE_KNOWLEDGE_ROOTS</CODE> 仍然认，但只在库里一条都没有时生效；
//    界面里配过就由界面说了算，并给一个「搬进来」把老配置收编（避免两处各说各话）。
//
// 目录选择不是让人敲路径：走「浏览本机目录」——后端只列子目录、不列文件、不递归，
// 每个目录都带一句「能不能当根」的理由；不合适也让你点进去看，只是选不了。

import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  ChevronLeft,
  CornerDownRight,
  FolderOpen,
  FolderTree,
  HardDrive,
  Loader2,
  Pencil,
  Plus,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import { ActionSwapButton } from "@/components/motion/action-swap";
import { StaggerList } from "@/components/motion/stagger-list";
import { EASE_OUT } from "@/lib/ease";
import {
  ApiError,
  browseKnowledge,
  deleteKnowledgeRoot,
  importEnvKnowledgeRoots,
  type KnowledgeBrowse,
  type KnowledgeRoot,
} from "@/lib/api";

/** 正在编辑的草稿：`id` 为空就是「加一个新的」。 */
export type RootDraft = {
  id: number | null;
  alias: string;
  path: string;
  enabled: boolean;
};

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 路径太长时从中间收：留着盘符与最后两级目录，中间省略——比单纯截尾好认。 */
function shortenPath(path: string): string {
  if (path.length <= 46) return path;
  const [head, ...rest] = path.split(/[\\/]/).filter(Boolean);
  const tail = rest.slice(-2);
  return `${head}${path.includes("\\") ? "\\" : "/"}…\\${tail.join("\\")}`;
}

// ---------- 左栏：根目录清单 ----------

export function KnowledgeRootsRail({
  roots,
  selectedRootId,
  locked = false,
  onSelect,
  onAdd,
  onEdit,
  onImported,
}: {
  roots: KnowledgeRoot[] | null;
  selectedRootId: number | null;
  /** 扫描进行中：不让切根，免得扫描结果和看的那一份对不上。 */
  locked?: boolean;
  onSelect: (root: KnowledgeRoot) => void;
  onAdd: () => void;
  onEdit: (root: KnowledgeRoot) => void;
  onImported: () => void;
}) {
  const [importing, setImporting] = useState(false);
  const [importNote, setImportNote] = useState<string | null>(null);

  const list = roots ?? [];
  const envCount = list.filter((root) => root.source === "env").length;
  const uiCount = list.length - envCount;

  async function onImport() {
    setImporting(true);
    setImportNote(null);
    try {
      const report = await importEnvKnowledgeRoots();
      const skipped = report.skipped.length > 0 ? `，跳过 ${report.skipped.length} 个（重名或目录不在）` : "";
      setImportNote(report.imported.length > 0 ? `搬进来 ${report.imported.length} 个${skipped}` : "没有可搬的");
      onImported();
    } catch (cause) {
      setImportNote(messageOf(cause, "搬的时候出了意外错误"));
    } finally {
      setImporting(false);
    }
  }

  return (
    <section>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium text-white/85">知识库</h3>
        <button
          type="button"
          onClick={onAdd}
          className="inline-flex items-center gap-1 rounded-full border border-white/[0.14] px-2.5 py-1 text-[11px] text-white/70 transition-colors hover:border-white/25 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
        >
          <Plus className="size-3" aria-hidden="true" />
          添加
        </button>
      </div>
      <p className="mb-3 mt-0.5 text-[11px] leading-5 text-white/40">
        {uiCount === 0 && envCount === 0
          ? "点「添加」选一个本机目录，扫描只读它。"
          : uiCount === 0
            ? "下面这些来自本机环境变量——可以就地搬进界面管理。"
            : "界面里配的库，扫描只读它们。"}
      </p>

      {list.length === 0 ? (
        <p className="rounded-lg border border-dashed border-white/[0.12] px-3 py-4 text-[11px] leading-5 text-white/40">
          还没有知识库。加一个：选个目录（比如你的笔记文件夹），扫描只读它。
        </p>
      ) : (
        <ul className="space-y-1">
          {list.map((root) => {
            const selectable = root.enabled && !locked;
            const selected = root.enabled && root.id === selectedRootId;
            return (
              <li key={`${root.source}-${root.id}`} className="relative">
                <button
                  type="button"
                  disabled={!selectable}
                  onClick={() => onSelect(root)}
                  aria-current={selected ? "true" : undefined}
                  className={`w-full rounded-lg px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed ${
                    root.source === "ui" ? "pr-10" : ""
                  } ${
                    selected
                      ? "bg-white/[0.05] text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]"
                      : selectable
                        ? "text-white/60 hover:bg-white/[0.02] hover:text-white/90"
                        : "text-white/35"
                  }`}
                >
                  <span className="flex items-baseline gap-2">
                    <span className="truncate text-[13px] font-medium">{root.alias}</span>
                    {root.source === "env" && (
                      <span className="shrink-0 rounded-full bg-white/[0.06] px-1.5 py-px text-[10px] text-white/45">
                        环境变量
                      </span>
                    )}
                    {!root.enabled && (
                      <span className="shrink-0 text-[10px] text-amber-200/70">已停用</span>
                    )}
                  </span>
                  <span
                    className="mt-0.5 block truncate font-mono text-[10.5px] leading-4 text-white/35"
                    title={root.path ?? undefined}
                  >
                    {root.path ? shortenPath(root.path) : root.error ?? "｜本机环境变量"}
                  </span>
                </button>
                {root.source === "ui" && (
                  <button
                    type="button"
                    onClick={() => onEdit(root)}
                    aria-label={`改「${root.alias}」`}
                    title="改名 / 换目录 / 停用"
                    className="absolute right-1.5 top-2.5 rounded-md p-1.5 text-white/35 transition-colors hover:bg-white/[0.06] hover:text-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                  >
                    <Pencil className="size-3.5" aria-hidden="true" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {envCount > 0 && uiCount === 0 && (
        <div className="mt-3 border-t border-white/[0.08] pt-3">
          <button
            type="button"
            onClick={() => void onImport()}
            disabled={importing}
            className="inline-flex items-center gap-1.5 text-[11px] text-white/60 underline-offset-4 transition-colors hover:text-white hover:underline disabled:cursor-not-allowed disabled:text-white/35 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
          >
            {importing ? (
              <Loader2 className="size-3 animate-spin" aria-hidden="true" />
            ) : (
              <CornerDownRight className="size-3" aria-hidden="true" />
            )}
            把环境变量里的 {envCount} 个根搬进界面
          </button>
          {importNote !== null && (
            <p className="mt-1.5 text-[10.5px] leading-4 text-white/45">{importNote}</p>
          )}
        </div>
      )}
    </section>
  );
}

// ---------- 主栏：添加 / 编辑 ----------

export function KnowledgeRootPanel({
  draft,
  busy,
  error,
  onChange,
  onSave,
  onCancel,
  onDeleted,
}: {
  draft: RootDraft;
  busy: boolean;
  error: string | null;
  onChange: (patch: Partial<RootDraft>) => void;
  onSave: () => void;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const [picking, setPicking] = useState(draft.path.trim() === "");
  const [manual, setManual] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const editing = draft.id !== null;
  const ready = draft.alias.trim().length > 0 && draft.path.trim().length > 0;

  async function onDelete() {
    if (draft.id === null) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deleteKnowledgeRoot(draft.id);
      onDeleted();
    } catch (cause) {
      setDeleteError(messageOf(cause, "删的时候出了意外错误"));
      setDeleteBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="space-y-8">
      <header className="border-b border-white/[0.14] pb-3">
        <h2 className="text-lg font-medium tracking-tight text-white">
          {editing ? `编辑「${draft.alias}」` : "添加知识库"}
        </h2>
        <p className="mt-1 max-w-[64ch] text-xs leading-5 text-white/50">
          选一个本机目录（笔记、资料都行）。扫描只读它里面的 Markdown 与纯文本，
          永不整体导入，也不会动里面任何一个文件。
        </p>
      </header>

      {(error !== null || deleteError !== null) && (
        <p
          role="alert"
          className="rounded-lg border border-amber-300/25 bg-amber-300/[0.07] px-3.5 py-2.5 text-[12.5px] leading-6 text-amber-100/90"
        >
          {error ?? deleteError}
        </p>
      )}

      <div className="space-y-6">
        <label className="block">
          <span className="block text-[12px] font-medium text-white/80">
            名字
            <span className="ml-2 text-[11px] font-normal text-white/40">
              显示在清单与候选出处上，不能与别的库重名
            </span>
          </span>
          <input
            value={draft.alias}
            onChange={(event) => onChange({ alias: event.target.value })}
            placeholder="比如：个人知识库"
            maxLength={40}
            className="mt-2 block w-full max-w-[420px] rounded-md border border-white/[0.14] bg-white/[0.03] px-3 py-2 text-[13px] text-white placeholder:text-white/30 transition-colors focus:border-white/30 focus:outline-none"
          />
        </label>

        <div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="text-[12px] font-medium text-white/80">目录</span>
            <button
              type="button"
              onClick={() => {
                setPicking(true);
                setManual(false);
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.14] px-2.5 py-1 text-[11px] text-white/70 transition-colors hover:border-white/25 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
            >
              <FolderTree className="size-3" aria-hidden="true" />
              浏览本机目录
            </button>
            <button
              type="button"
              onClick={() => {
                setManual(true);
                setPicking(false);
              }}
              className="text-[11px] text-white/45 underline-offset-4 transition-colors hover:text-white/80 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
            >
              直接填路径
            </button>
          </div>

          <div className="mt-2 max-w-[560px] rounded-md border border-white/[0.12] bg-white/[0.02] px-3 py-2">
            <span className="block break-all font-mono text-[12px] leading-5 text-white/85">
              {draft.path.trim() || "（还没选目录）"}
            </span>
          </div>

          {manual && (
            <input
              value={draft.path}
              onChange={(event) => onChange({ path: event.target.value })}
              placeholder="从盘符写起，例如 D:\笔记"
              spellCheck={false}
              className="mt-2 w-full max-w-[560px] rounded-md border border-white/[0.14] bg-white/[0.03] px-3 py-2 font-mono text-[12px] text-white placeholder:font-sans placeholder:text-white/30 transition-colors focus:border-white/30 focus:outline-none"
            />
          )}
        </div>

        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => onChange({ enabled: event.target.checked })}
            className="mt-0.5 size-4 shrink-0 accent-white"
          />
          <span className="text-[12px] leading-5 text-white/70">
            现在就让它可扫
            <span className="ml-2 text-[11px] text-white/40">不勾也能先存着，之后在左栏点一下再启用</span>
          </span>
        </label>
      </div>

      {picking && (
        <DirectoryPicker
          start={draft.path.trim() || undefined}
          onPick={(path) => {
            onChange({ path });
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-white/[0.1] pt-5">
        <ActionSwapButton
          items={[
            { id: "idle", label: editing ? "保存" : "添加这个知识库" },
            { id: "busy", label: "保存中", icon: <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> },
          ]}
          value={busy ? "busy" : "idle"}
          animation="roll"
          variant="primary"
          size="md"
          onClick={onSave}
          disabled={!ready || busy}
          className="rounded-md bg-white px-4 text-[13px] text-black hover:bg-white/90 disabled:cursor-not-allowed disabled:bg-white/30 disabled:text-black/50"
          title={ready ? undefined : "名字与目录都要有"}
        />
        <button
          type="button"
          onClick={onCancel}
          className="text-[12px] text-white/55 underline-offset-4 transition-colors hover:text-white hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
        >
          取消
        </button>
        {editing && (
          <span className="ml-auto">
            {confirming ? (
              <span className="inline-flex items-center gap-3">
                <span className="text-[11px] text-white/55">只删配置，笔记文件一个都不动——确定？</span>
                <button
                  type="button"
                  onClick={() => void onDelete()}
                  disabled={deleteBusy}
                  className="inline-flex items-center gap-1.5 text-[12px] text-rose-200/90 underline-offset-4 transition-colors hover:text-rose-100 hover:underline disabled:text-white/35"
                >
                  {deleteBusy ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
                  确认删除
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  className="text-[12px] text-white/45 underline-offset-4 transition-colors hover:text-white/80 hover:underline"
                >
                  算了
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirming(true)}
                className="inline-flex items-center gap-1.5 text-[12px] text-white/45 underline-offset-4 transition-colors hover:text-rose-200/90 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
                删除这个知识库
              </button>
            )}
          </span>
        )}
      </div>
    </section>
  );
}

// ---------- 目录选择器 ----------

function DirectoryPicker({
  start,
  onPick,
  onClose,
}: {
  start?: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<KnowledgeBrowse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  // 逐次点进去的竞态闸：慢回来的旧响应落不了视图（页面重做模板的老规矩）。
  const seq = useRef(0);

  function go(path?: string) {
    const current = ++seq.current;
    browseKnowledge(path)
      .then((data) => {
        if (seq.current !== current) return;
        setBusy(false);
        setError(null);
        setListing(data);
      })
      .catch((cause: unknown) => {
        if (seq.current !== current) return;
        setBusy(false);
        setError(messageOf(cause, "读这个目录时出了意外错误"));
      });
  }

  /** 事件里点着走：先把「正在读」亮起来，再让 `go` 去取。 */
  function navigate(path?: string) {
    setBusy(true);
    setError(null);
    go(path);
  }

  // 打开时先落在起点（没给就用盘符与用户主目录）。
  // 取数与 setState 都写在 promise 回调里：effect 里同步 setState 会被
  // react-hooks/set-state-in-effect 拦（初始 busy 已经在 useState 里点着了）。
  useEffect(() => {
    go(start);
    return () => {
      seq.current += 1; // 卸载后回来的响应一律作废
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const here = listing?.path ?? null;
  const blocked = listing?.problem ?? null;

  return (
    <motion.section
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: EASE_OUT }}
      className="rounded-xl border border-white/[0.12] bg-white/[0.02] p-4"
      aria-label="选择本机目录"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <FolderOpen className="size-4 shrink-0 text-white/55" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-white/75" title={here ?? undefined}>
          {here ?? "选择磁盘或目录"}
        </span>
        {listing?.parent && (
          <button
            type="button"
            onClick={() => navigate(listing.parent ?? undefined)}
            className="inline-flex shrink-0 items-center gap-1 rounded-full border border-white/[0.14] px-2.5 py-1 text-[11px] text-white/70 transition-colors hover:border-white/25 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
          >
            <ChevronLeft className="size-3" aria-hidden="true" />
            上一级
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 text-[11px] text-white/45 underline-offset-4 transition-colors hover:text-white/80 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
        >
          收起
        </button>
      </div>

      {blocked !== null && (
        <p className="mt-3 flex items-start gap-2 text-[11px] leading-5 text-amber-200/80">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {blocked}
        </p>
      )}

      <div className="mt-3 max-h-[320px] overflow-y-auto rounded-lg border border-white/[0.08]">
        {busy && listing === null ? (
          <p className="px-3 py-6 text-center text-[11px] text-white/40">正在读目录…</p>
        ) : error !== null ? (
          <p className="px-3 py-6 text-center text-[11px] text-white/50">{error}</p>
        ) : listing !== null && listing.entries.length === 0 ? (
          <p className="px-3 py-6 text-center text-[11px] text-white/40">
            这一层没有子目录。上级或「就选这个目录」。
          </p>
        ) : (
          <StaggerList step="18ms" className="divide-y divide-white/[0.05]">
            {(listing?.entries ?? []).map((entry) => (
              <li key={entry.path}>
                <button
                  type="button"
                  onClick={() => navigate(entry.path)}
                  className="flex w-full items-baseline gap-3 px-3 py-2 text-left transition-colors hover:bg-white/[0.03] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-white/70"
                >
                  {entry.path.length <= 3 ? (
                    <HardDrive className="size-3.5 shrink-0 self-center text-white/45" aria-hidden="true" />
                  ) : (
                    <FolderOpen className="size-3.5 shrink-0 self-center text-white/45" aria-hidden="true" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-white/80">{entry.name}</span>
                  {entry.problem !== null && (
                    <span className="shrink-0 truncate text-[10.5px] text-white/30">{entry.problem}</span>
                  )}
                </button>
              </li>
            ))}
          </StaggerList>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10.5px] leading-4 text-white/35">
          只列目录、不列文件；选中的目录只被读，不会被改。
        </span>
        <button
          type="button"
          disabled={here === null || blocked !== null || busy}
          onClick={() => {
            if (here !== null) onPick(here);
          }}
          className="inline-flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-[12px] text-black transition-colors hover:bg-white/90 disabled:cursor-not-allowed disabled:bg-white/25 disabled:text-black/45"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          就选这个目录
        </button>
      </div>
    </motion.section>
  );
}
