import type { CandidateList, FindResult } from "@/lib/api";

/**
 * 「找」的会话存续（2026-09-26 走查整改）：
 *
 * 走查抓到三件事：① 提交后取数要等几十秒到几分钟，期间旧结果被清空、整列变白；
 * ② 答完的追问没有地方回看；③ 等模型的半路切去别的页再切回来，一切都没了——
 * 结果其实已经落库，只是组件卸载时把状态全丢了。
 *
 * 对策：把「找」的进行时状态放进模块级单例（SPA 内切页不丢），组件挂载时从这里取初值，
 * 每次变更写回；历史轮次与输入框草稿另存 sessionStorage（整页刷新也能回来）。
 * 在途请求即使组件已卸载，落定后照样写进这里——切回来就能看到结果。
 */

export type FindRound = {
  /** 那一轮提交的原话。 */
  requestText: string;
  /** 这一轮回应的是哪条追问（首次提交为 null）。 */
  clarifyAnswered: { question: string; answer: string } | null;
  /** 这一轮模型留下的待回答追问。 */
  clarify?: { question: string; missing: string; answer?: string | null } | null;
  recommended: string | null;
  count: number;
  /** 非候选轮（chat / need_info）没有形态，是 null——不推断假形态。 */
  shape: string | null;
  /**
   * 那一轮「找」落库的回执 id（FindResult.request_id）。有它才能调 listCandidates
   * 把那一轮的候选摆回左栏回看；功能上线前存的老轮次没有这个字段，保持纯文字不可点。
   */
  requestId: number | null;
  /** 用户这轮实际说的话；旧记录没有时才退回 requestText。 */
  utterance?: string | null;
  /** 仅成功轮有回复，失败或旧记录为 null。 */
  intent?: string | null;
  reply?: string | null;
  status?: "pending" | "success" | "failed" | null;
  shapeDecision?: "pending" | "keep" | "switch" | null;
  /** 这一轮什么时候提交的（历史列表上显示「最近 …」用）；更早存下的轮次没有就是 null。 */
  createdAt?: string | null;
  /**
   * 这轮所属的探索线程（线程头请求 id，决策 44 ①）。旧记录是 null——
   * null 与 null 之间不靠原话强行认亲，null 与新线程之间也不合并。
   */
  threadId?: number | null;
};

/** 一段对话：一次原始提问，加上它后面接着答的那些追问轮。 */
export type FindThread = {
  /** 段标识，展开/回看按它认：新记录用线程 id，老轮次退回回执 id 或下标。 */
  key: string;
  /** 总标题 = 这段对话最初问的那句原话。 */
  title: string;
  /** 这段的探索线程 id（新记录才有）；老轮次为 null，靠既有启发式分组。 */
  threadId: number | null;
  /** 段内轮次，新在上；`index` 是它在 history 里的下标，回看那一轮要用。 */
  entries: { round: FindRound; index: number }[];
};

/**
 * 续写标记：后端回答追问时拼进下一轮原话里的分隔符（`advisor.compose_clarify_round`），
 * 外加更早版本留下的「我的回答：」写法。
 */
const CONTINUATION_MARKS = ["\n【追问】", "\n补充：", "我的回答："];

/** 这一轮真正「问的是什么」：切掉续写标记之后缀着的那半句。 */
function baseQuestionOf(text: string): string {
  const whole = (text ?? "").trim();
  let end = whole.length;
  for (const mark of CONTINUATION_MARKS) {
    const at = whole.indexOf(mark);
    if (at >= 0 && at < end) end = at;
  }
  return whole.slice(0, end).trim();
}

/**
 * 把逐轮的历史收成「一段一段的对话」（2026-09-27 走查：35 条平铺列出来没人看得完）。
 *
 * 判据只有一条（决策 44 ①）：带线程 id 的轮次按线程 id 归段——同一段探索的轮次
 * （含交错的 A→B→A 发言）归在一起；同句原话发起的两次独立探索是两段，不靠文字猜。
 *
 * 旧记录没有线程 id——关系无法确认就不认：各自独立成段，绝不与新线程合并，
 * 互相之间也不按原话/追问猜合并（复核整改）。
 */
