"use client";

import { useCallback, useEffect, useState } from "react";
import {
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
import { Sparkles } from "lucide-react";
import { ChatFlow } from "./chat-flow";
import { ChatInput } from "./chat-input";
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
  // 这一轮刚到的回话要流式展开（记它的行号；刷新/裁定后的重取不会触发）
  const [animateId, setAnimateId] = useState<number | null>(null);

  const fetchDialogue = (planId: number) => {
    getPlanDialogue(planId)
      .then((data) => {
        setDialogue(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError) {
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (selectedPlanId) {
      fetchDialogue(selectedPlanId);
    } else {
      Promise.resolve().then(() => {
        setDialogue(null);
        setLoading(false);
      });
    }
    // 换计划：上一轮的流式展开、乐观气泡与思考态都不属于新计划
    Promise.resolve().then(() => {
      setAnimateId(null);
      setPending(false);
      setOptimistic(null);
    });
  }, [selectedPlanId]);

  // 发送一句（输入框与问答卡共用这一条路）：乐观上屏 → 等回执 → 重取并流式展开回话。
  // 失败时把原话还回输入框——那句话没有被记进对话。
  const send = useCallback(
    (text: string) => {
      const content = text.trim();
      if (!selectedPlanId || !content || pending) return;
      setPending(true);
      setOptimistic(content);
      setDraft("");
      setError(null);
      sayPlanDialogue(selectedPlanId, content)
        .then(() => getPlanDialogue(selectedPlanId))
        .then((data) => {
          setDialogue(data);
          const last = [...data.messages]
            .reverse()
            .find((message) => message.role === "assistant");
          if (last) setAnimateId(last.id);
        })
        .catch((err) => {
          setError(err instanceof ApiError ? err.message : "发送失败");
          setDraft(content);
        })
        .finally(() => {
          setPending(false);
          setOptimistic(null);
        });
    },
    [selectedPlanId, pending],
  );

  return (
    <>
      <div className="flex-1 min-h-0 w-full max-w-[56rem] mx-auto px-4 md:px-12 flex flex-col">
        <MessageScroller
          navigation="rail"
          navigationLabel="跳到某一句"
          label="计划对话"
          busy={pending}
          className="flex-1 min-h-0"
          viewportClassName="pt-24"
          contentClassName="flex flex-col gap-8 pb-64"
        >
          {error ? (
            <div className="p-8 text-red-400 bg-red-400/10 rounded-lg text-sm">{error}</div>
          ) : loading ? (
            <div className="p-8 text-muted/50 text-sm flex items-center gap-2">
              <span className="w-4 h-4 rounded-full border-2 border-muted/30 border-t-muted animate-spin"></span>
              加载数据中...
            </div>
          ) : !selectedPlanId ? (
            <div className="p-8 text-muted/50 text-sm">没有可用的计划</div>
          ) : (
            <ChatFlow
              dialogue={dialogue}
              animateId={animateId}
              onRefresh={() => fetchDialogue(selectedPlanId)}
              onAnswer={send}
            />
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
              <div className="flex items-center gap-3 mb-1">
                <div className="w-7 h-7 rounded-full bg-white/[0.08] border border-white/10 flex items-center justify-center shadow-[0_0_16px_rgba(255,255,255,0.12)] backdrop-blur-sm">
                  <Sparkles className="w-4 h-4 text-white/90" />
                </div>
                <span className="text-[14px] font-semibold text-primary/90 tracking-wide">
                  cadence
                </span>
              </div>
              <div className="pl-10 flex flex-col gap-2.5">
                <AgentProgress label="在读计划、报告和档案" />
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
        />
      )}
    </>
  );
}
