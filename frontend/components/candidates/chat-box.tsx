"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useReducedMotion } from "motion/react";
import { ArrowUp, Check, CircleHelp, CornerDownRight, Loader2 } from "lucide-react";
import { PlanChatView, ChatMessage, FindResult, type BlueprintMode } from "@/lib/api";
import { HoverSelect } from "@/components/ui/hover-select";
import { MessageScroller } from "@/components/agents/message-scroller";
import { MessageBubble, MessageBubbleContent } from "@/components/agents/message-bubble";
import { StreamingResponse } from "@/components/agents/streaming-response";
import { AgentProgress } from "@/components/agents/loading-states/agent-progress";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";

type AssistantReply = {
  questions?: string[];
  ready?: boolean;
  note?: string;
};

function parseAssistantReply(content: string): AssistantReply | null {
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    return parsed as AssistantReply;
  } catch {
    return null;
  }
}

// 分段出现只给前几段（阶段 5：长回复不拖慢阅读），后续段落直接完整显示。
const REVEAL_CAP = 4;

/** 形态的中文名（决策 44 ③）：模型想换形态时，给人话对照而不是内部令牌。 */
const SHAPE_LABELS: Record<string, string> = {
  directions: "多方向",
  path: "一条路径",
};

/** 两种生成模式的一句话说明：切换控件的悬浮提示与选中后的旁注共用。 */
const MODE_HINTS: Record<BlueprintMode, string> = {
  standard: "标准：一次出稿，沿用原有流程",
  enhanced: "增强：出稿后由水平核对员与结构审查员独立复核，有异议自动修订一次",
};

