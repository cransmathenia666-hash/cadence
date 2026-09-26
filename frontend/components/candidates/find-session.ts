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
  recommended: string | null;
  count: number;
  shape: string;
};

type FindSession = {
  fresh: FindResult | null;
  stored: CandidateList | null;
  asking: boolean;
  error: string | null;
  history: FindRound[];
  rawText: string;
};

const session: FindSession = {
  fresh: null,
  stored: null,
  asking: false,
  error: null,
  history: [],
  rawText: "",
};

const PERSIST_KEY = "cadence-find-session";

if (typeof window !== "undefined") {
  try {
    const raw = window.sessionStorage.getItem(PERSIST_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as { history?: FindRound[]; rawText?: string };
      session.history = Array.isArray(saved.history) ? saved.history : [];
      session.rawText = typeof saved.rawText === "string" ? saved.rawText : "";
    }
  } catch {
    // 存档坏了就当没有，不挡正常流程
  }
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      PERSIST_KEY,
      JSON.stringify({ history: session.history, rawText: session.rawText }),
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
  session.history = [round, ...session.history].slice(0, 10);
  persist();
}

export function clearFindSession(): void {
  session.fresh = null;
  session.stored = null;
  session.asking = false;
  session.error = null;
  session.history = [];
  session.rawText = "";
  persist();
}
