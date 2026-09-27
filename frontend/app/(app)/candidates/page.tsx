"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, ChevronRight, Compass, Sparkles, X } from "lucide-react";
import { EASE_OUT, EASE_OUT_CSS, SPRING_PANEL } from "@/lib/ease";
import { AgentProgress } from "@/components/agents/loading-states/agent-progress";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import {
  ApiError,
  createPlan,
  findCandidates,
  generateBlueprint,
  getPlanChat,
  getProfile,
  listCandidates,
  listPlans,
  PROFILE_CATEGORIES,
  sayPlanChat,
  verdictCandidate,
  type CandidateList,
  type FindResult,
  type PlanSummary,
  type ProfileView,
  type PlanChatView,
} from "@/lib/api";

import { PromptInput } from "@/components/candidates/prompt-input";
import { CandidateCard } from "@/components/candidates/candidate-card";
import { ChatBox } from "@/components/candidates/chat-box";
import {
  getFindSession,
  patchFindSession,
  pushFindRound,
  type FindRound,
} from "@/components/candidates/find-session";

function rowsFromFind(found: FindResult) {
  return found.candidates.map((item, index) => ({
    id: found.candidate_ids[index],
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.title === found.recommended_start,
    status: "proposed",
    rejectReason: null,
    basis: item.profile_item_ids,
    planId: found.plan_id,
    landingPlanId: null,
    shape: found.shape,
    steps: found.steps,
  }));
}

function rowsFromStored(stored: CandidateList) {
  return stored.candidates.map((item) => ({
    id: item.id,
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.is_recommended === 1,
    status: item.status,
    rejectReason: item.reject_reason,
    basis: null,
    planId: item.plan_id,
    landingPlanId: item.landing_plan_id,
    shape: item.shape,
    steps: item.steps,
  }));
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/**
 * 桌面/移动右栏只挂载一份（阶段 5）：此前两份 ChatBox 同时在 DOM 里，
 * 隐藏的那份照样参与交互——重复输入框、焦点窜位。断点与样式里的 lg 对齐（1024px）。
 */
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = () => setIsDesktop(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return isDesktop;
}

/** 档案类别令牌 → 中文名（认不出的令牌原样摆出来，不编）。 */
function profileCategoryLabel(category: string): string {
  return (PROFILE_CATEGORIES as Record<string, string>)[category] ?? category;
}

const STATUS_LABELS: Record<string, string> = {
  accepted: "已采纳",
  rejected: "已否决",
  proposed: "待裁定",
  expired: "已过期",
};

/** 左列压缩态：对话面板展开时，候选清单收成一组紧凑按钮（自设计，无参考）。
 *  每颗都可点：已采纳的进它的规划对话；未采纳的弹出详情卡（依据 + 裁定）；
 *  点正在聊的那颗 = 收起对话回到全清单。 */
function CandidateRail({
  rows,
  activeId,
  onPick,
  onClose,
}: {
  rows: { id: number; title: string; status: string }[];
  activeId: number | null;
  onPick: (id: number) => void;
  onClose: (() => void) | null;
}) {
  return (
    <motion.nav
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
      aria-label="候选清单（压缩视图）"
      className="sticky top-24 flex flex-col max-h-[calc(100vh-8rem)]"
    >
      <div className="flex items-center justify-between pl-1 mb-3 shrink-0">
        <span className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35]">
          候选 · {rows.length}
        </span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="收起对话，回到候选清单"
            title="收起对话"
            className="grid size-7 place-items-center rounded-full text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
      <div className="rounded-[20px] border border-white/[0.05] bg-surface2/20 p-2 flex flex-col min-h-0 flex-1">
        <div className="flex flex-col gap-1 overflow-y-auto min-h-0">
          {rows.length === 0 && (
            <p className="px-2 py-1 text-[12px] leading-relaxed text-white/50">
              暂无候选。
            </p>
          )}
          {rows.map((row) => {
            const active = row.id === activeId;
            const dotClass =
              row.status === "accepted"
                ? "bg-green"
                : row.status === "rejected"
                  ? "bg-red-500"
                  : row.status === "expired"
                    ? "bg-white/30"
                    : "bg-amber-500";
            return (
              <button
                key={row.id}
                type="button"
                onClick={() => onPick(row.id)}
                title={`${STATUS_LABELS[row.status] ?? row.status} · ${row.title}`}
                className={`group relative flex w-full items-center gap-2.5 rounded-xl px-3 h-10 text-left transition-colors ${
                  active
                    ? "bg-white/[0.08] text-primary"
                    : "text-white/65 hover:bg-white/[0.04] hover:text-primary/90"
                }`}
              >
                {active && (
                  <span aria-hidden="true" className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-white/70" />
                )}
                <span aria-hidden="true" className={`size-2 rounded-full shrink-0 ${dotClass}`} />
                <span className="truncate text-[13px] font-medium flex-1">{row.title}</span>
                {active ? (
                  <span aria-hidden="true" className="size-1.5 rounded-full bg-white/70 shrink-0" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 shrink-0 opacity-0 group-hover:opacity-60 transition-opacity" />
                )}
              </button>
            );
          })}
        </div>
        <div className="shrink-0 mt-1 px-2 pt-2 pb-1 border-t border-white/[0.04] text-[11px] leading-relaxed text-white/50">
          点候选看详情或进对话
        </div>
      </div>
    </motion.nav>
  );
}