export function groupFindRounds(rounds: FindRound[]): FindThread[] {
  const oldestFirst = rounds.map((round, index) => ({ round, index })).reverse();
  const threads: FindThread[] = [];
  for (const item of oldestFirst) {
    const text = item.round.requestText ?? "";
    const base = baseQuestionOf(text);
    const roundThread = item.round.threadId ?? null;
    if (roundThread !== null) {
      // 带线程 id 的轮次认线不认邻：A→B→A 交错的两次发言仍归同一段 A（复核整改）。
      // 只在既有线程里找归属，找不到才开新段——绝不因为「跟上一段不同」就把同一线程拆开。
      const home = threads.find((thread) => thread.threadId === roundThread);
      if (home) {
        home.entries.push(item);
        continue;
      }
    }
    threads.push({
      key:
        roundThread !== null
          ? `thread-${roundThread}`
          : item.round.requestId === null
            ? `legacy-${item.index}`
            : `req-${item.round.requestId}`,
      title: base !== "" ? base : text.trim(),
      threadId: roundThread,
      entries: [item],
    });
  }
  // 段内与段间都按最新发言在前；A→B→A 的 A 应排在 B 前。
  for (const thread of threads) thread.entries.reverse();
  return threads.sort((a, b) => a.entries[0].index - b.entries[0].index);
}

export type ActiveFindChat = {
  candidateId: number;
  planId: number | null;
};

/**
 * 模型请求换「多方向／一路径」形态、正等用户拍板的说明（决策 44 ③）。
 * `text` 是触发这轮说明的那句原话——用户确认切换时原样重发（带原线程 + allow_shape_switch）。
 * 只活在内存会话里，整页刷新即失（与 fresh 同等待遇）。
 */
export type PendingShapeChange = {
  from: string;
  to: string;
  reason: string;
  text: string;
  requestId: number;
};

type FindSession = {
  fresh: FindResult | null;
  stored: CandidateList | null;
  asking: boolean;
  /** 在途请求所属线程；整页刷新后用于向服务端核对最终结果。 */
  pendingThreadId: number | null;
  error: string | null;
  history: FindRound[];
  rawText: string;
  activeRequestId: number | null;
  /**
   * 当前探索线程（决策 44 ①）：发起探索后记下响应的 thread_id，续问 / 答追问都带上它；
   * 线程归属被 409 拒绝时清掉——不静默把旧线程改挂到别的计划。随 sessionStorage 恢复。
   */
  activeThreadId: number | null;
  /**
   * 当前线程的**计划归属**（决策 44 ①，复核整改）：线程头请求属于哪个计划，续问 /
   * 答追问 / 重新推荐就发哪个计划——不信任界面上的计划选择器（刷新恢复后它可能被
   * 动过，发错会被后端 409 拒）。null = 「新方向」线程或当前没有线程。随存档恢复。
   */
  activeThreadPlanId: number | null;
  /** 模型想换形态、等用户确认的说明；确认前候选区保持旧形态不动。 */
  pendingShapeChange: PendingShapeChange | null;
  activeChat: ActiveFindChat | null;
};

const session: FindSession = {
  fresh: null,
  stored: null,
  asking: false,
  pendingThreadId: null,
  error: null,
  history: [],
  rawText: "",
  activeRequestId: null,
  activeThreadId: null,
  activeThreadPlanId: null,
  pendingShapeChange: null,
  activeChat: null,
};

const PERSIST_KEY = "cadence-find-session";
let storageHydrated = false;
const listeners = new Set<() => void>();

export function subscribeFindSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function notify(): void {
  for (const listener of listeners) listener();
}

