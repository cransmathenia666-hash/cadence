import {
  PlanDialogueView,
  DialogueQuestion,
  decideProposal,
  ApiError,
} from "@/lib/api";
import {
  Check,
  ChevronDown,
  CircleCheck,
  CornerDownLeft,
  Database,
  Info,
  BookOpenText,
  Sparkles,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { MessageBubble, MessageBubbleContent } from "@/components/agents/message-bubble";
import { StreamingResponse } from "@/components/agents/streaming-response";
import { AgentDisclosure } from "@/components/agents/agent-disclosure";
import { CitationList } from "@/components/agents/citations";
import {
  ApprovalCard,
  type ApprovalCardAnswers,
  type ApprovalCardQuestion,
} from "@/components/agents/approval-card";
import { TiltCard } from "@/components/motion/tilt-card";
import { SPRING_SWAP } from "@/lib/ease";
import { cn } from "@/lib/utils";

// 裁定/回答完的卡收起成的一行摘要，点击可展开回看详情。
function CollapsedNote({
  tone,
  label,
  summary,
  children,
}: {
  tone: "done" | "muted";
  label: string;
  summary: string;
  children?: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const reduce = useReducedMotion() ?? false;
  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-2.5 text-left transition-colors hover:bg-white/[0.05]"
      >
        <CircleCheck
          className={cn(
            "size-3.5 shrink-0",
            tone === "done" ? "text-green/80" : "text-muted/60",
          )}
        />
        <span className="shrink-0 text-[12px] font-semibold text-foreground/75">
          {label}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted/65">
          {summary}
        </span>
        <motion.span
          aria-hidden="true"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={reduce ? { duration: 0 } : SPRING_SWAP}
          className="shrink-0 text-muted/50"
        >
          <ChevronDown className="size-3.5" />
        </motion.span>
      </button>
      <AgentDisclosure open={expanded}>
        <div className="border-l border-white/[0.08] px-3.5 py-2.5 text-[12px] leading-relaxed text-muted/70">
          {children}
        </div>
      </AgentDisclosure>
    </div>
  );
}

function SuggestionCard({
  suggestion,
  onDecided,
}: {
  suggestion: NonNullable<PlanDialogueView["messages"][0]["suggestion"]>;
  onDecided: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDecide = async (accept: boolean) => {
    setLoading(true);
    setError(null);
    try {
      await decideProposal(suggestion.proposal_id, { approved: accept, reason: accept ? undefined : "聊天中点击忽略" });
      onDecided();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("操作失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const isPending = suggestion.status === "pending";

  // 裁定后收起成一行摘要，不再占画面；点开可回看建议原文。
  if (!isPending) {
    return (
      <div className="mt-2 w-full max-w-[520px]">
        <CollapsedNote
          tone={suggestion.status === "accepted" ? "done" : "muted"}
          label={suggestion.status === "accepted" ? "已批准" : "已忽略"}
          summary={`计划改动建议 · ${suggestion.summary}`}
        >
          <p>{suggestion.summary}</p>
        </CollapsedNote>
      </div>
    );
  }

  return (
    <TiltCard max={5} className="mt-2 w-full max-w-[520px] rounded-2xl">
      {/* 卡面对齐 ApprovalCard：同款深色浮起，不要渐变亮边、光晕与扫光 */}
      <div className="relative overflow-hidden rounded-2xl border border-white/[0.06] bg-[#131316] px-5 py-4 shadow-[0_8px_32px_rgba(0,0,0,0.45)]">
        {error && (
          <div className="text-red-300 text-xs mb-3 bg-red-400/10 px-2 py-1 rounded">
            {error}
          </div>
        )}

        <div className="flex items-center gap-2.5 mb-3">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60"></span>
            <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500"></span>
          </span>
          {/* 状态是拍板依据，亮度不低于 white/70 一档 */}
          <h4 className="min-w-0 flex-1 truncate text-[15px] font-medium text-foreground/90">
            计划改动建议
          </h4>
          <span className="shrink-0 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-400">
            待批准
          </span>
        </div>

        <p className="mb-5 text-[14px] text-foreground/80 leading-relaxed">
          {suggestion.summary}
        </p>

        <div className="flex items-center gap-3">
          <button
            onClick={() => handleDecide(true)}
            disabled={loading}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-full bg-white px-4 py-2 text-[13px] font-medium text-black transition-colors hover:bg-white/85 disabled:opacity-50"
          >
            <Check className="w-3.5 h-3.5" />
            {loading ? "处理中..." : "确认"}
          </button>
          <button
            onClick={() => handleDecide(false)}
            disabled={loading}
            className="rounded-full px-4 py-2 text-[13px] font-medium text-muted/85 outline-none transition-colors hover:bg-white/[0.05] hover:text-rose-400 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            忽略
          </button>
        </div>

        <p className="mt-2.5 text-[11px] text-muted/70">
          确认后才会真的改动计划；忽略会记入台账。
        </p>
      </div>
    </TiltCard>
  );
}

// 回话的分段出现：一轮回执到了以后，开头几段按段放出，其余段落随后直接完整显示。
// 只做短时分段出现，不伪装逐字流式传输；reduce-motion 时跳过动画、整段直接全显。
// 历史消息不走动画——只有这一轮刚到的回话才 animate。
const STAGED_SEGMENTS = 4;
const SEGMENT_INTERVAL_MS = 280;

function ReplyStream({ content, animate }: { content: string; animate: boolean }) {
  const reduce = useReducedMotion();
  const paragraphs = useMemo(
    () => content.split("\n\n").filter((part) => part.trim().length > 0),
    [content],
  );
  const stagedCount = Math.min(STAGED_SEGMENTS, paragraphs.length);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!animate || reduce) return;
    const timer = window.setInterval(() => {
      setShown((current) => {
        if (current >= stagedCount) {
          window.clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, SEGMENT_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [animate, reduce, stagedCount]);

  // 前 stagedCount 段逐段出现；走到上限后剩余内容一次性完整显示，不拖慢长回答的阅读。
  const visible =
    animate && !reduce && shown < stagedCount ? shown : paragraphs.length;
  const done = visible >= paragraphs.length;

  return (
    <StreamingResponse
      status={done ? "complete" : "streaming"}
      copyText={content}
      announce={false}
      showActions={false}
      contentClassName="text-[15px] leading-[1.7] [&_p]:text-primary/80"
    >
      {paragraphs.slice(0, visible).map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
    </StreamingResponse>
  );
}

// 把问答卡的作答拼成送回模型的一句话：每问一行问题 + 一行回答。
// 它只进上下文，不再以用户聊天气泡的形态上屏（问答卡自己收起并展示结果）。
function composeAnswer(
  questions: DialogueQuestion[],
  answers: ApprovalCardAnswers,
): string {
  return questions
    .map((question, index) => {
      const answer = answers[`q-${index}`] ?? { selected: [], custom: "" };
      const chosen = answer.selected.join("、");
      const custom = (answer.custom ?? "").trim();
      const body = [chosen, custom].filter(Boolean).join("；");
      return `${question.title}\n答：${body || "（这题先跳过）"}`;
    })
    .join("\n");
}

// 只有问答卡提交器生成的那种「问题标题 + 答：」消息，才算回答了这张卡。
// 不能把卡片之后任意一条用户消息当成回答；用户也可能先继续聊别的事。
function isQuestionAnswer(
  content: string,
  questions: DialogueQuestion[],
): boolean {
  const answer = content.trim();
  return (
    questions.length > 0 &&
    questions.every((question) =>
      answer.includes(`${question.title}\n答：`),
    )
  );
}

// 从拼装回答里抽出每题的作答文本（「答：」后面的部分，按题序对应）。
function extractAnswerBodies(content: string): string[] {
  return content
    .split("\n")
    .filter((line) => line.startsWith("答："))
    .map((line) => line.slice(2).trim())
    .filter(Boolean);
}

// 回答型用户消息的轻量形态：不渲染成大白聊天气泡，渲染成右对齐的紧凑摘要条。
function AnsweredNote({
  questions,
  content,
}: {
  questions: DialogueQuestion[];
  content: string;
}) {
  const bodies = extractAnswerBodies(content);
  return (
    <div className="flex w-full justify-end">
      <div className="max-w-[80%] rounded-2xl border border-white/[0.05] bg-white/[0.04] px-4 py-2.5">
        <div className="mb-1.5 flex items-center justify-end gap-1.5 text-[11px] font-medium text-muted/55">
          <CornerDownLeft className="size-3" />
          回答了 cadence 的追问
        </div>
        <div className="flex flex-col gap-1">
          {questions.map((question, index) =>
            bodies[index] ? (
              <div
                key={index}
                className="flex flex-wrap items-baseline gap-x-2 text-[13px] leading-relaxed"
              >
                <span className="text-muted/60">{question.title}</span>
                <span className="text-foreground/85">{bodies[index]}</span>
              </div>
            ) : null,
          )}
        </div>
      </div>
    </div>
  );
}

function QuestionCard({
  questions,
  answered,
  onAnswer,
}: {
  questions: DialogueQuestion[];
  answered: boolean;
  /** 送回模型；resolve true = 已送达（卡收起），false = 失败（卡重开让用户重试）。 */
  onAnswer: (text: string) => Promise<boolean>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const collapsed = submitted || (answered && !submitting);

  const items: ApprovalCardQuestion[] = questions.map((question, index) => ({
    id: `q-${index}`,
    title: question.title,
    description: question.description ?? undefined,
    options: question.options.map((option) => ({ value: option, label: option })),
    multiple: question.multiple,
    allowCustom: question.allow_custom !== false,
    customPlaceholder: "还有别的想法…写在这里",
  }));

  const handleSubmit = async (answers: ApprovalCardAnswers) => {
    setSubmitting(true);
    setError(null);
    const ok = await onAnswer(composeAnswer(questions, answers));
    setSubmitting(false);
    if (ok) {
      setSubmitted(true);
    } else {
      setError("刚才没送出去，请再提交一次");
    }
  };

  // 回答过就收起锁定：不再占半屏、不能再点。
  if (collapsed) {
    return (
      <div className="mt-2 w-full max-w-[520px]">
        <CollapsedNote
          tone="done"
          label="已回答"
          summary={questions.map((question) => question.title).join(" · ")}
        >
          <div className="flex flex-col gap-1.5">
            {questions.map((question, index) => (
              <div key={index}>
                <p className="text-foreground/75">{question.title}</p>
                {question.options.length > 0 && (
                  <p className="mt-0.5 text-[11px] text-muted/55">
                    {question.options.join(" / ")}
                  </p>
                )}
              </div>
            ))}
            <p className="text-[11px] text-muted/55">回答已发给 cadence，见上面的紧凑条。</p>
          </div>
        </CollapsedNote>
      </div>
    );
  }

  return (
    <div className="mt-2 w-full max-w-[520px]">
      <TiltCard max={4} className="rounded-2xl">
        <ApprovalCard
          title="cadence 想先问清楚"
          questions={items}
          status={submitting ? "submitting" : "pending"}
          submitLabel="提交回答"
          onSubmit={handleSubmit}
        />
      </TiltCard>
      <p
        className={cn(
          "mt-2 text-[11px] leading-relaxed",
          error ? "text-red-300" : "text-muted/70",
        )}
      >
        {error ?? "回答会作为下一轮上下文发送，不会直接修改计划。"}
      </p>
    </div>
  );
}

// 运行依据：一格一格都有据可查——结局一行（成功显示「答完了」，异常显示原话）、
// 调用统计一行小字、下面逐条工具读取（读了什么、成没成）。折叠时只占一行。
function RunEvidence({
  run,
}: {
  run: NonNullable<PlanDialogueView["messages"][0]["run"]>;
}) {
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion() ?? false;
  const tools = run.tools;
  const ok = run.status === "ok";

  return (
    <div className="mt-1 w-full text-[13px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="group -ml-1 flex min-h-7 items-center gap-2 rounded-lg px-1 text-left text-muted/75 outline-none transition-colors hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
      >
        <BookOpenText className="size-3.5" />
        <span className="font-medium">运行依据</span>
        {tools.length > 0 && (
          <span className="rounded-full bg-white/[0.07] px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-muted/80">
            {tools.length}
          </span>
        )}
        <motion.span
          aria-hidden="true"
          animate={{ rotate: open ? 180 : 0 }}
          transition={reduce ? { duration: 0 } : SPRING_SWAP}
          className="text-muted/50"
        >
          <ChevronDown className="size-3" />
        </motion.span>
      </button>

      <AgentDisclosure open={open}>
        <div className="mt-1 flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-1.5 py-0.5">
            {ok ? (
              <>
                <Check className="size-3.5 shrink-0 text-green/70" />
                <span className="text-foreground/80">答完了</span>
              </>
            ) : (
              <>
                <Info className="size-3.5 shrink-0 text-amber-400/80" />
                <span className="text-foreground/80">{run.stop_reason}</span>
              </>
            )}
            <span className="text-[12px] text-muted/60">
              调模型 {run.model_calls} 次 · 读资料 {run.tool_calls} 次
            </span>
          </div>

          {tools.length > 0 && (
            <CitationList
              citations={tools.map((tool, index) => ({
                id: `tool-${index}`,
                title: tool.summary,
                icon: (
                  <Database
                    className={tool.ok ? "text-muted/70" : "text-red-300/70"}
                  />
                ),
              }))}
              className="px-0"
            />
          )}
        </div>
      </AgentDisclosure>
    </div>
  );
}

export function ChatFlow({
  dialogue,
  animateId,
  onRefresh,
  onAnswer,
}: {
  dialogue: PlanDialogueView | null;
  animateId: number | null;
  onRefresh: () => void;
  onAnswer: (text: string, opts?: { silent?: boolean }) => Promise<boolean>;
}) {
  if (!dialogue || dialogue.messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center h-full py-24">
        <div className="text-muted/75 mb-4 flex items-center gap-2 text-sm">
          <Sparkles className="w-4 h-4" />
          <span>没有任何对话记录</span>
        </div>
        <p className="text-muted/65 text-xs">在这里说出你想学什么或改动什么...</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center justify-center">
        <span className="text-[11px] font-medium text-muted/65 tracking-widest uppercase">
          {dialogue.messages.length > 0 &&
            new Date(dialogue.messages[0].created_at).toLocaleString("zh-CN", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
        </span>
      </div>

      {dialogue.messages.map((msg, idx) => {
        const answered =
          msg.questions !== null &&
          dialogue.messages
            .slice(idx + 1)
            .filter((later) => later.role === "user")
            .some((later) => isQuestionAnswer(later.content, msg.questions ?? []));

        if (msg.role === "user") {
          // 这条是不是在回答某张问答卡：向前找最近一条带追问的助手消息核对。
          const sourceQuestion = [...dialogue.messages.slice(0, idx)]
            .reverse()
            .find(
              (earlier) =>
                earlier.role === "assistant" &&
                earlier.questions &&
                isQuestionAnswer(msg.content, earlier.questions),
            );

          if (sourceQuestion?.questions) {
            return (
              <div key={msg.id} data-slot="message" data-from="user" className="w-full">
                <AnsweredNote questions={sourceQuestion.questions} content={msg.content} />
              </div>
            );
          }

          return (
            <div
              key={msg.id}
              data-slot="message"
              data-from="user"
              className="flex justify-end w-full"
            >
              <MessageBubble variant="solid" align="end" className="max-w-[80%]">
                <MessageBubbleContent
                  data-slot="message-content"
                  className="rounded-full px-5 py-3 text-[15px] leading-relaxed shadow-[0_4px_24px_rgba(255,255,255,0.07)]"
                >
                  {msg.content}
                </MessageBubbleContent>
              </MessageBubble>
            </div>
          );
        }

        return (
          <div
            key={msg.id}
            data-slot="message"
            data-from="assistant"
            className="flex flex-col gap-2 w-full max-w-[92%]"
          >
            <div data-slot="message-content">
              <ReplyStream content={msg.content} animate={msg.id === animateId} />
            </div>

            {msg.run && <RunEvidence run={msg.run} />}

            {msg.suggestion && (
              <div className="mt-1 w-full max-w-[520px]">
                <SuggestionCard
                  suggestion={msg.suggestion}
                  onDecided={onRefresh}
                />
              </div>
            )}

            {msg.questions && msg.questions.length > 0 && (
              <QuestionCard
                questions={msg.questions}
                answered={answered}
                onAnswer={(text) => onAnswer(text, { silent: true })}
              />
            )}
          </div>
        );
      })}
    </>
  );
}