export default function CandidatesPage() {
  // 「找」的进行时状态以模块级会话为准（切页不丢，见 find-session.ts）；
  // 这里只是它的镜像，挂载时取一次初值，之后每次变更双写。
  const session = getFindSession();
  const isDesktop = useIsDesktop();
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);

  const [planChoice, setPlanChoice] = useState("");
  const [rawText, setRawTextState] = useState(session.rawText);
  const [asking, setAskingState] = useState(session.asking);
  const [askError, setAskErrorState] = useState<string | null>(session.error);
  const [fresh, setFreshState] = useState<FindResult | null>(session.fresh);
  const [stored, setStoredState] = useState<CandidateList | null>(session.stored);
  const [history, setHistory] = useState<FindRound[]>(session.history);
  // 历史轮次默认收起，点标题展开——平时不占竖向空间。
  const [historyOpen, setHistoryOpen] = useState(false);

  const setRawText = (val: string) => {
    setRawTextState(val);
    patchFindSession({ rawText: val });
  };
  const setAsking = (val: boolean) => {
    setAskingState(val);
    patchFindSession({ asking: val });
  };
  const setAskError = (val: string | null) => {
    setAskErrorState(val);
    patchFindSession({ error: val });
  };
  const setFresh = (val: FindResult | null) => {
    setFreshState(val);
    patchFindSession({ fresh: val });
  };
  const setStored = (val: CandidateList | null) => {
    setStoredState(val);
    patchFindSession({ stored: val });
  };

  // 历史轮次回看：点了带 requestId 的历史条目后，把那一轮的候选摆回左栏。
  // 只在内存里（切页即退回当前结果），不进会话存档。
  const [historyView, setHistoryView] = useState<{
    roundIndex: number;
    requestId: number;
    list: CandidateList;
  } | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  // 「查看依据」折叠（推荐理由展开的明细）
  const [basisOpen, setBasisOpen] = useState(false);

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [adoptingId, setAdoptingId] = useState<number | null>(null);
  
  const [freshPlanId, setFreshPlanId] = useState<number | null>(null);
  const [landingPlans, setLandingPlans] = useState<Record<number, number>>({});
  const [decidedNow, setDecidedNow] = useState<Record<number, string>>({});
  const [chattingId, setChattingId] = useState<number | null>(null);
  // 手风琴单开：当前展开明细的候选 id（null = 全部收起，信息密度优先）。
  const [openCandidateId, setOpenCandidateId] = useState<number | null>(null);

  const [chatViews, setChatViews] = useState<Record<number, PlanChatView | null>>({});
  const [chatBusy, setChatBusy] = useState<Record<number, boolean>>({});
  const [chatChosenPlan, setChatChosenPlan] = useState<Record<number, string>>({});

  useEffect(() => {
    getProfile().then(setProfile).catch(() => setProfile(null));
    listPlans().then(setPlans).catch(() => setPlans([]));
    listCandidates().then(setStored).catch(() => setStored(null));
  }, []);

  async function openChat(id: number | null, explicitPlanId?: number) {
    if (id === null || chattingId === id) {
      setChattingId(null);
      return;
    }
    setPeekId(null);
    setChattingId(id);
    const r = rows?.find(row => row.id === id);
    const pId = explicitPlanId ?? landingPlans[id] ?? r?.landingPlanId ?? r?.planId ?? undefined;
    try {
      const view = await getPlanChat(id, pId);
      setChatViews(prev => ({ ...prev, [id]: view }));
    } catch (err) {
      console.error(err);
    }
  }

  async function ask(text: string, clarAns: string | null) {
    // 等待期间不清旧结果——最坏要等几分钟，界面不能变白（2026-09-26 走查）
    const previousClarify = fresh?.clarify ?? null;
    setAsking(true);
    setAskError(null);
    try {
      const found = await findCandidates(
        text,
        planChoice === "" ? undefined : Number(planChoice),
        clarAns
      );
      pushFindRound({
        requestText: text,
        clarifyAnswered:
          clarAns && previousClarify
            ? { question: previousClarify.question, answer: clarAns }
            : null,
        recommended: found.recommended_start ?? null,
        count: found.candidates.length,
        shape: found.shape,
        requestId: found.request_id,
      });
      setHistory([...getFindSession().history]);
      setFresh(found);
      setStored(null);
      setHistoryView(null);
      setHistoryError(null);
    } catch (cause) {
      setAskError(messageOf(cause, "请求候选清单失败，原因不明"));
    } finally {
      setAsking(false);
    }
  }

  async function openHistoryRound(index: number, requestId: number) {
    if (historyView?.roundIndex === index) {
      // 再点一次正在回看的那条 = 退回当前结果
      setHistoryView(null);
      setHistoryError(null);
      return;
    }
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const list = await listCandidates(requestId);
      setHistoryView({ roundIndex: index, requestId, list });
    } catch (cause) {
      setHistoryError(messageOf(cause, "回看历史轮次失败"));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function onAdoptIntoNewPlan(candidateId: number, title: string, goalStr: string) {
    const goal = goalStr.trim() || title;
    setVerdicting(true);
    setVerdictError(null);
    setFreshPlanId(null);
    let createdId: number | null = null;
    try {
      const created = await createPlan(goal);
      createdId = created.id;
      setFreshPlanId(created.id);
      setPlans(await listPlans());
    } catch (cause) {
      setVerdictError(messageOf(cause, "新建计划失败，原因不明"));
      setVerdicting(false);
      return;
    }
    await onVerdict(candidateId, true, undefined, createdId);
  }

  async function onVerdict(candidateId: number, accepted: boolean, reason?: string, explicitPlanId?: number) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accepted, reason, explicitPlanId);
      setNotes((prev) => ({
        ...prev,
        [candidateId]: accepted
          ? `已采纳：已在计划 #${done.plan_id ?? ""} 里建立阶段 #${done.node_id ?? ""}「${getRow(candidateId)?.title ?? ""}」`
          : "已否决：已进永久禁区，之后不会再推荐它",
      }));
      const landedPlanId = done.plan_id == null ? null : Number(done.plan_id);
      if (landedPlanId !== null) {
        setLandingPlans((prev) => ({ ...prev, [candidateId]: landedPlanId }));
      }
      setDecidedNow((prev) => ({
        ...prev,
        [candidateId]: accepted ? "accepted" : "rejected",
      }));
      setRejectingId(null);
      setAdoptingId(null);
      setStored(await listCandidates());
      // 正回看历史轮次时裁定过：把那一轮的快照也刷一遍，别让卡片停在旧状态。
      if (historyView !== null) {
        try {
          setHistoryView({ ...historyView, list: await listCandidates(historyView.requestId) });
        } catch {
          // 快照刷不动就留着旧的，不挡裁定主流程
        }
      }
      // 采纳成功即点亮右栏进入规划对话；显式传入本次回执的计划 id，避免依赖异步 state。
      if (accepted) {
        await openChat(candidateId, landedPlanId ?? undefined);
      }
    } catch (cause) {
      setVerdictError(messageOf(cause, "裁定候选失败"));
    } finally {
      setVerdicting(false);
    }
  }

  const listed = historyView
    ? rowsFromStored(historyView.list)
    : fresh
      ? rowsFromFind(fresh)
      : stored
        ? rowsFromStored(stored)
        : null;
  const rows = listed?.map((row) => 
    decidedNow[row.id] === undefined ? row : { ...row, status: decidedNow[row.id] }
  ) ?? null;
  
  function getRow(id: number) {
    return rows?.find(r => r.id === id);
  }

  const pendingCount = (rows ?? []).filter(r => r.status === "proposed").length;
  const isPath = rows !== null && rows.length > 0 && rows[0].shape === "path";

  // 右栏只在有话可聊时出现：聊某条候选，或「找」的追问待答（追问卡住在右栏）。
  const clarifyPending = fresh?.clarify != null;
  const panelOpen = chattingId !== null || clarifyPending;

  // 列宽动画：网格列全用 px 表述（1fr 之间不可插值），容器宽度靠 ResizeObserver 量。
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [gridWidth, setGridWidth] = useState(0);
  useEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      setGridWidth(entries[0].contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const railWidth = 300;
  // 对话一开（规划对话或找方向追问）就是主体：左列压成按钮列，右侧占满其余宽度。
  const gridColumns = !isDesktop
    ? undefined
    : gridWidth === 0
      ? panelOpen
        ? `${railWidth}px minmax(0,1fr)`
        : "minmax(0,1fr)"
      : panelOpen
        ? `${railWidth}px ${Math.max(gridWidth - railWidth, 0)}px`
        : `${gridWidth}px 0px`;

  // rail 里点未采纳的候选 → 在对话主体上弹出详情卡（看依据、可裁定），再点一下收起。
  const [peekId, setPeekId] = useState<number | null>(null);
  const peekRow = peekId !== null ? rows?.find((r) => r.id === peekId) ?? null : null;

  async function handleChatSend(candidateId: number, message: string, effPlanId: number) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    try {
      await sayPlanChat(candidateId, message, effPlanId);
      const view = await getPlanChat(candidateId, effPlanId);
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
      return view;
    } catch (err) {
      console.error(err);
      setVerdictError(messageOf(err, "规划对话请求失败"));
      return null;
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  async function handleChatGenerate(candidateId: number, effPlanId: number) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    try {
      await generateBlueprint(candidateId, effPlanId);
      const view = await getPlanChat(candidateId, effPlanId);
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
    } catch (err) {
      setVerdictError(messageOf(err, "生成蓝图方案失败"));
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  function renderRightPanel() {
    if (chattingId === null) {
      // 「找方向」对话：原始请求与每轮追问/回答留痕，回答走底部输入框（一次追问 = 一次发信息）。
      return (
        <ChatBox
          view={null}
          effectivePlanId={null}
          plans={plans}
          chosenPlan={""}
          setChosenPlan={() => {}}
          onSend={async () => null}
          onGenerate={async () => {}}
          busy={asking}
          clarifyData={fresh?.clarify}
          onClarifySend={(answer) => { void ask(rawText, answer); }}
          clarifyError={askError}
          findRequest={history.length > 0 ? history[history.length - 1].requestText : rawText}
          findQa={[...history].reverse().flatMap((round) => round.clarifyAnswered ?? [])}
        />
      );
    }
    const id = chattingId;
    const r = getRow(id);
    const effPlanId = r ? (landingPlans[r.id] ?? r.landingPlanId ?? r.planId) : null;
    return (
      <ChatBox
        view={chatViews[id] ?? null}
        effectivePlanId={effPlanId}
        plans={plans}
        chosenPlan={chatChosenPlan[id] ?? ""}
        setChosenPlan={(val) => setChatChosenPlan(prev => ({ ...prev, [id]: val }))}
        onSend={(msg) => handleChatSend(id, msg, effPlanId!)}
        onGenerate={() => handleChatGenerate(id, effPlanId!)}
        busy={chatBusy[id] ?? false}
      />
    );
  }

  return (
    <div className="flex flex-col h-full bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background min-h-screen pt-20 pb-24">
      
      {/* Full width Prompt Input Row */}
      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12 mb-10 mt-4">
        <PromptInput 
          plans={plans}
          planChoice={planChoice}
          setPlanChoice={setPlanChoice}
          rawText={rawText}
          setRawText={setRawText}
          onSubmit={(e) => { e.preventDefault(); ask(rawText, null); }}
          asking={asking}
          bannedCount={fresh?.banned_titles?.length ?? 0}
        />
      </div>

      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12">
        <div
          ref={gridRef}
          className="grid grid-cols-1 items-start"
          style={{
            gridTemplateColumns: gridColumns,
            transition: isDesktop ? `grid-template-columns 500ms ${EASE_OUT_CSS}` : undefined,
          }}
        >

        {/* Left Column (Main) —— 对话一开就压缩成按钮列；按钮全可点（见 CandidateRail 说明） */}
        <div className="flex flex-col w-full min-w-0">
          {isDesktop && panelOpen ? (
            <>
              {(askError || historyError || verdictError) && (
                <div className="flex flex-col gap-2 mb-5 px-1">
                  {askError && <p className="text-[12px] leading-relaxed text-red-400">{askError}</p>}
                  {historyError && <p className="text-[12px] leading-relaxed text-red-400">{historyError}</p>}
                  {verdictError && <p className="text-[12px] leading-relaxed text-red-400">裁定失败：{verdictError}</p>}
                </div>
              )}
              <CandidateRail
                rows={rows ?? []}
                activeId={chattingId}
                onPick={(id) => {
                  const picked = rows?.find((r) => r.id === id);
                  if (picked?.status === "accepted") void openChat(id);
                  else setPeekId((prev) => (prev === id ? null : id));
                }}
                onClose={chattingId !== null ? () => setChattingId(null) : null}
              />
            </>
          ) : (
          <>
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-4">
              <h1 className="text-[22px] font-semibold text-primary/90 tracking-tight">候选清单</h1>
              {profile !== null && (
                <span className="px-3 py-1 bg-white/[0.03] border border-white/[0.06] rounded-full text-[11px] font-medium text-white/60 shadow-sm shrink-0">
                  长期档案 {profile.items.length} 条
                </span>
              )}
            </div>
          </div>

          {askError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
              {askError}
            </div>
          )}

          {historyLoading && (
            <div className="mb-6 text-[12px] text-white/50">正在取回那一轮的候选…</div>
          )}

          {historyError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
              {historyError}
            </div>
          )}

          {verdictError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
              <strong>裁定失败：</strong>{verdictError}
              {freshPlanId !== null && (
                <div className="mt-1 text-red-400/80">
                  新计划已经建好了 (计划 #{freshPlanId})，落点已选中它，再点一次「确认归入」即可。
                </div>
              )}
            </div>
          )}

          {asking && (
            <div className="rounded-2xl border border-white/[0.06] bg-[#141416]/90 px-4 py-3 mb-6 flex items-center gap-3 shadow-[0_8px_32px_rgba(0,0,0,0.35)]">
              <AgentProgress label="think" />
              <ThinkingShimmer className="text-[13px] text-muted/60">
                正在找候选，通常 20–30 秒，最坏几分钟；上一轮结果与已答的追问都还在。
              </ThinkingShimmer>
            </div>
          )}

          {history.length > 0 && (
            <div className="mb-8">
              <button
                type="button"
                onClick={() => setHistoryOpen((v) => !v)}
                aria-expanded={historyOpen}
                className="flex items-center gap-2 text-[11px] uppercase tracking-widest font-semibold text-white/[0.35] transition-colors hover:text-white/60"
              >
                <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${historyOpen ? "rotate-180" : ""}`} />
                历史轮次（新在上）
                <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium tracking-normal text-white/50">
                  {history.length} 轮
                </span>
              </button>
              {historyOpen && (
              <div className="mt-3 flex flex-col gap-2">
              {history.map((round, index) => {
                const requestId = round.requestId;
                const canReopen = typeof requestId === "number";
                const active = historyView?.roundIndex === index;
                const roundNo = history.length - index;
                const entryClass = `text-[12px] leading-relaxed border-l-2 pl-3 text-left w-full ${
                  active ? "border-white/40 text-white/70" : "border-white/[0.08] text-white/50"
                } ${canReopen && !active ? "cursor-pointer hover:text-white/70 hover:border-white/30 transition-colors" : ""}`;
                const body = (
                  <>
                    第 {roundNo} 轮 ·〈
                    {round.requestText.slice(0, 36)}
                    {round.requestText.length > 36 ? "…" : ""}〉
                    {round.clarifyAnswered && (
                      <>
                        {" "}
                        · 追问「{round.clarifyAnswered.question.slice(0, 22)}
                        {round.clarifyAnswered.question.length > 22 ? "…" : ""}」答：
                        {round.clarifyAnswered.answer.slice(0, 16)}
                        {round.clarifyAnswered.answer.length > 16 ? "…" : ""}
                      </>
                    )}
                    {round.recommended && (
                      <>
                        {" "}
                        · 推荐「{round.recommended.slice(0, 18)}
                        {round.recommended.length > 18 ? "…" : ""}」（{round.count} 条候选）
                      </>
                    )}
                    {canReopen && (
                      <span className="text-white/50">
                        {active ? " · 正在回看，再点一次返回" : " · 点击回看这一轮"}
                      </span>
                    )}
                  </>
                );
                return canReopen ? (
                  <button
                    key={index}
                    type="button"
                    onClick={() => {
                      if (typeof requestId === "number") openHistoryRound(index, requestId);
                    }}
                    disabled={historyLoading}
                    className={entryClass}
                  >
                    {body}
                  </button>
                ) : (
                  <div key={index} className={entryClass}>
                    {body}
                  </div>
                );
              })}
              </div>
              )}
            </div>
          )}

          {rows && rows.length > 0 ? (
            <div className="mt-2">
              <div className="flex items-end justify-between mb-5">
                <div className="flex items-center gap-3">
                  <h2 className="text-[14px] font-medium text-primary/80">
                    {isPath
                      ? `一条完整路径（含 ${rows[0]?.steps.length ?? 0} 个步骤）`
                      : `推荐候选（${rows.length} 条，待裁定 ${pendingCount} 条）`}
                  </h2>
                  {historyView && (
                    <span className="px-2.5 py-1 bg-white/[0.04] border border-white/[0.06] rounded-full text-[11px] font-medium text-white/60 shadow-sm shrink-0">
                      历史第 {history.length - historyView.roundIndex} 轮
                    </span>
                  )}
                </div>
                <span className="text-[11px] text-white/60">
                  {isPath ? "整条采纳或否决，去留在生成蓝图时调整" : "采纳将新建阶段，否决将进永久禁区"}
                </span>
              </div>

              {/* 推荐依据（fresh 结果才有）：一句话理由 + 可展开的明细；后端没给的字段不渲染对应行 */}
              {fresh && !historyView && (
                <div className="mb-4">
                  <div className="flex items-center gap-3 flex-wrap">
                    {fresh.start_reason && (
                      <span className="text-[12px] text-white/50 leading-relaxed">
                        推荐理由：{fresh.start_reason}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => setBasisOpen((prev) => !prev)}
                      className="shrink-0 text-[11px] text-white/50 hover:text-white/70 border border-white/[0.06] hover:border-white/[0.2] rounded-full px-2.5 py-1 transition-colors"
                    >
                      {basisOpen ? "收起依据" : "查看依据"}
                    </button>
                  </div>
                  <div
                    className="grid transition-all duration-200 ease-in-out"
                    style={{ gridTemplateRows: basisOpen ? "1fr" : "0fr" }}
                  >
                    <div className="overflow-hidden">
                      <div className="mt-3 border-l-2 border-white/[0.06] pl-3 flex flex-col gap-2 text-[12px] text-white/50 leading-relaxed">
                        {fresh.source && (
                          <div>
                            来源：{fresh.source.name}
                            （{fresh.source.networked ? "联网" : "不联网"}）
                          </div>
                        )}
                        {fresh.profile_basis && (
                          <div>
                            档案依据：{fresh.profile_basis.total} 条
                            {fresh.profile_basis.missing_categories &&
                              fresh.profile_basis.missing_categories.length > 0 && (
                                <>
                                  ，缺：
                                  {fresh.profile_basis.missing_categories
                                    .map((cat) => profileCategoryLabel(cat))
                                    .join("、")}
                                </>
                              )}
                          </div>
                        )}
                        {fresh.feedback_lines && fresh.feedback_lines.length > 0 && (
                          <div>
                            上一轮反馈：
                            <ul className="mt-1 space-y-1">
                              {fresh.feedback_lines.map((line, i) => (
                                <li key={i} className="border-l border-white/[0.06] pl-2">
                                  {line}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {fresh.banned_titles && fresh.banned_titles.length > 0 && (
                          <div>禁区标题：{fresh.banned_titles.join("、")}</div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              <div className="flex flex-col">
                {rows.map((row, index) => (
                  <CandidateCard
                    key={row.id}
                    row={row}
                    open={openCandidateId === row.id}
                    onToggle={() => setOpenCandidateId((prev) => (prev === row.id ? null : row.id))}
                    isFirst={index === 0}
                    isLast={index === rows.length - 1}
                    prevOpen={openCandidateId === rows[index - 1]?.id}
                    nextOpen={openCandidateId === rows[index + 1]?.id}
                    plans={plans}
                    notes={notes}
                    isDecided={row.status !== "proposed"}
                    verdicting={verdicting}
                    rejectingId={rejectingId}
                    setRejectingId={setRejectingId}
                    rejectReason={rejectReason}
                    setRejectReason={setRejectReason}
                    adoptingId={adoptingId}
                    setAdoptingId={setAdoptingId}
                    onVerdict={onVerdict}
                    onAdoptIntoNewPlan={onAdoptIntoNewPlan}
                    setChattingId={openChat}
                  />
                ))}
              </div>
            </div>
          ) : null}

          {!asking && !historyLoading && rows !== null && rows.length === 0 && (
            <div className="mt-2 rounded-[20px] border border-white/[0.06] bg-surface2/20 px-8 py-12 flex flex-col items-center gap-2 text-center">
              <Sparkles aria-hidden="true" className="w-5 h-5 text-white/25" />
              <p className="text-[13px] text-white/50">
                {clarifyPending ? "回答右侧的追问，这一轮会出候选。" : "这一轮没出候选，换个说法再来一轮。"}
              </p>
            </div>
          )}

          {!asking && rows === null && (
            <div className="mt-2 rounded-[20px] border border-white/[0.06] bg-surface2/20 px-8 py-12 flex flex-col items-center gap-2 text-center">
              <Compass aria-hidden="true" className="w-5 h-5 text-white/25" />
              <p className="text-[13px] text-white/50">
                还没有候选——在上面写下处境与困惑开始。
              </p>
            </div>
          )}
          </>
          )}
        </div>

        {/* Right Column —— 默认收起；一开就是主体（工作台式面板）。rail 里点未采纳的候选时，
            详情卡从左侧盖在对话上（不挤占对话主体），收起后回到对话。 */}
        {isDesktop ? (
          <div className="sticky top-24 h-[calc(100vh-8rem)] flex flex-col min-w-0 relative">
            <AnimatePresence initial={false}>
              {panelOpen && (
                <motion.div
                  key="candidates-chat-panel"
                  initial={{ opacity: 0, x: 32 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 32 }}
                  transition={SPRING_PANEL}
                  className="flex flex-col h-full min-h-0 pl-8"
                >
                  {renderRightPanel()}
                </motion.div>
              )}
            </AnimatePresence>

            <AnimatePresence initial={false}>
              {peekRow && (
                <motion.div
                  key="candidate-peek"
                  initial={{ opacity: 0, x: -24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -24 }}
                  transition={SPRING_PANEL}
                  className="absolute inset-y-0 left-0 z-20 flex w-[440px] max-w-[85%] flex-col rounded-[24px] border border-white/[0.06] bg-[#0c0c0e] shadow-[0_16px_48px_rgba(0,0,0,0.5)]"
                >
                  <div className="flex items-center justify-between px-5 pt-4 pb-2">
                    <span className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35]">
                      候选详情
                    </span>
                    <button
                      type="button"
                      onClick={() => setPeekId(null)}
                      aria-label="收起候选详情"
                      title="收起候选详情"
                      className="grid size-7 place-items-center rounded-full text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
                    <CandidateCard
                      row={peekRow}
                      open
                      onToggle={() => setPeekId(null)}
                      isFirst
                      isLast
                      prevOpen={false}
                      nextOpen={false}
                      plans={plans}
                      notes={notes}
                      isDecided={peekRow.status !== "proposed"}
                      verdicting={verdicting}
                      rejectingId={rejectingId}
                      setRejectingId={setRejectingId}
                      rejectReason={rejectReason}
                      setRejectReason={setRejectReason}
                      adoptingId={adoptingId}
                      setAdoptingId={setAdoptingId}
                      onVerdict={onVerdict}
                      onAdoptIntoNewPlan={onAdoptIntoNewPlan}
                      setChattingId={openChat}
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ) : panelOpen ? (
          <div className="mt-12 w-full h-[600px]">
            {renderRightPanel()}
          </div>
        ) : null}

        </div>
      </div>
    </div>
  );
}