function ReplyStream({ content, animate }: { content: string; animate: boolean }) {
  const reduce = useReducedMotion();
  const paragraphs = useMemo(
    () => content.split("\n\n").filter((part) => part.trim().length > 0),
    [content],
  );
  const staggerLimit = Math.min(paragraphs.length, REVEAL_CAP);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!animate || reduce || staggerLimit === 0) return;
    const timer = window.setInterval(() => {
      setShown((current) => {
        if (current >= staggerLimit) {
          window.clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, 280);
    return () => window.clearInterval(timer);
  }, [animate, reduce, staggerLimit]);

  const animated = animate && !reduce;
  // 前 4 段逐段浮现；到第 4 段（或不足 4 段）后，余下段落一次性完整出现。
  const visible = !animated || shown >= staggerLimit ? paragraphs.length : shown;
  const done = visible >= paragraphs.length;

  return (
    <StreamingResponse
      status={done ? "complete" : "streaming"}
      copyText={content}
      announce={false}
      showActions={false}
      contentClassName="text-[14px] leading-[1.7] text-white/80 [&_p]:text-white/80"
    >
      {paragraphs.slice(0, visible).map((paragraph, index) => (
        <p key={index} className="whitespace-pre-wrap">
          {paragraph}
        </p>
      ))}
    </StreamingResponse>
  );
}

function ChatMessageView({ item, animate }: { item: ChatMessage; animate: boolean }) {
  if (item.role === "user") {
    return (
      <div
        data-slot="message"
        data-from="user"
        className="flex justify-end w-full"
      >
        <MessageBubble variant="solid" align="end" className="max-w-[80%]">
          <MessageBubbleContent
            data-slot="message-content"
            className="rounded-full px-5 py-3 text-[14px] leading-relaxed shadow-[0_4px_24px_rgba(255,255,255,0.07)]"
          >
            {item.content}
          </MessageBubbleContent>
        </MessageBubble>
      </div>
    );
  }

  const data = parseAssistantReply(item.content);
  const note = data?.note;
  const questions = data?.questions ?? [];

  return (
    <div
      data-slot="message"
      data-from="assistant"
      className="flex flex-col gap-3 w-full max-w-[92%]"
    >
      <div data-slot="message-content">
        {data === null ? (
          <ReplyStream content={item.content} animate={animate} />
        ) : (
          <>
            {note ? <ReplyStream content={note} animate={animate} /> : null}

            {questions.length > 0 && (
              <div className="bg-white/[0.02] border border-white/[0.04] rounded-xl p-4 mt-3">
                <ul className="space-y-3">
                  {questions.map((question, index) => (
                    <li
                      key={`${question}-${index}`}
                      className="flex gap-3 text-[13px] text-white/70"
                    >
                      <span className="text-white/50 mt-0.5 shrink-0">
                        <CornerDownRight className="w-3.5 h-3.5" />
                      </span>
                      <span>{question}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {data.ready === true && (
              <div className="flex items-center gap-2 mt-3 py-2 px-3 bg-green/10 text-green rounded-lg w-fit text-[12px] font-medium border border-green/20">
                <Check className="w-3.5 h-3.5" />
                意向信息已充分，可以随时生成阶段蓝图
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** 追问卡（展示用）：问题摆出来，回答走底部输入框——一次追问就是一次发信息。 */
function ClarifyQuestionCard({ question }: { question: string }) {
  return (
    <div className="w-full max-w-[92%] overflow-hidden rounded-xl border border-white/[0.06] bg-surface2/80 px-4 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.45)]">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="mt-0.5 grid size-5 shrink-0 place-items-center text-muted-foreground">
          <CircleHelp className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline gap-2">
            <h3 className="text-[15px] font-medium leading-5 text-foreground">追问</h3>
            <span className="text-[11px] text-white/40">回答后带着答案再找一轮</span>
          </div>
          <p className="mt-2 text-[14px] leading-relaxed text-white/85">
            {question}
          </p>
        </div>
      </div>
    </div>
  );
}

export function ChatBox({
  view,
  effectivePlanId,
  plans,
  chosenPlan,
  setChosenPlan,
  onSend,
  onGenerate,
  busy,
  onReturnBlueprint,
  onReopenPlanning,
  clarifyData,
  onClarifySend,
  clarifyError,
  findRequest,
  findPlanLabel,
  findTurns,
  findReply,
  shapeChange,
  onKeepShape,
  onSwitchShape,
  onFollowUpSend,
  findResultNotice,
  onFindResultNoticeClose,
  variant = "plan",
}: {
  view: PlanChatView | null;
  effectivePlanId: number | null;
  plans: { id: number; goal: string }[];
  chosenPlan: string;
  setChosenPlan: (val: string) => void;
  onSend: (msg: string) => Promise<PlanChatView | null>;
  /** 生成蓝图；resolve `false` = 这次没成（模式保留，重试不用重选）。 */
  onGenerate: (mode: BlueprintMode) => Promise<boolean>;
  busy: boolean;
  /** 把待裁定蓝图退回规划对话（理由必填，进台账）；resolve false = 退回没成功。 */
  onReturnBlueprint?: (reason: string) => Promise<boolean>;
  /** 终态会话（converted / abandoned / expired）的「重新规划」：开一段新会话。 */
  onReopenPlanning?: () => Promise<boolean>;
  clarifyData?: FindResult["clarify"] | null;
  /** 发送回答；resolve `false` = 这轮失败，回答没被消费，输入框要把原话放回来。 */
  onClarifySend?: (answer: string) => Promise<boolean>;
  clarifyError?: string | null;
  findRequest?: string;
  /** 这段探索挂在哪个计划（线程自己的归属；「新方向」线程也明说）——头部的归属徽标。 */
  findPlanLabel?: string | null;
  findTurns?: { id: number | null; utterance: string; reply: string | null; clarify: string | null; candidates?: number | null }[];
  /** chat / need_info 轮模型的人话回应：对话流里的一条消息，不是候选卡。 */
  findReply?: string | null;
  /** 模型想换「多方向／一路径」形态、等用户拍板的说明（决策 44 ③）。 */
  shapeChange?: { from: string; to: string; reason: string } | null;
  onKeepShape?: () => void | Promise<void>;
  onSwitchShape?: () => void;
  /** 找方向对话里没有待答追问时的续问发送口（带原线程，不带追问 id）；
   *  resolve `false` = 这轮失败，输入框要把原话放回来。 */
  onFollowUpSend?: (text: string) => Promise<boolean>;
  findResultNotice?: { answer: string; count: number } | null;
  onFindResultNoticeClose?: () => void;
  /** find = 找方向对话实例：空态与等待文案按「找方向」写，不借用意向规划的措辞。 */
  variant?: "plan" | "find";
}) {
  const [text, setText] = useState("");
  const [blueprintMode, setBlueprintMode] = useState<BlueprintMode>("standard");
  const [pendingMode, setPendingMode] = useState<BlueprintMode | null>(null);
  const [optimistic, setOptimistic] = useState<string | null>(null);
  const [animateKey, setAnimateKey] = useState<string | null>(null);
  const [clarifyText, setClarifyText] = useState("");
  const [sentAnswer, setSentAnswer] = useState<string | null>(null);
  // 蓝图退回（OC-06）：理由必填、进台账——表单只在用户点开「退回规划」后出现。
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnReason, setReturnReason] = useState("");
  const [returnPending, setReturnPending] = useState(false);
  const [returnError, setReturnError] = useState<string | null>(null);
  const [returnNotice, setReturnNotice] = useState<string | null>(null);
  const [reopenPending, setReopenPending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const returnInputId = `blueprint-return-reason-${view?.planning_session?.id ?? view?.candidate_id ?? "chat"}`;

  const messages = useMemo(() => view?.messages ?? [], [view?.messages]);
  const isClarifyMode = !view && !!clarifyData;
  // 找方向对话的闲聊态：模型回了人话（或挂着形态切换确认），但没有待答追问。
  const isFindChat =
    !view && !isClarifyMode && (findReply != null || shapeChange != null);
  const chatPlanGoal =
    effectivePlanId != null
      ? plans.find((item) => item.id === effectivePlanId)?.goal ?? null
      : null;

  // ---- 规划会话状态（OC-05）：全部读后端给的 planning_session / planning_status，
  // 前端不自行推算业务判定。----
  const session = view?.planning_session ?? null;
  const planningStatus = view?.planning_status ?? null;
  /** 有一份待裁定蓝图：普通发言锁定，先裁定（提案页）或退回（下面的表单）。 */
  const blueprintPending = planningStatus === "blueprint_pending";
  /** converted / abandoned / expired 是终态：整段只读，可「重新规划」开新会话。 */
  const sessionTerminal =
    session !== null &&
    (session.status === "converted" || session.status === "abandoned" || session.status === "expired");
  /** legacy 候选（从没进过规划会话）且落点没定：仍要先指明注入的计划。 */
  const planBlocked = view !== null && session === null && effectivePlanId === null;
  const composerLocked = blueprintPending || sessionTerminal || planBlocked;
  const lockedPlaceholder = blueprintPending
    ? "蓝图在等裁定——普通发言已锁定"
    : sessionTerminal
      ? "这段规划会话已结束，只读"
      : "先指明落点计划";
  /** 「上一轮没聊成」只认真正的那种态：你说了话、这一轮没被回话（模型调用失败）。
   *  正常追问轮的最后一条是助手回话，不算没聊成；锁定态另有锁定提示，都不该出现这句
   *  （2026-10-04 走查 D2/D3：锁定态与正常追问轮都误显示这句，和同屏提示自相矛盾）。 */
  const lastTurnUnanswered =
    messages.length > 0 && messages[messages.length - 1]?.role === "user";

  // 乐观回答气泡只在等待期间展示；落定后由会话历史（或错误态）接管。
  // 渲染期重置（React 官方模式）：busy 翻回 false 就地清掉，不进 effect。
  if (!busy && sentAnswer !== null) {
    setSentAnswer(null);
  }

  const handleSend = async () => {
    const content = text.trim();
    if (!content || busy || composerLocked) return;
    setReturnNotice(null);

    setOptimistic(content);
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    const refreshed = await onSend(content);
    if (!refreshed) {
      setText(content);
      setOptimistic(null);
      if (textareaRef.current) textareaRef.current.style.height = "";
      return;
    }

    const assistant = [...refreshed.messages]
      .reverse()
      .find((message) => message.role === "assistant");
    if (assistant) setAnimateKey(`${assistant.created_at}:${assistant.content}`);
    setOptimistic(null);
  };

  /** 提交蓝图退回：理由必填；成功后页面会刷新视图，会话恢复 active。 */
  const submitReturn = async () => {
    const reason = returnReason.trim();
    if (!reason || returnPending || !onReturnBlueprint) return;
    setReturnPending(true);
    setReturnError(null);
    const ok = await onReturnBlueprint(reason);
    setReturnPending(false);
    if (ok) {
      setReturnOpen(false);
      setReturnReason("");
      setReturnNotice(
        "已退回规划：那版蓝图不再等裁定（旧稿只读保留），对话历史都在——接着聊，聊成后可以再出一版。",
      );
    } else {
      setReturnError("退回没成功；原因见页面的错误提示，改完可以重试。");
    }
  };

  /** 重新规划：给终态会话开一段新的（旧会话只读保留）；成功后页面刷新视图。 */
  const submitReopen = async () => {
    if (reopenPending || !onReopenPlanning) return;
    setReopenPending(true);
    setReturnNotice(null);
    const ok = await onReopenPlanning();
    setReopenPending(false);
    if (ok) setReturnError(null);
  };

  const sendClarify = () => {
    const content = clarifyText.trim();
    if (!content || busy || !onClarifySend) return;
    setSentAnswer(content);
    setClarifyText("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    void Promise.resolve(onClarifySend(content)).then((ok) => {
      if (ok !== false) return;
      // 这轮失败：回答没被消费，把原话放回输入框——重试不用重打一遍（复核整改）。
      setClarifyText(content);
      if (textareaRef.current) textareaRef.current.style.height = "";
    });
  };

  const sendFollowUp = () => {
    const content = text.trim();
    if (!content || busy || !onFollowUpSend) return;
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    void Promise.resolve(onFollowUpSend(content)).then((ok) => {
      if (ok !== false) return;
      // 这轮失败：原话放回输入框，线程上下文没动，原样重发就行（复核整改）。
      setText(content);
      if (textareaRef.current) textareaRef.current.style.height = "";
    });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (isClarifyMode) sendClarify();
      else if (view) void handleSend();
      else sendFollowUp();
    }
  };

  if (!view && !clarifyData && !isFindChat && findResultNotice) {
    return (
      <div className="mt-0 bg-[#141416] rounded-[32px] border border-green/20 flex flex-col h-full shadow-lg overflow-hidden relative">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.04] bg-surface2/30">
          <div className="flex items-center gap-3">
            <Check className="w-4 h-4 text-green" />
            <div className="text-[13px] font-medium text-primary/90">找方向完成</div>
          </div>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
          <div className="mb-5 grid size-12 place-items-center rounded-full bg-green/10 text-green">
            <Check className="w-6 h-6" />
          </div>
          <div className="text-[15px] font-medium text-primary/90 mb-2">
            已根据你的回答生成候选
          </div>
          <p className="max-w-sm text-[13px] leading-relaxed text-white/50">
            本轮已生成 {findResultNotice.count} 条候选，结果已经更新到左侧清单。
          </p>
          <div className="mt-5 w-full max-w-sm rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-3 text-left">
            <div className="text-[11px] uppercase tracking-widest text-white/35 mb-1">你的回答</div>
            <div className="text-[13px] leading-relaxed text-primary/80">{findResultNotice.answer}</div>
          </div>
          <button
            type="button"
            onClick={onFindResultNoticeClose}
            className="mt-6 rounded-full bg-white px-5 py-2.5 text-[13px] font-medium text-black transition-colors hover:bg-gray-200"
          >
            查看候选清单
          </button>
        </div>
      </div>
    );
  }

  if (!view && !clarifyData && !isFindChat && !busy) {
    const isFind = variant === "find";
    return (
      <div className="mt-0 bg-surface2/60 rounded-[32px] border border-white/[0.04] flex flex-col h-full shadow-sm overflow-hidden relative">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.04] bg-white/[0.01]">
          <div className="text-[13px] font-medium text-primary/90">{isFind ? "找方向对话" : "意向规划对话"}</div>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
          <div className="text-[14px] text-white/[0.35] font-medium tracking-wide mb-5 max-w-[240px] leading-relaxed">
            {isFind
              ? "把你的困惑从上面发出去。寻找会带着你的长期档案——档案越全，方向越贴身；要补充就去「档案」页。"
              : "采纳候选后在此继续规划"}
          </div>
          <button
            type="button"
            disabled
            className="px-6 py-2.5 border border-white/[0.08] text-white/50 rounded-full text-[13px] cursor-not-allowed bg-white/[0.01]"
          >
            {isFind ? "等第一条发出去" : "等待采纳候选"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-0 min-h-0 bg-surface2/60 rounded-2xl border border-white/[0.04] flex flex-col h-full shadow-lg overflow-hidden relative group/chatbox">
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.04] bg-surface2/30">
        <div className="flex items-center gap-3">
          <div className="text-[13px] font-medium text-primary/90">
            {view ? "意向规划对话" : "找方向对话"}
          </div>
          {/* 这段探索挂在哪个计划（复核整改）：线程自己的归属，跟计划选择器解耦。 */}
          {!view && findPlanLabel && (
            <div className="text-[11px] text-white/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04] max-w-[220px] truncate">
              {findPlanLabel}
            </div>
          )}
          {view && (
            <div className="text-[11px] text-white/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04]">
              {view.turns_used}/{view.max_turns} 轮
            </div>
          )}
          {chatPlanGoal && (
            <div className="text-[11px] text-white/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04] max-w-[220px] truncate">
              {chatPlanGoal}
            </div>
          )}
          {session && session.landing_plan_id === null && !chatPlanGoal && (
            <div className="text-[11px] text-white/50 font-medium px-2 py-0.5 rounded-full bg-white/[0.04] max-w-[240px] truncate">
              新方向 · 还没有正式计划
            </div>
          )}
        </div>

        {view?.blueprint != null && view.blueprint.candidate_id === view.candidate_id && (
          <a
            href="/proposals"
            className="text-[12px] font-medium text-amber-500 hover:text-amber-400 bg-amber-500/10 px-3 py-1.5 rounded-full transition-colors flex items-center gap-1.5"
          >
            有蓝图待你批准
            <ArrowUp className="w-3.5 h-3.5 rotate-45" />
          </a>
        )}
      </div>

      {planBlocked && (
        <div className="m-4 p-4 rounded-2xl bg-amber-500/5 border border-amber-500/10 flex flex-col gap-3">
          <div className="text-[13px] text-amber-500/90 font-medium">
            这条旧候选还没进过规划会话，请先指定注入的计划：
          </div>
          <HoverSelect
            value={chosenPlan}
            onChange={setChosenPlan}
            placeholder="请选择目标计划…"
            className="max-w-sm w-full"
            searchable
            options={[
              { value: "", label: "请选择目标计划…" },
              ...plans.map((item) => ({ value: String(item.id), label: item.goal })),
            ]}
          />
        </div>
      )}

      <MessageScroller
        navigation="rail"
        navigationLabel="跳到某一轮规划对话"
        label={view ? "意向规划对话" : "找方向对话"}
        busy={busy}
        className="flex-1 min-h-0"
        viewportClassName="px-4 pt-4 pb-32 md:px-6 md:pt-6 md:pb-32"
        contentClassName="flex flex-col gap-6"
      >
        {!view && (
          <>
            {!findTurns?.length && findRequest && (
              <div data-slot="message" data-from="user" className="flex justify-end w-full">
                <MessageBubble variant="solid" align="end" className="max-w-[80%]">
                  <MessageBubbleContent
                    data-slot="message-content"
                    className="rounded-full px-5 py-3 text-[14px] leading-relaxed shadow-[0_4px_24px_rgba(255,255,255,0.07)]"
                  >
                    {findRequest}
                  </MessageBubbleContent>
                </MessageBubble>
              </div>
            )}

            {findTurns?.map((turn, index) => (
              <div key={`find-turn-${turn.id ?? `legacy-${index}`}`} className="flex flex-col gap-4">
                {turn.utterance.trim() && (
                  <div data-slot="message" data-from="user" className="flex justify-end w-full">
                    <MessageBubble variant="solid" align="end" className="max-w-[80%]">
                      <MessageBubbleContent data-slot="message-content" className="rounded-full px-5 py-3 text-[14px] leading-relaxed">
                        {turn.utterance}
                      </MessageBubbleContent>
                    </MessageBubble>
                  </div>
                )}
                {turn.reply?.trim() && (
                  <div data-slot="message" data-from="assistant" className="w-full max-w-[92%]">
                    <ReplyStream content={turn.reply} animate={false} />
                  </div>
                )}
                {turn.clarify && (turn.id !== findTurns[findTurns.length - 1]?.id || !clarifyData) && (
                  <div data-slot="message" data-from="assistant" className="w-full">
                    <ClarifyQuestionCard question={turn.clarify} />
                  </div>
                )}
                {/* 出候选的轮次后端不写人话回复（候选本身就是回答）：给一条回执，
                    不然这轮在对话流里只剩用户那句，看起来像模型没理你。 */}
                {typeof turn.candidates === "number" && !turn.reply?.trim() && (
                  <div data-slot="message" data-from="assistant" className="w-full max-w-[92%]">
                    <div className="flex items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-green" aria-hidden="true" />
                      <p className="text-[13px] leading-relaxed text-white/70">
                        这一轮给出了 <span className="font-medium text-white">{turn.candidates}</span>{" "}
                        条候选，已放进左侧清单——点开逐条看，或直接在这里接着说想调整的方向。
                      </p>
                    </div>
                  </div>
                )}
              </div>
            ))}
            {!findTurns?.length && findReply?.trim() && (
              <div data-slot="message" data-from="assistant" className="w-full max-w-[92%]">
                <ReplyStream content={findReply} animate={false} />
              </div>
            )}

            {/* 模型想换形态：说明理由，给两个动作；确认前候选区保持旧形态（决策 44 ③）。 */}
            {shapeChange && (
              <div className="w-full max-w-[92%] rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3">
                <div className="text-[13px] font-medium text-amber-500/90">
                  模型想换一种给法：{SHAPE_LABELS[shapeChange.from] ?? shapeChange.from} →{" "}
                  {SHAPE_LABELS[shapeChange.to] ?? shapeChange.to}
                </div>
                {shapeChange.reason && (
                  <p className="mt-1.5 text-[12px] leading-relaxed text-white/60">
                    {shapeChange.reason}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={onKeepShape}
                    disabled={busy}
                    className="px-3.5 py-1.5 rounded-full border border-white/[0.12] text-[12px] font-medium text-white/80 hover:border-white/[0.3] transition-colors disabled:opacity-50"
                  >
                    保持当前形态
                  </button>
                  <button
                    type="button"
                    onClick={onSwitchShape}
                    disabled={busy}
                    className="px-3.5 py-1.5 rounded-full bg-amber-500 text-black text-[12px] font-medium hover:bg-amber-400 transition-colors disabled:opacity-50"
                  >
                    切换形态重算
                  </button>
                </div>
              </div>
            )}

            {clarifyData && !findTurns?.some((turn) => turn.clarify === clarifyData.question) && (
              <div data-slot="message" data-from="assistant" className="w-full">
                <ClarifyQuestionCard question={clarifyData.question} />
              </div>
            )}

            {sentAnswer && (
              <div data-slot="message" data-from="user" className="flex justify-end w-full">
                <MessageBubble variant="solid" align="end" className="max-w-[80%]">
                  <MessageBubbleContent
                    data-slot="message-content"
                    className="rounded-full px-5 py-3 text-[14px] leading-relaxed shadow-[0_4px_24px_rgba(255,255,255,0.07)]"
                  >
                    {sentAnswer}
                  </MessageBubbleContent>
                </MessageBubble>
              </div>
            )}

            {clarifyError && (
              <div data-slot="message" data-from="assistant" className="text-[12px] leading-relaxed text-red-400">{clarifyError}</div>
            )}
          </>
        )}

        {view !== null && view.steps.length > 0 && (
          <div className="bg-white/[0.02] border border-white/[0.04] rounded-2xl p-5">
            <div className="text-[13px] font-semibold text-primary/80 mb-1">
              这一步的底稿：它当时给的先后步骤
            </div>
            <div className="text-[12px] text-white/50 mb-4">
              （草案，不是成品——要在哪儿加、合并、砍掉，直接跟它说）
            </div>
            <ul className="space-y-3 pl-2 border-l-2 border-white/[0.06]">
              {view.steps.map((step, index) => (
                <li key={index} className="pl-4 text-[13px] text-primary/90 leading-relaxed">
                  <span className="font-medium mr-2">{step.title}</span>
                  {step.deliverable && (
                    <span className="text-white/50">｜ 要交：{step.deliverable}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {view !== null && view.messages.length === 0 && !optimistic && !busy && (
          <div className="flex items-center justify-center text-center mt-12 px-4">
            <p className="text-[13px] text-white/50 max-w-sm leading-relaxed">
              先聊清楚你的时间投入、首选切入点与验收目标，聊透后再出蓝图。
            </p>
          </div>
        )}

        {messages.map((item, index) => {
          const signature = `${item.created_at}:${item.content}`;
          return (
            <ChatMessageView
              key={`${signature}:${index}`}
              item={item}
              animate={signature === animateKey}
            />
          );
        })}

        {optimistic && (
          <div className="flex justify-end w-full">
            <MessageBubble variant="solid" align="end" className="max-w-[80%]">
              <MessageBubbleContent
                data-slot="message-content"
                className="rounded-full px-5 py-3 text-[14px] leading-relaxed shadow-[0_4px_24px_rgba(255,255,255,0.07)]"
              >
                {optimistic}
              </MessageBubbleContent>
            </MessageBubble>
          </div>
        )}

        {/* 会话状态条（OC-05）：只转述后端给的 planning_status，不自行判定。 */}
        {view !== null && blueprintPending && (
          <div className="w-full max-w-[92%] rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3">
            <p className="text-[13px] font-medium text-amber-200/90">
              蓝图已生成，这段规划在等你裁定
            </p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-white/60">
              裁定之前普通发言已锁定。去提案页批准或驳回这版蓝图（驳回会退回这里继续聊）；
              或者不裁定，直接把它退回规划，接着聊再出新版。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <a
                href="/proposals"
                className="rounded-full bg-amber-500 px-3.5 py-1.5 text-[12px] font-medium text-black transition-colors hover:bg-amber-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
              >
                去提案页裁定
              </a>
              {onReturnBlueprint && !returnOpen && (
                <button
                  type="button"
                  onClick={() => { setReturnOpen(true); setReturnError(null); }}
                  disabled={busy || returnPending}
                  className="rounded-full border border-white/[0.12] px-3.5 py-1.5 text-[12px] font-medium text-white/80 transition-colors hover:border-white/[0.3] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50"
                >
                  退回规划继续聊…
                </button>
              )}
            </div>
            {returnOpen && (
              <div className="mt-3 border-t border-amber-500/15 pt-3">
                <label htmlFor={returnInputId} className="block text-[12px] font-medium text-white/70">
                  退回理由（必填，进台账）
                </label>
                <textarea
                  id={returnInputId}
                  value={returnReason}
                  onChange={(event) => setReturnReason(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void submitReturn();
                    }
                  }}
                  rows={2}
                  placeholder="这版蓝图为什么先不裁定？例如：成果定义还不是我想要的…"
                  disabled={returnPending || busy}
                  className="mt-2 w-full resize-none rounded-lg border border-white/[0.1] bg-black/30 px-3 py-2 text-[13px] leading-relaxed text-primary placeholder-white/30 outline-none transition-colors focus:border-white/30 disabled:opacity-50"
                />
                {returnError && (
                  <p role="alert" className="mt-2 text-[12px] leading-relaxed text-red-400">{returnError}</p>
                )}
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void submitReturn()}
                    disabled={returnPending || busy || !returnReason.trim()}
                    className="rounded-full bg-white px-4 py-1.5 text-[12px] font-medium text-black transition-colors hover:bg-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50"
                  >
                    {returnPending ? "退回中…" : "确认退回规划"}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setReturnOpen(false); setReturnReason(""); setReturnError(null); }}
                    disabled={returnPending || busy}
                    className="rounded-full px-3 py-1.5 text-[12px] text-white/55 transition-colors hover:text-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {view !== null && sessionTerminal && (
          <div className="w-full max-w-[92%] rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-3">
            <p className="text-[13px] font-medium text-white/80">
              {session?.status === "converted"
                ? "这段规划已转正：蓝图批准，会话只读保留"
                : session?.status === "abandoned"
                  ? "这段规划会话已被放弃，只读保留"
                  : "这段规划会话超过无活动期限，已过期（只读保留）"}
            </p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-white/55">
              {session?.closed_reason ? `结束原因：${session.closed_reason}。` : ""}
              历史对话都在上面；想基于这条候选再规划一轮，可以开一段新的会话（旧会话不会被改动）。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {onReopenPlanning && (
                <button
                  type="button"
                  onClick={() => void submitReopen()}
                  disabled={reopenPending || busy}
                  className="rounded-full bg-white px-4 py-1.5 text-[12px] font-medium text-black transition-colors hover:bg-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:opacity-50"
                >
                  {reopenPending ? "开启中…" : "重新规划"}
                </button>
              )}
              {session?.status === "converted" && session.landing_plan_id !== null && (
                <a
                  href={`/workbench?plan_id=${encodeURIComponent(String(session.landing_plan_id))}`}
                  className="rounded-full border border-white/[0.12] px-3.5 py-1.5 text-[12px] font-medium text-white/80 transition-colors hover:border-white/[0.3] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                >
                  进入工作台看这棵树
                </a>
              )}
            </div>
          </div>
        )}

        {returnNotice && !blueprintPending && (
          <p role="status" className="w-full max-w-[92%] text-[12px] leading-relaxed text-green/90">
            {returnNotice}
          </p>
        )}

      </MessageScroller>

      {busy && (
        <div className="absolute bottom-[5.75rem] left-5 right-5 z-20 rounded-xl border border-white/[0.06] bg-surface2/95 px-4 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.35)] backdrop-blur-md">
          <div className="flex items-center gap-3">
            <AgentProgress label="think" />
            <ThinkingShimmer className="text-[13px] text-muted/60">
              {!view
                ? "正在找候选，最慢要几分钟…"
                : pendingMode === "enhanced"
                  ? "出稿中，随后两位审查员独立复核…"
                  : "正在整理思路…"}
            </ThinkingShimmer>
          </div>
          {!view && (
            <p className="mt-1.5 text-[11px] leading-snug text-muted/50">
              这次寻找会参考你的长期档案；想补充就先去「档案」页加几条，下一次寻找就会带上
            </p>
          )}
          {view && pendingMode === "enhanced" && (
            <p className="mt-1.5 text-[11px] leading-snug text-muted/50">
              增强是多轮串行（初稿 → 两位审查员 → 必要时修订），整段常要十分钟上下——不是卡住了
            </p>
          )}
        </div>
      )}

      <div className="absolute bottom-4 left-4 right-4 bg-surface2/90 backdrop-blur-md rounded-2xl border border-white/[0.04] p-1.5 flex items-end shadow-lg group-focus-within/chatbox:border-white/[0.15] transition-colors z-10">
        <textarea
          ref={textareaRef}
          data-rec="plan-chat-input"
          aria-label={isClarifyMode ? "回答追问" : view ? "补充规划要求" : "继续这段探索"}
          value={isClarifyMode ? clarifyText : text}
          onChange={(event) => {
            const value = event.target.value;
            if (isClarifyMode) setClarifyText(value);
            else setText(value);
            event.target.style.height = "auto";
            event.target.style.height = `${event.target.scrollHeight}px`;
          }}
          onKeyDown={handleKeyDown}
          disabled={busy || composerLocked}
          className="w-full bg-transparent border-none focus:ring-0 focus:outline-none resize-none text-[14px] text-primary placeholder-white/30 py-2.5 px-3 max-h-[160px] min-h-[44px] overflow-hidden leading-relaxed disabled:opacity-50"
          rows={1}
          placeholder={
            isClarifyMode
              ? busy
                ? "cadence 正在找候选…"
                : "一句话回答，回车发送…"
              : view
                ? busy
                  ? "cadence 正在思考..."
                  : composerLocked
                    ? lockedPlaceholder
                    : "补充要求，例如：每周 5 小时..."
                : busy
                  ? "cadence 正在思考…"
                  : "接着说，继续这段探索…"
          }
        />

        <div className="flex items-center gap-2 pl-2 shrink-0">
          {/* 决策 44 / III-01：出方案按钮只认 can_generate（按聊成的有效轮数算）；
              聊过但没聊成的那轮不算数——给一句人话提示，别让用户对着消失的按钮发呆。
              文案按状态分层（走查 D2/D3）：锁定态另有锁定提示、这里一个字都不显示；
              「上一轮没被回话」说没聊成；正常追问轮（最后一轮它答了）说还差信息。 */}
          {!isClarifyMode && view && !composerLocked && !view.can_generate && lastTurnUnanswered && (
            <span className="hidden md:block max-w-[240px] text-right text-[11px] leading-snug text-white/45">
              上一轮没聊成，还不算有效对话——再说一句，聊成了就能出方案。
            </span>
          )}
          {!isClarifyMode && view && !composerLocked && !view.can_generate && !lastTurnUnanswered && messages.length > 0 && (
            <span className="hidden md:block max-w-[240px] text-right text-[11px] leading-snug text-white/45">
              信息还不够——接着聊，聊成了就能出方案。
            </span>
          )}
          {!isClarifyMode && view?.can_generate && (
            <>
              {blueprintMode === "enhanced" && (
                <span className="hidden md:block max-w-[220px] text-right text-[11px] leading-snug text-amber-200/70">
                  出稿后两位独立审查员逐条复核，有异议自动修订一次
                </span>
              )}
              {/* 模式切换用分段按钮而不是下拉：两种模式摆在一起，选中项带悬浮说明，
                  增强「多了一道审查席」这件事在点生成之前就看得见。 */}
              <div
                role="radiogroup"
                aria-label="本次蓝图生成模式"
                className="flex shrink-0 items-center rounded-full border border-white/10 bg-white/[0.03] p-0.5"
              >
                {(["standard", "enhanced"] as const).map((mode) => {
                  const active = blueprintMode === mode;
                  return (
                    <button
                      key={mode}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      title={MODE_HINTS[mode]}
                      disabled={busy}
                      data-rec={`mode-${mode}`}
                      onClick={() => setBlueprintMode(mode)}
                      className={`rounded-full px-3 py-1.5 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                        active
                          ? mode === "enhanced"
                            ? "bg-amber-400/15 text-amber-200"
                            : "bg-white/[0.14] text-white"
                          : "text-white/45 hover:text-white/80"
                      }`}
                    >
                      {mode === "standard" ? "标准" : "增强"}
                    </button>
                  );
                })}
              </div>
              <button
                type="button"
                onClick={() => {
                  setPendingMode(blueprintMode);
                  setReturnNotice(null);
                  void onGenerate(blueprintMode)
                    .then((ok) => {
                      // 只有生成成功才回默认模式；失败保留用户选的模式——不然重试时
                      // 已静默降回标准，用户以为自己选错了（2026-10-04 走查 D4）。
                      if (ok) setBlueprintMode("standard");
                    })
                    .finally(() => setPendingMode(null));
                }}
                disabled={busy || composerLocked}
                data-rec="generate-blueprint"
                className="px-4 py-2.5 bg-green/10 text-green hover:bg-green/20 rounded-xl transition-colors text-[13px] font-medium disabled:opacity-50 disabled:bg-transparent"
                title="意向达成，生成蓝图"
              >
                生成蓝图
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => (isClarifyMode ? sendClarify() : view ? void handleSend() : sendFollowUp())}
            disabled={
              isClarifyMode
                ? !clarifyText.trim() || busy
                : !text.trim() || busy || composerLocked
            }
            className="w-10 h-10 bg-white text-black rounded-xl hover:bg-gray-200 transition-colors flex items-center justify-center disabled:opacity-50 disabled:bg-white/10 disabled:text-white/50"
            aria-label={isClarifyMode ? "发送回答" : view ? "发送规划要求" : "发送续问"}
          >
            {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <ArrowUp className="w-5 h-5" />}
          </button>
        </div>
      </div>
    </div>
  );
}
