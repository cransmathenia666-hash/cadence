"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, ChevronRight, Compass, Sparkles } from "lucide-react";
import { EASE_OUT, EASE_OUT_CSS, SPRING_PANEL } from "@/lib/ease";
import { AgentProgress } from "@/components/agents/loading-states/agent-progress";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import {
  ApiError,
  findCandidates,
  generateBlueprint,
  getPlanChat,
  getProfile,
  keepFindShape,
  listCandidates,
  listLearningRequests,
  listPlans,
  PROFILE_CATEGORIES,
  reopenCandidatePlanning,
  returnBlueprintToPlanning,
  sayPlanChat,
  verdictCandidate,
  type CandidateList,
  type BlueprintMode,
  type FindResult,
  type LearningRequestHistory,
  type PlanSummary,
  type ProfileView,
  type PlanChatView,
  type PathStep,
} from "@/lib/api";

import { PromptInput } from "@/components/candidates/prompt-input";
import { CandidateCard } from "@/components/candidates/candidate-card";
import { ChatBox } from "@/components/candidates/chat-box";
import {
  CandidateResultCard,
  type CandidateActionResult,
} from "@/components/candidates/candidate-result-card";
import { useWorkspace } from "@/components/shell/workspace-context";
import {
  getFindSession,
  groupFindRounds,
  hydrateFindSession,
  patchFindSession,
  pushFindRound,
  setActiveFindChat,
  setActiveFindRequest,
  setActiveFindThread,
  subscribeFindSession,
  type FindRound,
  type PendingShapeChange,
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

function roundFromHistory(request: LearningRequestHistory): FindRound {
  return {
    requestText: request.utterance || request.raw_text,
    utterance: request.utterance,
    intent: request.intent,
    reply: request.reply,
    status: request.status,
    shapeDecision: request.shape_change?.decision ?? null,
    createdAt: request.created_at ?? null,
    clarifyAnswered: request.clarify?.answer
      ? { question: request.clarify.question, answer: request.clarify.answer }
      : null,
    clarify: request.clarify,
    recommended: null,
    count: request.candidate_count,
    shape: null,
    requestId: request.request_id,
    threadId: request.thread_id,
  };
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

/** 截断到 `max` 个字，超了加省略号（历史列表里一句话放不下）。 */
function clip(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

/** 「09-27 22:30」——历史条目上的时间到分钟就够。 */
function formatRoundTime(value: string): string {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

const STATUS_LABELS: Record<string, string> = {
  accepted: "已采纳",
  rejected: "已否决",
  proposed: "待裁定",
  expired: "已过期",
};

/** PC 结果目录：选择候选后右侧主区替换为评审席；路径结果附步骤索引。 */
function CandidateRail({
  rows,
  activeId,
  onPick,
}: {
  rows: { id: number; title: string; status: string; shape: string | null; steps: PathStep[] }[];
  activeId: number | null;
  onPick: (id: number) => void;
}) {
  return (
    <motion.nav
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
      aria-label={rows[0]?.shape === "path" ? "路径索引" : "候选目录"}
      className="sticky top-24 flex flex-col max-h-[calc(100vh-8rem)]"
    >
      <div className="mb-3 flex shrink-0 items-center justify-between pl-1">
        <span className="text-[12px] font-medium text-primary/70">
          {rows[0]?.shape === "path" ? "路径索引" : `候选目录 · ${rows.length}`}
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col border-t border-white/[0.12]">
        <div className="min-h-0 flex-1 overflow-y-auto">
          {rows.length === 0 && (
            <p className="px-2 py-1 text-[12px] leading-relaxed text-white/50">
              暂无候选。
            </p>
          )}
          {rows.map((row, index) => {
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
                aria-current={active ? "true" : undefined}
                className={`group relative flex w-full items-start gap-3 border-b border-white/[0.07] px-2 py-4 text-left transition-colors ${
                  active
                    ? "bg-white/[0.05] text-primary"
                    : "text-white/65 hover:bg-white/[0.04] hover:text-primary/90"
                }`}
              >
                {active && (
                  <span aria-hidden="true" className="absolute inset-y-0 left-0 w-px bg-white/70" />
                )}
                <span className="pt-0.5 font-mono text-[11px] tabular-nums text-white/35">{String(index + 1).padStart(2, "0")}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-medium leading-snug">{row.title}</span>
                  <span className="mt-1.5 flex items-center gap-2 text-[11px] text-white/40"><span aria-hidden="true" className={`size-1.5 rounded-full ${dotClass}`} />{row.shape === "path" ? `完整路径 · ${row.steps.length} 步` : STATUS_LABELS[row.status] ?? row.status}</span>
                </span>
                <ChevronRight className="mt-1 size-3.5 shrink-0 text-white/30 group-hover:text-white/70" aria-hidden="true" />
              </button>
            );
          })}
          {rows.length === 1 && rows[0].shape === "path" && rows[0].steps.length > 0 && (
            <ol className="px-2 py-5" aria-label="路径步骤概览">
              {rows[0].steps.map((step, index) => <li key={index} className="flex gap-3 border-l border-white/15 py-2 pl-3 text-[11px] text-white/50"><span className="font-mono tabular-nums text-white/30">{index + 1}</span><span>{step.title}</span></li>)}
            </ol>
          )}
        </div>
        <div className="shrink-0 border-t border-white/[0.08] px-2 py-3 text-[11px] leading-relaxed text-white/50">
          点选一项，在右侧评审
        </div>
      </div>
    </motion.nav>
  );
}

export default function CandidatesPage() {
  const router = useRouter();
  const { plans: workspacePlans, setSelectedPlanId, refreshPlans } = useWorkspace();
  // 「找」的进行时状态以模块级会话为准（切页不丢，见 find-session.ts）；
  // 这里只是它的镜像，挂载时取一次初值，之后每次变更双写。
  const session = getFindSession();
  const isDesktop = useIsDesktop();
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [plansLoading, setPlansLoading] = useState(true);
  const [plansError, setPlansError] = useState<string | null>(null);

  const [planChoice, setPlanChoice] = useState("");
  const [rawText, setRawTextState] = useState(session.rawText);
  const [asking, setAskingState] = useState(session.asking);
  const [askError, setAskErrorState] = useState<string | null>(session.error);
  const [fresh, setFreshState] = useState<FindResult | null>(session.fresh);
  const [stored, setStoredState] = useState<CandidateList | null>(session.stored);
  const [history, setHistory] = useState<FindRound[]>(session.history);
  const [activeRequestId, setActiveRequestIdState] = useState<number | null>(session.activeRequestId);
  // 当前探索线程：发起后记下响应 thread_id，续问 / 答追问都带上（决策 44 ①）。
  const [activeThreadId, setActiveThreadIdState] = useState<number | null>(session.activeThreadId);
  // 线程自己的计划归属（决策 44 ①，复核整改）：续问 / 答追问 / 重新推荐发它，
  // 不发计划选择器里的当前值——两者不一致时以线程为准，否则后端 409。
  const [activeThreadPlanId, setActiveThreadPlanIdState] = useState<number | null>(
    session.activeThreadPlanId,
  );
  const [pendingShapeChange, setPendingShapeChangeState] = useState<PendingShapeChange | null>(
    session.pendingShapeChange,
  );
  // 历史默认收起，点标题展开——平时不占竖向空间。
  const [historyOpen, setHistoryOpen] = useState(false);
  // 展开着的那几段对话（默认都收起，只把正在回看的那段自动摊开）。
  const [openThreads, setOpenThreads] = useState<string[]>([]);
  const toggleThread = (key: string) =>
    setOpenThreads((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );

  /**
   * 记下 / 清掉当前探索线程，连同它的计划归属（决策 44 ①，复核整改）。
   * `planId` 是线程头请求所属的计划（null = 「新方向」）；清线程时归属一并清空。
   */
  const applyActiveThread = (id: number | null, planId: number | null = null) => {
    setActiveThreadIdState(id);
    setActiveThreadPlanIdState(id === null ? null : planId);
    setActiveFindThread(id);
    patchFindSession({ activeThreadId: id, activeThreadPlanId: id === null ? null : planId });
  };

  const setRawText = (val: string) => {
    setRawTextState(val);
    if (val.trim() !== rawText.trim()) {
      setFreshState(null);
      setStoredState(null);
      setHistoryView(null);
      setFindResultNotice(null);
      setActiveRequestIdState(null);
      setActiveFindRequest(null);
      // 原话改了就是新问题：线程上下文（连同它的计划归属）与待确认的形态切换一并清掉。
      applyActiveThread(null);
      setPendingShapeChangeState(null);
      patchFindSession({
        fresh: null,
        stored: null,
        rawText: val,
        activeRequestId: null,
        activeThreadId: null,
        activeThreadPlanId: null,
        pendingShapeChange: null,
        activeChat: null,
      });
      setChattingId(null);
      setActiveFindChat(null);
    } else {
      patchFindSession({ rawText: val });
    }
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
  const setPendingShapeChange = (val: PendingShapeChange | null) => {
    setPendingShapeChangeState(val);
    patchFindSession({ pendingShapeChange: val });
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
  const historyLoadId = useRef(0);
  // 「查看依据」折叠（推荐理由展开的明细）
  const [basisOpen, setBasisOpen] = useState(false);

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [adoptingId, setAdoptingId] = useState<number | null>(null);

  const [landingPlans, setLandingPlans] = useState<Record<number, number>>({});
  // 规划会话号（OC-05）：采纳 / 重开回执给的，或从视图读回的——查看 / 发言 / 出蓝图一律按会话走。
  const [chatSessions, setChatSessions] = useState<Record<number, number>>({});
  const [decidedNow, setDecidedNow] = useState<Record<number, string>>({});
  const [chattingId, setChattingId] = useState<number | null>(null);
  // 手风琴单开：当前展开明细的候选 id（null = 全部收起，信息密度优先）。
  const [openCandidateId, setOpenCandidateId] = useState<number | null>(null);
  const [showFindConversation, setShowFindConversation] = useState(false);

  const [chatViews, setChatViews] = useState<Record<number, PlanChatView | null>>({});
  const [chatBusy, setChatBusy] = useState<Record<number, boolean>>({});
  // 首次打开规划对话的取数等待（与发言 / 出方案的 busy 分开记）。
  const [chatLoading, setChatLoading] = useState<Record<number, boolean>>({});
  // 规划栏（对话/生成蓝图）自己的错误通道：生成失败不说成「裁定失败」——两码事。
  const [planningError, setPlanningError] = useState<string | null>(null);
  const [chatChosenPlan, setChatChosenPlan] = useState<Record<number, string>>({});
  const [actionResults, setActionResults] = useState<CandidateActionResult[]>([]);
  const [findResultNotice, setFindResultNotice] = useState<{
    answer: string;
    count: number;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    const restored = hydrateFindSession();
    const interruptedThread = restored.pendingThreadId;
    if (restored.asking && interruptedThread === null) {
      patchFindSession({ asking: false });
    }
    // SPA 切页时在途请求仍归旧页面收尾；新页面订阅单例以接住其结果。
    const unsubscribe = subscribeFindSession(() => {
      if (!alive) return;
      const current = getFindSession();
      setAskingState(current.asking);
      setAskErrorState(current.error);
      setFreshState(current.fresh);
      setStoredState(current.stored);
      setHistory([...current.history]);
      setActiveRequestIdState(current.activeRequestId);
      setActiveThreadIdState(current.activeThreadId);
      setActiveThreadPlanIdState(current.activeThreadPlanId);
      setPendingShapeChangeState(current.pendingShapeChange);
    });
    // 线程上下文恢复：优先用存档里的 activeThreadId；存档没有（旧格式）就从
    // 历史行里对 activeRequestId 那轮的 thread_id 拿——不靠原话猜（决策 44 ①）。
    const restoredThread =
      restored.activeThreadId ??
      restored.history.find(
        (round) => round.requestId === restored.activeRequestId && round.threadId != null,
      )?.threadId ??
      null;
    // 线程的计划归属一并还原（复核整改）：优先存档值，没有就从 fresh 那轮的 plan_id 拿；
    // 恢复后计划选择器也指到它——续问要发线程自己的计划，发错会被后端 409 拒。
    const restoredThreadPlan =
      restored.activeThreadPlanId ??
      (restoredThread !== null ? restored.fresh?.plan_id ?? null : null);
    Promise.resolve().then(() => {
      if (!alive) return;
      setRawTextState(restored.rawText);
      setActiveRequestIdState(restored.activeRequestId);
      applyActiveThread(restoredThread, restoredThreadPlan);
      setAskingState(restored.asking && interruptedThread !== null);
      setAskErrorState(restored.error);
      setFreshState(restored.fresh);
      // 待确认的形态切换随存档回来（复核整改）：刷新前模型说明刚挂上、还没来得及拍板，
      // 刷新后说明与原话一起回来，确认动作不用重来。
      setPendingShapeChangeState(restored.pendingShapeChange);
      // 计划选择器以线程自己的归属为准（复核整改）：不一致时不是改线程，是把选择器拨回去。
      if (restoredThread !== null) {
        setPlanChoice(restoredThreadPlan === null ? "" : String(restoredThreadPlan));
      }
      setStoredState(restored.stored);
      setHistory([...restored.history]);
    });

    getProfile()
      .then((value) => {
        if (!alive) return;
        setProfile(value);
        setProfileError(null);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        setProfile(null);
        setProfileError(messageOf(cause, "读取长期档案失败"));
      })
      .finally(() => {
        if (alive) setProfileLoading(false);
      });
    listPlans()
      .then((value) => {
        if (!alive) return;
        setPlans(value);
        setPlansError(null);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        setPlans([]);
        setPlansError(messageOf(cause, "读取计划失败"));
      })
      .finally(() => {
        if (alive) setPlansLoading(false);
      });
    if (restoredThread !== null) {
      // 按线程取最近有候选的一轮：活跃轮本身没出候选（追问 / 闲聊）时旧候选照样回来。
      listCandidates(undefined, restoredThread).then(setStored).catch(() => setStored(null));
    } else if (restored.activeRequestId !== null) {
      listCandidates(restored.activeRequestId).then(setStored).catch(() => setStored(null));
    }
    const restoredChat = restored.activeChat;
    if (restoredChat !== null) {
      Promise.resolve().then(() => {
        if (!alive) return;
        setChattingId(restoredChat.candidateId);
        if (restoredChat.planningSessionId != null) {
          setChatSessions((previous) => ({
            ...previous,
            [restoredChat.candidateId]: restoredChat.planningSessionId as number,
          }));
        }
        setChatLoading((previous) => ({ ...previous, [restoredChat.candidateId]: true }));
      });
      getPlanChat({
        planningSessionId: restoredChat.planningSessionId ?? null,
        candidateId: restoredChat.candidateId,
        planId: restoredChat.planningSessionId == null ? restoredChat.planId : null,
      })
        .then((view) => {
          if (!alive) return;
          setChatViews((previous) => ({
            ...previous,
            [restoredChat.candidateId]: view,
          }));
          if (view.planning_session) {
            setChatSessions((previous) => ({
              ...previous,
              [restoredChat.candidateId]: view.planning_session!.id,
            }));
          }
        })
        .catch((cause: unknown) => {
          if (!alive) return;
          setPlanningError(messageOf(cause, "恢复规划对话失败"));
        })
        .finally(() => {
          if (alive) {
            setChatLoading((previous) => ({ ...previous, [restoredChat.candidateId]: false }));
          }
        });
    }
    let pendingTimer: number | null = null;
    const absorb = (requests: LearningRequestHistory[]) => {
      if (!alive || requests.length === 0) return;
      setHistory((current) => {
        const local = new Map(current.map((round) => [round.requestId, round]));
        const recovered = requests.map((request) => ({
          ...local.get(request.request_id),
          ...roundFromHistory(request),
        }));
        return [
          ...recovered,
          ...current.filter((round) => round.requestId == null || !requests.some((request) => request.request_id === round.requestId)),
        ].sort((a, b) => (b.requestId ?? -1) - (a.requestId ?? -1));
      });
      // 刷新把在途请求的 promise 丢了：按服务端状态收尾——那一轮已落定就把它的回复
      // 接回当前轮；还在跑（模型最坏要几分钟）就晚点再取一次，不做无限轮询。
      const pendingThread = getFindSession().pendingThreadId;
      if (pendingThread !== null) {
        const newest = requests.find(
          (request) => (request.thread_id ?? request.request_id) === pendingThread,
        );
        if (newest === undefined || newest.status !== "pending") {
          patchFindSession({ asking: false, pendingThreadId: null });
          if (newest !== undefined && (getFindSession().activeRequestId ?? 0) < newest.request_id) {
            setActiveRequestIdState(newest.request_id);
            setActiveFindRequest(newest.request_id);
          }
        } else if (pendingTimer === null) {
          // 还在跑（模型最坏几分钟）：每 5 秒问一次服务端，落定即停；页面卸载时清掉。
          pendingTimer = window.setTimeout(() => {
            pendingTimer = null;
            listLearningRequests()
              .then((again) => absorb(again))
              .catch(() => undefined);
          }, 5000);
        }
      }
      // 存档里连 activeThreadId 都没有时，从后端历史行补一次线程归属（连同计划归属）。
      if (getFindSession().activeThreadId == null && restored.activeRequestId != null) {
        const match = requests.find((request) => request.request_id === restored.activeRequestId);
        if (match?.thread_id != null) {
          applyActiveThread(match.thread_id, match.plan_id ?? null);
        }
      }
      // 计划归属以历史行为准再核一遍（复核整改）：比存档 fresh 更可靠——
      // 没出过候选的线程（只聊过天）fresh 可能是空的，历史行的 plan_id 仍在。
      // 线程头行（request_id 即线程号）的归属最可靠，找不到才退回段内任一行的归属。
      const currentThread = getFindSession().activeThreadId;
      if (currentThread != null) {
        const head =
          requests.find((request) => request.request_id === currentThread) ??
          requests.find((request) => request.thread_id === currentThread);
        if (head) {
          applyActiveThread(currentThread, head.plan_id ?? null);
          setPlanChoice(head.plan_id === null ? "" : String(head.plan_id));
        }
        // 待确认的形态切换也能从历史行恢复（复核整改）：会话存档里没有时（比如提案那轮
        // 之后还续问过、刷新把存档 fresh 顶掉了），取该线程最近一条带提案的行——
        // 它之后线程里没再出过候选才仍然有效（出过就是提案已被回答，失效），与后端判据一致。
        if (getFindSession().pendingShapeChange == null) {
          const threadRows = requests.filter(
            (request) => (request.thread_id ?? request.request_id) === currentThread,
          );
          const proposalAt = threadRows.findIndex((request) => request.shape_change?.decision === "pending");
          if (proposalAt >= 0) {
            const consumed = threadRows
              .slice(0, proposalAt)
              .some((request) => request.has_candidates);
            if (!consumed) {
              const proposal = threadRows[proposalAt].shape_change!;
              setPendingShapeChange({
                from: proposal.from ?? "原形态",
                to: proposal.to,
                reason: proposal.reason,
                text: threadRows[proposalAt].utterance,
                requestId: threadRows[proposalAt].request_id,
              });
            }
          }
        }
      }
    };
    listLearningRequests()
      .then((requests) => absorb(requests))
      .catch(() => undefined);
    return () => {
      alive = false;
      unsubscribe();
      if (pendingTimer !== null) window.clearTimeout(pendingTimer);
      historyLoadId.current += 1;
    };
  }, []);

  const [planningRetryCandidateId, setPlanningRetryCandidateId] = useState<number | null>(null);

  async function openChat(
    id: number | null,
    opts?: { planId?: number | null; planningSessionId?: number | null; retry?: boolean },
  ) {
    if (id === null || (chattingId === id && opts?.retry !== true)) {
      setChattingId(null);
      setActiveFindChat(null);
      return;
    }
    setPeekId(null);
    setShowFindConversation(true);
    setPlanningError(null);
    setPlanningRetryCandidateId(null);
    const r = rows?.find(row => row.id === id);
    // 会话优先（OC-05）：采纳 / 重开回执给过会话号就直接按会话看；没有就按候选看——
    // 进过规划的候选后端会自动改走它的会话（视图里带回 planning_session），旧候选走 legacy。
    const sessionId = opts?.planningSessionId ?? chatSessions[id] ?? null;
    const pId = opts?.planId ?? landingPlans[id] ?? r?.landingPlanId ?? r?.planId ?? null;
    const nextChat = { candidateId: id, planId: pId, planningSessionId: sessionId };
    setChattingId(id);
    setActiveFindChat(nextChat);
    setChatLoading(prev => ({ ...prev, [id]: true }));
    try {
      const view = await getPlanChat({
        planningSessionId: sessionId,
        candidateId: id,
        planId: sessionId == null ? pId : null,
      });
      setChatViews(prev => ({ ...prev, [id]: view }));
      if (view.planning_session) {
        setChatSessions(prev => ({ ...prev, [id]: view.planning_session!.id }));
        setActiveFindChat({ ...nextChat, planningSessionId: view.planning_session.id });
      }
    } catch (err) {
      setPlanningError(messageOf(err, "读取规划对话失败"));
      setPlanningRetryCandidateId(id);
    } finally {
      setChatLoading(prev => ({ ...prev, [id]: false }));
    }
  }

  /** 发一轮「找」。返回是否成功：失败的轮次什么都没落，原话与线程都要留给用户重试。 */
  async function ask(
    text: string,
    clarAns: string | null,
    opts?: { allowShapeSwitch?: boolean; followUp?: boolean; redo?: boolean },
  ): Promise<boolean> {
    // 等待期间不清旧结果——最坏要等几分钟，界面不能变白（2026-09-26 走查）
    const previousClarify = fresh?.clarify ?? null;
    // 只有「接着这段探索说」的四种轮次带线程：答追问、确认切形态重算、闲聊续问、
    // 明确重新推荐（redo）。顶栏输入框的全新提问不带——同句原话两次就是两段独立探索。
    const continueThread =
      clarAns !== null ||
      opts?.allowShapeSwitch === true ||
      opts?.followUp === true ||
      opts?.redo === true;
    const threadId = continueThread ? activeThreadId : null;
    // 续聊轮发**线程自己的计划归属**，不发计划选择器的当前值（决策 44 ①，复核整改）：
    // 刷新恢复后选择器可能被动过，归属以后端线程头为准，发错会被 409 拒。
    const roundPlanId =
      continueThread && threadId !== null
        ? activeThreadPlanId
        : planChoice === ""
          ? null
          : Number(planChoice);
    setAsking(true);
    setAskError(null);
    setShowFindConversation(true);
    // 记下这轮挂在哪个线程：整页刷新会丢 promise，靠它按服务端状态把结果接回来。
    patchFindSession({ pendingThreadId: threadId });
    try {
      const found = await findCandidates(
        text,
        roundPlanId,
        clarAns,
        clarAns ? activeRequestId : null,
        threadId,
        opts?.allowShapeSwitch ?? false,
        opts?.redo ?? false,
      );
      setActiveRequestIdState(found.request_id);
      setActiveFindRequest(found.request_id);
      applyActiveThread(found.thread_id, found.plan_id);
      setActiveFindChat(null);
      // 模型想换形态时先记下说明等用户拍板；出候选的轮次说明已失效，一并清掉（决策 44 ③）。
      setPendingShapeChange(
        found.shape_change
          ? {
              from: found.shape_change.from,
              to: found.shape_change.to,
              reason: found.shape_change.reason,
              text,
              requestId: found.request_id,
            }
          : null,
      );
      setFindResultNotice(
        clarAns && found.intent === "candidates"
          ? { answer: clarAns, count: found.candidates.length }
          : null,
      );
      pushFindRound({
        requestText: text,
        utterance: clarAns ?? text,
        intent: found.intent,
        reply: found.reply,
        status: "success",
        createdAt: new Date().toISOString(),
        clarifyAnswered:
          clarAns && previousClarify
            ? { question: previousClarify.question, answer: clarAns }
            : null,
        clarify: found.clarify,
        recommended: found.recommended_start ?? null,
        count: found.candidates.length,
        shape: found.shape,
        requestId: found.request_id,
        threadId: found.thread_id,
      });
      setHistory([...getFindSession().history]);
      setFresh(found);
      // 非候选轮（chat / need_info）不动候选区——旧候选原样留着，不加载也不替换。
      if (found.intent === "candidates") {
        setStored(null);
        setShowFindConversation(false);
      } else {
        setShowFindConversation(true);
      }
      historyLoadId.current += 1;
      setHistoryView(null);
      setHistoryError(null);
      return true;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && threadId !== null) {
        const detail = cause.message;
        // 409 分型（复核整改）。线程本身还好的两种——「接管」（同一条追问被另一路
        // 回答抢了先）与「没有待确认的形态切换」（提案已失效）——上下文原样保留，
        // 后端的中文 detail 原样展示，用户照着提示走就行；其余（线程号坏、计划归属
        // 不符「请回到原线程」）才清线程上下文——不静默把旧线程改挂到新计划。
        const proposalGone = detail.includes("没有待确认的形态切换");
        if (proposalGone) {
          // 提案已被消费/失效：撤掉挂着的确认条，别让「切换形态」一直撞 409。
          setPendingShapeChange(null);
        }
        const keepThread = detail.includes("接管") || proposalGone;
        if (!keepThread) {
          setActiveRequestIdState(null);
          setActiveFindRequest(null);
          applyActiveThread(null);
          setFindResultNotice(null);
          const previousFind = getFindSession().fresh;
          if (previousFind) {
            setFresh({ ...previousFind, clarify: null, reply: null, shape_change: null });
          }
        }
        setAskError(detail);
      } else {
        // 普通失败（超时 / 输出不合格 / 网络）：什么都不清——原话留在输入框、原线程还在、
        // 要答的追问还在，原样重试就行（复核整改）。
        setAskError(messageOf(cause, "请求候选清单失败，原因不明"));
      }
      return false;
    } finally {
      setAsking(false);
      patchFindSession({ pendingThreadId: null });
    }
  }

  async function openHistoryRound(index: number, requestId: number) {
    const loadId = ++historyLoadId.current;
    if (historyView?.requestId === requestId) {
      // 再点一次正在回看的那条 = 退回当前结果
      setHistoryView(null);
      setHistoryError(null);
      return;
    }
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const list = await listCandidates(requestId);
      if (historyLoadId.current === loadId) {
        setHistoryView({ roundIndex: index, requestId, list });
        setPeekId(null);
        setShowFindConversation(false);
      }
    } catch (cause) {
      if (historyLoadId.current === loadId) setHistoryError(messageOf(cause, "回看历史轮次失败"));
    } finally {
      if (historyLoadId.current === loadId) setHistoryLoading(false);
    }
  }

  async function onVerdict(candidateId: number, accepted: boolean, reason?: string, explicitPlanId?: number) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accepted, reason, explicitPlanId);
      // OC-05 新语义：plan_id = **规划落点**（「新方向」为 null），node_id 恒为 null——
      // 采纳不再建阶段，回执只说「已进入规划会话」。
      const landedPlanId = done.landing_plan_id ?? done.plan_id ?? null;
      const landedPlan = landedPlanId !== null
        ? plans.find((item) => item.id === landedPlanId) ??
          workspacePlans.find((item) => item.id === landedPlanId) ??
          null
        : null;
      if (accepted) {
        if (done.planning_session_id !== null) {
          setChatSessions((prev) => ({ ...prev, [candidateId]: done.planning_session_id as number }));
        }
        setActionResults((previous) => [
          {
            kind: "adopted",
            candidateId,
            candidateTitle: getRow(candidateId)?.title ?? "候选",
            planningSessionId: done.planning_session_id,
            planningStatus: done.planning_status,
            message: done.message,
            landingPlanId: landedPlanId,
            landingPlanGoal: landedPlan?.goal ?? null,
          },
          ...previous.filter((item) => !(item.kind === "adopted" && item.candidateId === candidateId)),
        ]);
      }
      setNotes((prev) => ({
        ...prev,
        [candidateId]: accepted
          ? landedPlanId !== null
            ? `已采纳：进入规划会话，规划落点是${landedPlan ? `计划「${landedPlan.goal}」` : "所选计划"}；正式阶段等蓝图批准后才建立`
            : "已采纳：进入规划会话（新方向，还没有正式计划）；蓝图批准时才创建计划与阶段"
          : "已否决：这段探索里不再推荐它，别的探索不受影响",
      }));
      if (landedPlanId !== null) {
        setLandingPlans((prev) => ({ ...prev, [candidateId]: landedPlanId }));
      }
      setDecidedNow((prev) => ({
        ...prev,
        [candidateId]: accepted ? "accepted" : "rejected",
      }));
      setRejectingId(null);
      setAdoptingId(null);
      // 裁定后刷新左栏快照：活跃轮自己带候选就按轮取；否则按线程取最近有候选的
      // 一轮（活跃轮可能是追问 / 闲聊）。刷不动就留着旧的，不挡裁定主流程。
      try {
        const snapshot =
          fresh?.intent === "candidates" && fresh.request_id === activeRequestId
            ? await listCandidates(activeRequestId)
            : activeThreadId !== null
              ? await listCandidates(undefined, activeThreadId)
              : activeRequestId !== null
                ? await listCandidates(activeRequestId)
                : null;
        if (snapshot !== null) {
          setStored(snapshot);
        }
      } catch {
        // 快照刷不动就留着旧的
      }
      // 正回看历史轮次时裁定过：把那一轮的快照也刷一遍，别让卡片停在旧状态。
      if (historyView !== null) {
        try {
          setHistoryView({ ...historyView, list: await listCandidates(historyView.requestId) });
        } catch {
          // 快照刷不动就留着旧的，不挡裁定主流程
        }
      }
      // 采纳成功即点亮右栏进入规划对话：直接带上回执里的会话号，按会话看。
      if (accepted) {
        await openChat(candidateId, {
          planId: landedPlanId,
          planningSessionId: done.planning_session_id,
        });
      }
    } catch (cause) {
      setVerdictError(messageOf(cause, "裁定候选失败"));
    } finally {
      setVerdicting(false);
    }
  }

  // 左栏展示来源：只有 candidates 轮的 fresh 才画候选卡；chat / need_info 轮
  // 不触发候选区的加载与替换，旧候选（stored / 历史回看）原样留在原地（决策 44 ①）。
  // 刷新恢复的 fresh 被掏空了候选数组（裁定状态以库里重取为准）——空数组让位给 stored。
  const listed = historyView
    ? rowsFromStored(historyView.list)
    : fresh && fresh.intent === "candidates" && fresh.candidates.length > 0
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

  // 历史按「一段一段的对话」收起来：35 轮平铺一条条列出来没人看得完。
  const historyThreads = groupFindRounds(history);

  // 右栏只在有话可聊时出现：聊某条候选，或当前轮「找」的追问待答。
  const activeRound = history.find((round) => round.requestId === activeRequestId) ?? null;
  const activeClarify = fresh?.clarify ?? (
    activeRound?.clarify && activeRound.clarify.answer == null
      ? { question: activeRound.clarify.question, missing: activeRound.clarify.missing }
      : null
  );
  const clarifyPending = activeClarify != null && activeRequestId !== null;
  // 找方向对话的会话流：当前线程的全部轮次（旧记录无线程 id 时退回单轮），旧 → 新。
  const activeThreadRounds = activeRound
    ? activeRound.threadId != null
      ? history.filter((round) => round.threadId === activeRound.threadId)
      : history.filter((round) => round.requestId === activeRound.requestId)
    : [];
  const threadChronological = [...activeThreadRounds].reverse();
  const headRound = threadChronological[0] ?? null;
  const findTurns = threadChronological
    .filter((round) => round.status !== "failed")
    .map((round) => ({
      id: round.requestId,
      utterance: round.utterance ?? round.requestText,
      reply: round.reply ?? (round.requestId === fresh?.request_id ? fresh.reply : null),
      clarify: round.clarify?.question ?? null,
      // 出候选的轮次后端不写人话回复：带上条数，对话流里好给回执
      candidates: round.intent === "candidates" ? round.count ?? 0 : null,
    }));
  const findReply = activeRound?.reply ?? fresh?.reply ?? null;
  // 这段探索挂在哪个计划（复核整改）：以线程自己的归属为准——计划选择器被动过也不变；
  // 「新方向」线程明说它不属于任何计划。计划已不在进行中列表里时（暂停/收尾）不硬凑名字。
  const threadPlanGoal =
    activeThreadId === null
      ? null
      : activeThreadPlanId === null
        ? "新方向"
        : (plans.find((item) => item.id === activeThreadPlanId)?.goal ?? null);
  const findConversationOpen =
    findTurns.length > 0 || (findReply !== null && findReply.trim() !== "") || pendingShapeChange !== null;
  const panelOpen =
    chattingId !== null ||
    clarifyPending ||
    findResultNotice !== null ||
    findConversationOpen ||
    // 首条寻找在途时也要把右栏摆出来：只带原话的对话流 + 进度提示，别让用户对着空页面等
    asking ||
    (isDesktop && rows !== null && rows.length > 0);

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
  const reviewRow = isDesktop && !showFindConversation && chattingId === null
    ? peekRow ?? (clarifyPending ? null : rows?.[0] ?? null)
    : null;

  /** 这段规划对话按哪条会话走：视图里的会话优先，其次采纳 / 重开回执记下的；都没有 = legacy。 */
  function sessionOf(candidateId: number): number | null {
    return chatViews[candidateId]?.planning_session?.id ?? chatSessions[candidateId] ?? null;
  }

  /** legacy 候选（从没进过规划）才需要落点计划兜底；会话路径的落点由服务端从会话解析。 */
  function legacyPlanOf(candidateId: number): number | null {
    const r = getRow(candidateId);
    return landingPlans[candidateId] ?? r?.landingPlanId ?? r?.planId ?? null;
  }

  async function handleChatSend(candidateId: number, message: string) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    setPlanningError(null);
    const sessionId = sessionOf(candidateId);
    const planId = sessionId == null ? legacyPlanOf(candidateId) : null;
    try {
      await sayPlanChat({ planningSessionId: sessionId, candidateId, message, planId });
      const view = await getPlanChat({ planningSessionId: sessionId, candidateId, planId });
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
      if (view.planning_session) {
        setChatSessions(prev => ({ ...prev, [candidateId]: view.planning_session!.id }));
      }
      return view;
    } catch (err) {
      setPlanningError(messageOf(err, "规划对话这轮没发出去"));
      return null;
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  async function handleChatGenerate(candidateId: number, mode: BlueprintMode) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    setPlanningError(null);
    const sessionId = sessionOf(candidateId);
    const planId = sessionId == null ? legacyPlanOf(candidateId) : null;
    try {
      const created = await generateBlueprint({
        planningSessionId: sessionId,
        candidateId,
        planId,
        mode,
      });
      const view = await getPlanChat({ planningSessionId: sessionId, candidateId, planId });
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
      if (view.planning_session) {
        setChatSessions(prev => ({ ...prev, [candidateId]: view.planning_session!.id }));
      }
      const row = getRow(candidateId);
      const landedPlanId = created.plan_id;
      const plan = landedPlanId === null
        ? null
        : plans.find((item) => item.id === landedPlanId) ??
          workspacePlans.find((item) => item.id === landedPlanId) ??
          null;
      setActionResults((previous) => [
        {
          kind: "blueprint",
          candidateId,
          candidateTitle: row?.title ?? "候选",
          planId: landedPlanId,
          planGoal: plan?.goal ?? null,
          proposalId: created.proposal_id,
          version: created.version,
        },
        ...previous.filter((item) => !(item.kind === "blueprint" && item.candidateId === candidateId)),
      ]);
    } catch (err) {
      setPlanningError(messageOf(err, "生成蓝图方案失败"));
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  /** 把待裁定蓝图退回规划对话（理由必填，进台账）；成功返回 true，会话恢复 active。 */
  async function handleReturnBlueprint(candidateId: number, reason: string): Promise<boolean> {
    const view = chatViews[candidateId];
    const sessionId = view?.planning_session?.id ?? null;
    const proposalId = view?.pending_blueprint_proposal_id ?? view?.blueprint?.id ?? null;
    if (sessionId === null || proposalId === null) {
      setPlanningError("这段规划对话里没有可退回的待裁定蓝图——刷新后再试");
      return false;
    }
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    setPlanningError(null);
    try {
      await returnBlueprintToPlanning(proposalId, { planningSessionId: sessionId, reason });
      const next = await getPlanChat({ planningSessionId: sessionId, candidateId });
      setChatViews(prev => ({ ...prev, [candidateId]: next }));
      // 退回后这份蓝图不再等裁定：清掉同候选的「蓝图已生成」回执卡，别引导去裁定已作废的稿子
      setActionResults((previous) =>
        previous.filter((item) => !(item.kind === "blueprint" && item.candidateId === candidateId)),
      );
      return true;
    } catch (err) {
      setPlanningError(messageOf(err, "退回蓝图失败"));
      return false;
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  /** 终态会话（converted / abandoned / expired）的「重新规划」：开一段新会话，旧的只读保留。 */
  async function handleReopenPlanning(candidateId: number): Promise<boolean> {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    setPlanningError(null);
    try {
      const reopened = await reopenCandidatePlanning(candidateId);
      setChatSessions(prev => ({ ...prev, [candidateId]: reopened.planning_session_id }));
      setActiveFindChat({
        candidateId,
        planId: reopened.landing_plan_id,
        planningSessionId: reopened.planning_session_id,
      });
      const view = await getPlanChat({
        planningSessionId: reopened.planning_session_id,
        candidateId,
      });
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
      if (reopened.landing_plan_id !== null) {
        setLandingPlans(prev => ({ ...prev, [candidateId]: reopened.landing_plan_id as number }));
      }
      return true;
    } catch (err) {
      setPlanningError(messageOf(err, "重新开始规划失败"));
      return false;
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  function enterWorkbench(planId: number) {
    setSelectedPlanId(planId);
    void refreshPlans();
    router.push(`/workbench?plan_id=${encodeURIComponent(String(planId))}`);
  }

  /** 蓝图可能属于「新方向」（还没有正式计划）：没有落点就只去提案页，不选计划。 */
  function goToProposals(planId: number | null) {
    if (planId !== null) setSelectedPlanId(planId);
    router.push("/proposals");
  }

  function continuePlanning(candidateId: number, planId: number | null) {
    if (planId !== null) setSelectedPlanId(planId);
    void openChat(candidateId, {
      planId,
      planningSessionId: chatSessions[candidateId] ?? null,
    });
  }

  function renderRightPanel() {
    if (chattingId === null) {
      // 「找方向」对话：原始请求、每轮追问/回答、模型的人话回应与待确认的形态切换
      // 都在这条流里；回答与续问走底部输入框（一次追问 = 一次发信息）。
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
          clarifyData={activeClarify}
          // 答追问这轮「用户说了什么」就是那句回答本身：后端记的是本轮原话（备用展示
          // 与下一轮对照），续聊的原题由后端从线程头自己取，不靠客户端重发。
          onClarifySend={(answer) => ask(answer, answer)}
          clarifyError={askError}
          findRequest={headRound?.utterance ?? headRound?.requestText ?? rawText}
          findPlanLabel={threadPlanGoal}
          findTurns={findTurns}
          findReply={findReply}
          shapeChange={pendingShapeChange}
          onKeepShape={async () => {
            const pending = pendingShapeChange;
            if (!pending || activeThreadId === null) return;
            try {
              await keepFindShape(activeThreadId, pending.requestId);
              setPendingShapeChange(null);
              setHistory((current) => current.map((round) => round.requestId === pending.requestId
                ? { ...round, shapeDecision: "keep" }
                : round));
            } catch (cause) {
              setAskError(messageOf(cause, "保持原形态失败，请重试"));
            }
          }}
          onSwitchShape={() => {
            const pending = pendingShapeChange;
            if (!pending) return;
            // 不先清卡片：重发失败时它还挂着，用户可以再试；成功后由响应决定去留。
            void ask(pending.text, null, { allowShapeSwitch: true });
          }}
          onFollowUpSend={(text) => ask(text, null, { followUp: true })}
          findResultNotice={findResultNotice}
          onFindResultNoticeClose={() => setFindResultNotice(null)}
          variant="find"
        />
      );
    }
    const id = chattingId;
    const planView = chatViews[id] ?? null;
    // 会话路径的落点跟着会话走（landing_plan_id 可为 null =「新方向」，照样能聊能出方案）；
    // 从没进过规划的 legacy 候选才退回行上的落点，并在对话区提示先指明计划。
    const effPlanId = planView?.planning_session
      ? planView.planning_session.landing_plan_id
      : legacyPlanOf(id);
    return (
      <ChatBox
        view={planView}
        effectivePlanId={effPlanId}
        plans={plans}
        chosenPlan={chatChosenPlan[id] ?? ""}
        setChosenPlan={(val) => setChatChosenPlan(prev => ({ ...prev, [id]: val }))}
        onSend={(msg) => handleChatSend(id, msg)}
        onGenerate={(mode) => handleChatGenerate(id, mode)}
        busy={(chatBusy[id] ?? false) || (chatLoading[id] ?? false)}
        onReturnBlueprint={(reason) => handleReturnBlueprint(id, reason)}
        onReopenPlanning={() => handleReopenPlanning(id)}
      />
    );
  }

  const conversationPanel = renderRightPanel();

  return (
    <div className="flex min-h-screen flex-col bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background pt-20 pb-24">
      <div className="mx-auto w-full max-w-[1400px] px-4 md:px-8 xl:px-12">
      {/* Prompt and results share one frame */}
      <div className="mb-10 mt-4 w-full">
        <div className="mb-6 flex items-end justify-between gap-4 border-b border-white/[0.08] pb-5">
          <h1 className="text-[27px] font-medium tracking-tight text-primary">找方向</h1>
          <span className="text-[12px] text-white/45">描述困惑，比较方向，再决定是否采纳</span>
        </div>
          <PromptInput
            plans={plans}
            planChoice={planChoice}
            setPlanChoice={setPlanChoice}
            rawText={rawText}
            setRawText={setRawText}
            onSubmit={(e) => { e.preventDefault(); ask(rawText, null); }}
            asking={asking}
            bannedCount={fresh?.banned_titles?.length ?? 0}
            plansLoading={plansLoading}
            plansError={plansError}
            onRetryPlans={() => {
              setPlansLoading(true);
              setPlansError(null);
              listPlans()
                .then(setPlans)
                .catch((cause: unknown) => setPlansError(messageOf(cause, "读取计划失败")))
                .finally(() => setPlansLoading(false));
            }}
          />
      </div>

      <div className="w-full">
        {actionResults.length > 0 && (
          <div className="mb-8 space-y-3" aria-label="候选动作结果">
            {actionResults.map((result) => (
              <CandidateResultCard
                key={`${result.kind}-${result.candidateId}`}
                result={result}
                onContinuePlanning={continuePlanning}
                onEnterWorkbench={enterWorkbench}
                onGoToProposals={goToProposals}
              />
            ))}
          </div>
        )}

        <div
          ref={gridRef}
          className="grid grid-cols-1 items-start"
          style={{
            gridTemplateColumns: gridColumns,
            transition: isDesktop ? `grid-template-columns 320ms ${EASE_OUT_CSS}` : undefined,
          }}
        >

        {/* Left Column (Main) —— 对话一开就压缩成按钮列；按钮全可点（见 CandidateRail 说明） */}
        <div className="flex flex-col w-full min-w-0">
          {isDesktop && panelOpen ? (
            <>
              {(askError || historyError || verdictError || planningError) && (
                <div className="flex flex-col gap-2 mb-5 px-1">
                  {askError && <p className="text-[12px] leading-relaxed text-red-400">{askError}</p>}
                  {historyError && <p className="text-[12px] leading-relaxed text-red-400">{historyError}</p>}
                  {planningError && (
                    <div className="flex flex-wrap items-center gap-3 text-[12px] leading-relaxed text-red-400" role="alert">
                      <span>{planningError}</span>
                      {planningRetryCandidateId !== null && (
                        <button
                          type="button"
                          onClick={() => void openChat(planningRetryCandidateId, { retry: true })}
                          className="text-white/80 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                        >
                          重新读取
                        </button>
                      )}
                    </div>
                  )}
                  {verdictError && <p className="text-[12px] leading-relaxed text-red-400">裁定失败：{verdictError}</p>}
                </div>
              )}
              <CandidateRail
                rows={rows ?? []}
                activeId={reviewRow?.id ?? null}
                onPick={(id) => {
                  setChattingId(null);
                  setActiveFindChat(null);
                  setPeekId(id);
                  setShowFindConversation(false);
                }}
              />
              <button type="button" onClick={() => { setChattingId(null); setActiveFindChat(null); setShowFindConversation(true); }} className="mt-4 border-t border-white/[0.08] px-2 py-3 text-left text-[12px] text-white/55 transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-white">
                返回探索对话 <ChevronRight className="ml-1 inline size-3" aria-hidden="true" />
              </button>
              {history.length > 0 && (
                <div className="border-t border-white/[0.08] px-2 py-3">
                  <button type="button" onClick={() => setHistoryOpen((value) => !value)} aria-expanded={historyOpen} className="flex w-full items-center justify-between text-left text-[11px] text-white/50 hover:text-white/80">
                    历史对话 · {historyThreads.length} 段 <ChevronDown className={`size-3 transition-transform ${historyOpen ? "rotate-180" : ""}`} />
                  </button>
                  {historyOpen && <div className="mt-3 max-h-48 space-y-1 overflow-y-auto">
                    {historyThreads.map((thread) => {
                      const unfolded = openThreads.includes(thread.key) || thread.entries.some((entry) => entry.index === historyView?.roundIndex);
                      return <div key={thread.key} className="border-l border-white/15 pl-2">
                        <button type="button" onClick={() => toggleThread(thread.key)} aria-expanded={unfolded} className="flex w-full items-center gap-1 py-1.5 text-left text-[11px] text-white/60 hover:text-white/85"><ChevronRight className={`size-3 shrink-0 transition-transform ${unfolded ? "rotate-90" : ""}`} />{clip(thread.title, 30) || "（没有留下原话）"}</button>
                        {unfolded && <div className="ml-3 space-y-1 pb-2">{thread.entries.map(({ round, index }) => <button key={index} type="button" disabled={historyLoading || typeof round.requestId !== "number"} onClick={() => { if (typeof round.requestId === "number") void openHistoryRound(index, round.requestId); }} className={`block w-full py-1 text-left text-[11px] disabled:opacity-40 ${historyView?.roundIndex === index ? "text-primary" : "text-white/45 hover:text-white/75"}`}>第 {history.length - index} 轮 · {clip(round.utterance ?? round.requestText, 22)}</button>)}</div>}
                      </div>;
                    })}
                  </div>}
                </div>
              )}
            </>
          ) : (
          <>
          <div className="flex items-center justify-between mb-10">
            <div className="flex items-center gap-4">
              <h2 className="text-[26px] font-semibold text-primary/90 tracking-tight">候选清单</h2>
              {profileLoading ? (
                <span className="h-6 w-24 animate-pulse rounded-full bg-white/[0.05]" role="status" aria-label="正在读取长期档案" />
              ) : profileError ? (
                <span className="text-[12px] text-red-300" role="alert">
                  档案读取失败：{profileError}
                </span>
              ) : profile !== null ? (
                <span className="px-3 py-1 bg-white/[0.03] border border-white/[0.06] rounded-full text-[11px] font-medium text-white/60 shadow-sm shrink-0">
                  长期档案 {profile.items.length} 条
                </span>
              ) : null}
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
            </div>
          )}

          {planningError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]" role="alert">
              <div className="flex flex-wrap items-center gap-3">
                <span>{planningError}</span>
                {planningRetryCandidateId !== null && (
                  <button
                    type="button"
                    onClick={() => void openChat(planningRetryCandidateId, { retry: true })}
                    className="text-white/80 underline underline-offset-4 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70"
                  >
                    重新读取
                  </button>
                )}
              </div>
            </div>
          )}

          {asking && (
            <div className="rounded-xl border border-white/[0.06] bg-surface2/90 px-4 py-3 mb-6 flex items-center gap-3 shadow-[0_8px_32px_rgba(0,0,0,0.35)]">
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
                历史对话（新在上）
                <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium tracking-normal text-white/50">
                  {historyThreads.length} 段 · {history.length} 轮
                </span>
              </button>
              {historyOpen && (
              <div className="mt-3 flex flex-col">
              {historyThreads.map((thread) => {
                const threadActive = thread.entries.some(
                  (entry) => entry.index === historyView?.roundIndex,
                );
                const unfolded = openThreads.includes(thread.key) || threadActive;
                const newest = thread.entries[0];
                const oldest = thread.entries[thread.entries.length - 1];
                const newestNo = history.length - newest.index;
                const oldestNo = history.length - oldest.index;
                const span = thread.entries.length > 1 ? `第 ${oldestNo}–${newestNo} 轮` : `第 ${newestNo} 轮`;
                const when = newest.round.createdAt ? formatRoundTime(newest.round.createdAt) : "";
                return (
                  <div key={thread.key} className="min-w-0">
                    {/* 一段对话一行：默认只报总标题，点开才摊开里面的各轮 */}
                    <button
                      type="button"
                      onClick={() => toggleThread(thread.key)}
                      aria-expanded={unfolded}
                      className={`group flex w-full items-start gap-2 border-l-2 py-1.5 pl-3 pr-1 text-left transition-colors ${
                        threadActive
                          ? "border-white/40"
                          : unfolded
                            ? "border-white/25"
                            : "border-white/[0.08] hover:border-white/25"
                      }`}
                    >
                      <ChevronRight
                        className={`mt-[3px] size-3 shrink-0 transition-transform duration-200 ${
                          unfolded ? "rotate-90 text-white/55" : "text-white/35"
                        }`}
                      />
                      <span className="min-w-0 flex-1">
                        <span
                          className={`block truncate text-[12.5px] leading-relaxed ${
                            unfolded ? "text-white/75" : "text-white/60 group-hover:text-white/80"
                          }`}
                        >
                          {clip(thread.title, 40) || "（没有留下原话）"}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-white/35">
                          {span}
                          {when && ` · 最近 ${when}`}
                        </span>
                      </span>
                    </button>
                    {/* 摊开用 grid 行高过渡，不animate高度以免整列重排 */}
                    <div
                      className="grid transition-[grid-template-rows] duration-300"
                      style={{
                        gridTemplateRows: unfolded ? "1fr" : "0fr",
                        transitionTimingFunction: EASE_OUT_CSS,
                      }}
                    >
                      <div className="overflow-hidden">
                        <div className="mb-1 ml-6 mt-1 flex flex-col gap-1 border-l border-white/[0.06] pb-1 pl-3">
                          {thread.entries.map(({ round, index }) => {
                            const requestId = round.requestId;
                            const canReopen = typeof requestId === "number";
                            const active = historyView?.roundIndex === index;
                            const roundNo = history.length - index;
                            const entryClass = `w-full border-l-2 py-1 pl-2 text-left text-[12px] leading-relaxed transition-colors ${
                              active ? "border-white/40 text-white/70" : "border-white/[0.06] text-white/45"
                            } ${canReopen && !active ? "cursor-pointer hover:border-white/25 hover:text-white/70" : ""}`;
                            const body = (
                              <>
                                第 {roundNo} 轮
                                {round.clarifyAnswered && (
                                  <>
                                    {" "}
                                    · 追问「{clip(round.clarifyAnswered.question, 22)}」答：
                                    {clip(round.clarifyAnswered.answer, 16)}
                                  </>
                                )}
                                {round.recommended && (
                                  <> · 推荐「{clip(round.recommended, 18)}」（{round.count} 条候选）</>
                                )}
                                {!round.clarifyAnswered && !round.recommended && round.count > 0 && (
                                  <> · {round.count} 条候选</>
                                )}
                                {canReopen && (
                                  <span className="text-white/40">
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
                      </div>
                    </div>
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
                {/* 明确重新推荐（决策 44 ①/II-03）：同一段探索里重找一轮，带原线程 redo——
                    新版出来后才替下旧版未裁定的候选，已裁定的不受影响。 */}
                <div className="flex items-center gap-3">
                  {activeThreadId !== null && !historyView && (
                    <button
                      type="button"
                      disabled={asking}
                      onClick={() => void ask("重新推荐", null, { redo: true })}
                      title="在同一段探索里重新找一轮；新版出来后，旧版未裁定的候选会被替下（已采纳/已否决的不受影响）"
                      className="shrink-0 text-[11px] text-white/50 hover:text-white/70 border border-white/[0.06] hover:border-white/[0.2] rounded-full px-2.5 py-1 transition-colors disabled:opacity-50"
                    >
                      重新推荐
                    </button>
                  )}
                  <span className="text-[11px] text-white/60">
                    {isPath
                      ? "整条采纳或否决，去留在生成蓝图时调整"
                      : "采纳进入规划（蓝图批准后才建正式阶段），否决只在这段探索里避开"}
                  </span>
                </div>
              </div>

              {/* 推荐依据（candidates 轮的 fresh 结果才有）：一句话理由 + 可展开的明细；后端没给的字段不渲染对应行 */}
              {fresh && fresh.intent === "candidates" && !historyView && (
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
                          <div>这段探索避开的标题：{fresh.banned_titles.join("、")}</div>
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
                    plansLoading={plansLoading}
                    plansError={plansError}
                    onRetryPlans={() => {
                      setPlansLoading(true);
                      setPlansError(null);
                      listPlans()
                        .then(setPlans)
                        .catch((cause: unknown) => setPlansError(messageOf(cause, "读取计划失败")))
                        .finally(() => setPlansLoading(false));
                    }}
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
                    setChattingId={openChat}
                    clarifyNote={clarifyPending && !historyView}
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

        {/* 桌面评审席：候选目录与右侧主内容并置；点目录替换主内容，不覆盖对话。 */}
        {isDesktop ? (
          <div className="sticky top-24 flex h-[calc(100vh-8rem)] min-w-0 flex-col">
            <AnimatePresence initial={false}>
              {panelOpen && (
                <motion.div
                  key={reviewRow ? `review-${reviewRow.id}` : `conversation-${chattingId ?? "find"}`}
                  initial={{ opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -12 }}
                  transition={SPRING_PANEL}
                  className="flex h-full min-h-0 flex-col pl-8"
                >
                  {reviewRow ? (
                    <div className="flex h-full min-h-0 flex-col border-l border-white/[0.09] bg-surface-raised/40">
                      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.09] px-8 py-4 text-[11px] text-white/45 xl:px-12">
                        <span className="font-mono tabular-nums">{isPath ? "完整路径" : `候选 ${String((rows?.findIndex((item) => item.id === reviewRow.id) ?? 0) + 1).padStart(2, "0")} / ${String(rows?.length ?? 0).padStart(2, "0")}`}</span>
                        <button type="button" onClick={() => setShowFindConversation(true)} className="text-white/60 underline decoration-white/25 underline-offset-4 transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-white">查看探索对话</button>
                      </div>
                      <div className="min-h-0 flex-1 overflow-y-auto">
                        <CandidateCard
                          key={reviewRow.id}
                          presentation="dossier"
                          row={reviewRow}
                          open
                          onToggle={() => setShowFindConversation(true)}
                          isFirst
                          isLast
                          prevOpen={false}
                          nextOpen={false}
                          plans={plans}
                          plansLoading={plansLoading}
                          plansError={plansError}
                          onRetryPlans={() => {
                            setPlansLoading(true);
                            setPlansError(null);
                            listPlans()
                              .then(setPlans)
                              .catch((cause: unknown) => setPlansError(messageOf(cause, "读取计划失败")))
                              .finally(() => setPlansLoading(false));
                          }}
                          notes={notes}
                          isDecided={reviewRow.status !== "proposed"}
                          verdicting={verdicting}
                          rejectingId={rejectingId}
                          setRejectingId={setRejectingId}
                          rejectReason={rejectReason}
                          setRejectReason={setRejectReason}
                          adoptingId={adoptingId}
                          setAdoptingId={setAdoptingId}
                          onVerdict={onVerdict}
                          setChattingId={openChat}
                          clarifyNote={clarifyPending && !historyView}
                        />
                        {fresh?.intent === "candidates" && !historyView && (
                          <details className="mx-8 border-t border-white/[0.09] pb-10 pt-5 text-[12px] leading-relaxed text-white/55 xl:mx-12">
                            <summary className="cursor-pointer text-white/70 marker:text-white/35">推荐依据</summary>
                            {fresh.start_reason && <p className="mt-4">{fresh.start_reason}</p>}
                            {fresh.source && <p className="mt-2">来源：{fresh.source.name}（{fresh.source.networked ? "联网" : "不联网"}）</p>}
                            {fresh.profile_basis && <p className="mt-2">档案依据：{fresh.profile_basis.total} 条{fresh.profile_basis.missing_categories?.length ? `；缺：${fresh.profile_basis.missing_categories.map(profileCategoryLabel).join("、")}` : ""}</p>}
                            {fresh.feedback_lines?.length ? <ul className="mt-2 list-inside list-disc">{fresh.feedback_lines.map((line, index) => <li key={index}>{line}</li>)}</ul> : null}
                            {fresh.banned_titles?.length ? <p className="mt-2">本段避开的标题：{fresh.banned_titles.join("、")}</p> : null}
                          </details>
                        )}
                        {activeThreadId !== null && !historyView && (
                          <div className="mx-8 border-t border-white/[0.09] py-5 xl:mx-12">
                            <button type="button" disabled={asking} onClick={() => void ask("重新推荐", null, { redo: true })} className="text-[11px] text-white/50 underline decoration-white/25 underline-offset-4 hover:text-white disabled:opacity-40">重新推荐这段探索</button>
                          </div>
                        )}
                      </div>
                    </div>
                  ) : conversationPanel}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ) : panelOpen ? (
          <div className="mt-12 min-h-[min(620px,calc(100vh-7rem))] w-full">
            {conversationPanel}
          </div>
        ) : null}

        </div>
      </div>
      </div>
    </div>
  );
}