/**
 * 只在客户端挂载后恢复浏览器存档，避免 SSR 首屏与客户端首屏不一致。
 * SPA 内的模块级会话仍然优先保留，整页刷新时再从 sessionStorage 恢复。
 */
export function setActiveFindRequest(requestId: number | null): void {
  session.activeRequestId = requestId;
  persist();
}

/** 记下 / 清掉当前探索线程（发起探索成功后带上响应的 thread_id；409 冲突时清空）。 */
export function setActiveFindThread(threadId: number | null): void {
  session.activeThreadId = threadId;
  persist();
}

export function setActiveFindChat(activeChat: ActiveFindChat | null): void {
  session.activeChat = activeChat;
  persist();
}

export function hydrateFindSession(): FindSession {
  if (storageHydrated || typeof window === "undefined") return session;
  storageHydrated = true;
  try {
    const raw = window.sessionStorage.getItem(PERSIST_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as {
        history?: FindRound[];
        rawText?: string;
        activeRequestId?: number | null;
        activeThreadId?: number | null;
        activeThreadPlanId?: number | null;
        pendingThreadId?: number | null;
        activeChat?: ActiveFindChat | null;
        fresh?: FindResult | null;
        pendingShapeChange?: PendingShapeChange | null;
      };
      session.history = Array.isArray(saved.history) ? saved.history : [];
      session.rawText = typeof saved.rawText === "string" ? saved.rawText : "";
      session.activeRequestId = saved.activeRequestId ?? null;
      session.activeThreadId = saved.activeThreadId ?? null;
      session.activeThreadPlanId = saved.activeThreadPlanId ?? null;
      session.pendingThreadId = saved.pendingThreadId ?? null;
      session.activeChat = saved.activeChat ?? null;
      // 最后一轮的回执与待确认的形态切换也随存档回来（复核整改）：普通助手回复、
      // 追问槽位、形态说明原来刷新即失，只能重新问一轮才看得见。
      // 候选清单不随存档复活：候选轮的 fresh 掏空候选数组，让位给挂载后按线程重取的
      // 库内清单——裁定状态以库里为准，存档里的「待裁定」可能是刷新前的旧貌。
      const restoredFresh = saved.fresh ?? null;
      session.fresh =
        restoredFresh && restoredFresh.intent === "candidates"
          ? { ...restoredFresh, candidates: [], candidate_ids: [], recommended_start: null }
          : restoredFresh;
      session.pendingShapeChange = saved.pendingShapeChange ?? null;
    }
  } catch {
    // 存档坏了就当没有，不挡正常流程
  }
  return session;
}

function persist(): void {
  notify();
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      PERSIST_KEY,
      JSON.stringify({
        history: session.history,
        rawText: session.rawText,
        activeRequestId: session.activeRequestId,
        activeThreadId: session.activeThreadId,
        activeThreadPlanId: session.activeThreadPlanId,
        pendingThreadId: session.pendingThreadId,
        activeChat: session.activeChat,
        fresh: session.fresh,
        pendingShapeChange: session.pendingShapeChange,
      }),
    );
  } catch {
    // 存不下就算了（隐私模式等），内存里的还在
  }
}

export function getFindSession(): FindSession {
  return session;
}

export function patchFindSession(patch: Partial<Omit<FindSession, "history">>): void {
  Object.assign(session, patch);
  persist();
}

export function pushFindRound(round: FindRound): void {
  session.history = [round, ...session.history].slice(0, 200);
  persist();
}

export function clearFindSession(): void {
  session.fresh = null;
  session.stored = null;
  session.asking = false;
  session.pendingThreadId = null;
  session.error = null;
  session.history = [];
  session.rawText = "";
  session.activeRequestId = null;
  session.activeThreadId = null;
  session.activeThreadPlanId = null;
  session.pendingShapeChange = null;
  session.activeChat = null;
  persist();
}
