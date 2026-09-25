"use client";

import { useEffect, useState } from "react";
import { getPlanDialogue, PlanDialogueView, ApiError } from "@/lib/api";
import { ChatFlow } from "./chat-flow";
import { ChatInput } from "./chat-input";
import { useWorkspace } from "@/components/shell/workspace-context";

// 工作台主区的对话视图：计划列表与顶栏由外壳提供，这里只管对话流。
export function WorkbenchChat() {
  const { selectedPlanId } = useWorkspace();
  const [dialogue, setDialogue] = useState<PlanDialogueView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
  }, [selectedPlanId]);

  return (
    <>
      <div className="flex-1 overflow-y-auto scrollbar-hide pt-24 pb-64 px-4 md:px-12 w-full max-w-[56rem] mx-auto flex flex-col gap-8">
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
          <ChatFlow dialogue={dialogue} onRefresh={() => fetchDialogue(selectedPlanId)} />
        )}
      </div>

      {selectedPlanId && (
        <ChatInput
          planId={selectedPlanId}
          onTurnAdded={() => fetchDialogue(selectedPlanId)}
          onError={setError}
        />
      )}
    </>
  );
}
