"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useSearchParams } from "next/navigation";
import {
  extractProfileProposals,
  getPlanDialogue,
  sayPlanDialogue,
  PlanDialogueView,
  ApiError,
} from "@/lib/api";
import { MessageScroller } from "@/components/agents/message-scroller";
import {
  AgentProgress,
} from "@/components/agents/loading-states/agent-progress";
import {
  ThinkingShimmer,
} from "@/components/agents/loading-states/thinking-shimmer";
import { Sparkles, Loader2, Check } from "lucide-react";
import Link from "next/link";
import {
  ActionSwapIcon,
  ActionSwapText,
} from "@/components/motion/action-swap";
import { ChatFlow } from "./chat-flow";
import { ChatInput } from "./chat-input";
import { PlanTreePanel } from "./plan-tree-panel";
import { ToastStack, type ToastData } from "@/components/ui/toast";
import { useWorkspace } from "@/components/shell/workspace-context";

// 工作台主区的对话视图：计划列表与顶栏由外壳提供，这里只管对话流。
// 取数、发送与状态都收在这里——ChatFlow 只管渲染，ChatInput 只管输入。
export function WorkbenchChat() {
  const { selectedPlanId } = useWorkspace();
  const [dialogue, setDialogue] = useState<PlanDialogueView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 一轮正在跑（还没拿到回执）：显示乐观的用户气泡 + 思考中的加载态
  const [pending, setPending] = useState(false);
  const [optimistic, setOptimistic] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // 这一轮刚到的回话要做分段出现（记它的行号；刷新/裁定后的重取不会触发）
  const [animateId, setAnimateId] = useState<number | null>(null);
  // 当前画面对应的计划：在途回执落定时对不上号就丢弃，防止旧计划的结果落进新计划画面
  const planIdRef = useRef<number | null>(null);
  // 档案提炼：跑动中禁用入口；结果/错误各给一行短回执
  const [extracting, setExtracting] = useState(false);
  const [extractNote, setExtractNote] = useState<{
    count: number;
    error: string | null;
  } | null>(null);
  // 右栏计划树的版本号：对话每刷新一次（换计划、建议被裁定）就重取一次树
  const [treeVersion, setTreeVersion] = useState(0);
  // P3 复盘回流：从 /report 的复盘卡跳过来时 URL 带 report_id——下一句对话带上它
  // （报告原文＋复盘卡进本轮上下文），发过一次就消费掉，不反复粘。
  // 用 useSearchParams 反应式读取：直接进页与 SPA 点链接两种导航都能拿到。
  // URL 参数只是「入口」，状态可被消费（发完归 null），所以用「记住上一版参数、渲染期比对」
  // 的官方模式跟随——effect 里同步 setState 会被 lint 拦（同 chat-box 的渲染期重置口径）。
  const reportParam = useSearchParams().get("report_id");
  const parsedReportId =
    reportParam !== null && /^[1-9]\d*$/.test(reportParam) ? Number(reportParam) : null;
  const [pendingReportId, setPendingReportId] = useState<number | null>(parsedReportId);
  const [lastReportParam, setLastReportParam] = useState(reportParam);
  if (lastReportParam !== reportParam) {
    setLastReportParam(reportParam);
    setPendingReportId(parsedReportId);
  }

  // 操作回执走右下角 toast；错误例外——不许被动画带走，仍留在输入框上方
  const [toasts, setToasts] = useState<ToastData[]>([]);
  const dismissToast = useCallback(
    (id: number) => setToasts((current) => current.filter((toast) => toast.id !== id)),
    [],
  );
  const pushToast = useCallback((text: string, href?: string, hrefLabel?: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-3), { id, text, href, hrefLabel }]);
  }, []);

  // 计划树宽度：拖分割条调整（双击复位、方向键微调、记忆在本地）。
  // 没拖过就是默认宽；对话内容撑满左栏，树紧贴分割条，中间不留空隙。
  const zoneRef = useRef<HTMLDivElement>(null);
  const panelWRef = useRef<number | null>(null);
  const splitDrag = useRef<{ startX: number; startW: number } | null>(null);
  const [panelWidth, setPanelWidth] = useState<number | null>(null);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("cadence-tree-split");
      if (saved) Promise.resolve().then(() => setPanelWidth(Number(saved)));
    } catch {
      // 读不到就用默认宽
    }
  }, []);

  const clampSplit = (w: number) => {
    const zoneW = zoneRef.current?.clientWidth ?? 0;
    return Math.round(Math.min(Math.max(w, 240), Math.max(300, zoneW * 0.6)));
  };

  const saveSplit = (w: number) => {
    try {
      window.localStorage.setItem("cadence-tree-split", String(w));
    } catch {
      // 存不下就算了，本次会话内仍有效
    }
  };

  const onSplitDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    splitDrag.current = { startX: e.clientX, startW: panelWidth ?? 400 };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onSplitMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!splitDrag.current) return;
    const w = clampSplit(splitDrag.current.startW + (splitDrag.current.startX - e.clientX));
    panelWRef.current = w;
    setPanelWidth(w);
  };
  const onSplitUp = () => {
    if (!splitDrag.current) return;
    splitDrag.current = null;
    if (panelWRef.current !== null) saveSplit(panelWRef.current);
  };
  const resetSplit = () => {
    splitDrag.current = null;
    panelWRef.current = null;
    setPanelWidth(null);
    try {
      window.localStorage.removeItem("cadence-tree-split");
    } catch {
      // 忽略
    }
  };
  const onSplitKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 48 : 16;
    const base = panelWidth ?? 400;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const w = clampSplit(e.key === "ArrowLeft" ? base + step : base - step);
    panelWRef.current = w;
    setPanelWidth(w);
    saveSplit(w);
    e.preventDefault();
  };

  const fetchDialogue = (planId: number) => {
    getPlanDialogue(planId)
      .then((data) => {
        if (planIdRef.current !== planId) return; // 已切走：旧计划的取数不上屏
        setDialogue(data);
        setError(null);
        setTreeVersion((v) => v + 1);
      })
      .catch((err) => {
        if (planIdRef.current !== planId) return;
        if (err instanceof ApiError) {
          setError(err.message);
        }
      })
      .finally(() => {
        if (planIdRef.current !== planId) return;
        setLoading(false);
      });
  };

  useEffect(() => {
    planIdRef.current = selectedPlanId;
    if (selectedPlanId) {
      fetchDialogue(selectedPlanId);
    }
    // 换计划：旧计划的对话、错误与上一轮的过渡态都不属于新画面（setState 走微任务，绕开同步闸）
    Promise.resolve().then(() => {
      setLoading(Boolean(selectedPlanId));
      setDialogue(null);
      setError(null);
      setAnimateId(null);
      setPending(false);
      setOptimistic(null);
      setExtracting(false);
      setExtractNote(null);
    });
  }, [selectedPlanId]);

  // 发送一句（输入框与问答卡共用这一条路）：乐观上屏 → 等回执 → 重取并分段放出回话。
  // silent（问答卡回答）不发乐观气泡——回答不是用户想说的话，卡片自己收起展示结果。
  // 返回是否送达：失败时卡片重开让用户重试；普通输入失败仍把原话还回输入框。
  // 在途期间切换计划的话，这轮的回执落定后整个丢弃：不覆盖新画面、不把原话还进新计划的输入框。
  const send = useCallback(
    (text: string, opts?: { silent?: boolean }): Promise<boolean> => {
      const content = text.trim();
      const planId = selectedPlanId;
      if (!planId || !content || pending) return Promise.resolve(false);
      setPending(true);
      if (!opts?.silent) setOptimistic(content);
      setDraft("");
      setError(null);
      const reportForThisTurn = pendingReportId;
      return sayPlanDialogue(planId, content, reportForThisTurn)
        .then(() => {
          // 报告上下文只服务一轮：送达即消费，失败则留着让用户原样重试
          if (reportForThisTurn !== null) setPendingReportId(null);
          return getPlanDialogue(planId);
        })
        .then((data) => {
          if (planIdRef.current !== planId) return true;
          setDialogue(data);
          const last = [...data.messages]
            .reverse()
            .find((message) => message.role === "assistant");
          if (last) setAnimateId(last.id);
          return true;
        })
        .catch((err) => {
          if (planIdRef.current !== planId) return false;
          setError(err instanceof ApiError ? err.message : "发送失败");
          if (!opts?.silent) setDraft(content);
          return false;
        })
        .finally(() => {
          // 切走后不清思考态——那可能是新计划正在跑的下一轮
          if (planIdRef.current !== planId) return;
          setPending(false);
          setOptimistic(null);
        });
    },
    [selectedPlanId, pending, pendingReportId],
  );

  // 把这段对话提炼成待裁定的档案变更提案（只生成提案，不改档案）。
  const extractProfile = useCallback(() => {
    const planId = selectedPlanId;
    if (!planId || extracting) return;
    setExtracting(true);
    setExtractNote(null);
    extractProfileProposals(planId)
      .then((data) => {
        if (planIdRef.current !== planId) return;
        if (data.items.length > 0) {
          pushToast(
            `已生成 ${data.items.length} 条档案变更提案`,
            "/proposals",
            "去待裁定提案页裁定",
          );
        } else {
          pushToast("这轮对话没有需要同步档案的变化");
        }
      })
      .catch((err) => {
        if (planIdRef.current !== planId) return;
        setExtractNote({
          count: 0,
          error: err instanceof ApiError ? err.message : "提炼失败",
        });
      })
      .finally(() => {
        if (planIdRef.current !== planId) return;
        setExtracting(false);
      });
  }, [selectedPlanId, extracting, pushToast]);

  // 提炼按钮的三态：空闲 → 提炼中（禁用）→ 已生成提案；没提炼出东西就回到空闲。
  const extractState = extracting
    ? "busy"
    : extractNote && !extractNote.error && extractNote.count > 0
    ? "done"
    : "idle";

  return (
    <div ref={zoneRef} className="flex min-h-0 w-full flex-1">
      <div className="relative flex min-w-0 flex-1 flex-col">
      <div className="flex-1 min-h-0 w-full px-4 md:px-10 flex flex-col">
        <MessageScroller
          navigation="rail"
          navigationLabel="跳到某一句"
          label="计划对话"
          busy={pending}
          className="flex-1 min-h-0"
          viewportClassName="pt-24"
          contentClassName="flex flex-col gap-8 pb-40"
        >
          {error ? (
            <div className="p-8 text-red-300 bg-red-400/10 rounded-lg text-sm">{error}</div>
          ) : loading ? (
            <div className="p-8 text-muted/75 text-sm flex items-center gap-2">
              <span className="w-4 h-4 rounded-full border-2 border-muted/30 border-t-muted animate-spin"></span>
              加载数据中...
            </div>
          ) : !selectedPlanId ? (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-8 text-center text-sm text-muted/75">
              <p>还没有可用的计划。</p>
              <Link
                href="/candidates"
                className="rounded-full border border-white/[0.14] px-4 py-2 text-xs text-primary transition-colors hover:bg-white/[0.08]"
              >
                去提出想法
              </Link>
            </div>
          ) : (
            <ChatFlow
              dialogue={dialogue}
              animateId={animateId}
              onRefresh={() => fetchDialogue(selectedPlanId)}
              onAnswer={send}
            />
          )}

          {pendingReportId !== null && (
            <div className="w-full max-w-[92%] rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3">
              <p className="text-[13px] font-medium text-amber-200/90">
                复盘回流已就绪：报告 #{pendingReportId}
              </p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-white/60">
                你下一句对话会带上这份报告原文与它的复盘卡；只是闲聊不会产提案，明确要求调整才会。发过一次后自动解除。
              </p>
              <button
                type="button"
                onClick={() => setPendingReportId(null)}
                className="mt-2 text-[12px] text-white/55 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
              >
                不带报告，普通聊
              </button>
            </div>
          )}

          {optimistic && (
            <div className="flex justify-end w-full">
              <div className="max-w-[80%] bg-white rounded-full px-5 py-3 text-[15px] leading-relaxed text-black shadow-[0_4px_24px_rgba(255,255,255,0.07)]">
                {optimistic}
              </div>
            </div>
          )}

          {pending && (
            <div className="flex flex-col gap-3 w-full">
              <div className="flex flex-col gap-2.5">
                <AgentProgress label="think" />
                <ThinkingShimmer className="text-[13px] text-muted/60">
                  正在整理思路…
                </ThinkingShimmer>
              </div>
            </div>
          )}
        </MessageScroller>
      </div>

      {selectedPlanId && (
        <ChatInput
          value={draft}
          onChange={setDraft}
          onSend={send}
          sending={pending}
          toolbar={
            !loading && dialogue?.can_extract ? (
              <button
                onClick={extractProfile}
                disabled={extracting || pending}
                title="只生成待裁定提案，不直接改档案"
                className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[12px] text-primary/70 transition-colors hover:bg-white/[0.08] hover:text-primary/90 disabled:opacity-40"
              >
                <ActionSwapIcon value={extractState} className="h-3 w-3">
                  {extracting ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : extractState === "done" ? (
                    <Check className="h-3 w-3" />
                  ) : (
                    <Sparkles className="h-3 w-3" />
                  )}
                </ActionSwapIcon>
                <ActionSwapText value={extractState}>
                  {extracting
                    ? "提炼中…"
                    : extractState === "done"
                    ? "已生成提案"
                    : "提炼档案提案"}
                </ActionSwapText>
              </button>
            ) : undefined
          }
          note={
            extractNote?.error ? (
              <div aria-live="polite" className="text-[12px] text-red-300">
                {extractNote.error}
              </div>
            ) : undefined
          }
        />
      )}
      </div>

      {selectedPlanId && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整计划树宽度（双击复位）"
          aria-valuenow={panelWidth ?? 400}
          tabIndex={0}
          onPointerDown={onSplitDown}
          onPointerMove={onSplitMove}
          onPointerUp={onSplitUp}
          onDoubleClick={resetSplit}
          onKeyDown={onSplitKey}
          className="hidden w-1 shrink-0 cursor-col-resize bg-white/[0.04] transition-colors hover:bg-white/25 focus-visible:bg-white/25 focus-visible:outline-none lg:block"
        />
      )}

      {selectedPlanId && (
        <aside
          style={{ width: panelWidth ?? 400 }}
          className="hidden min-h-0 shrink-0 lg:flex"
        >
          <PlanTreePanel key={selectedPlanId} planId={selectedPlanId} version={treeVersion} />
        </aside>
      )}

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
