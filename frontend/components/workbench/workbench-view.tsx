import { useEffect, useState } from "react";
import { listPlans, PlanSummary, getPlanDialogue, PlanDialogueView, ApiError } from "@/lib/api";
import { Sidebar } from "./sidebar";
import { ChatFlow } from "./chat-flow";
import { ChatInput } from "./chat-input";
import { Topbar } from "./topbar";

export function WorkbenchView() {
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null);
  const [dialogue, setDialogue] = useState<PlanDialogueView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDialogue = (planId: number) => {
    getPlanDialogue(planId)
      .then((data) => setDialogue(data))
      .catch((err) => {
        if (err instanceof ApiError) {
          setError(err.message);
        }
      });
  };

  useEffect(() => {
    listPlans(true)
      .then((data) => {
        setPlans(data);
        const activePlan = data.find((p) => p.status === "active");
        if (activePlan) {
          setSelectedPlanId(activePlan.id);
        } else if (data.length > 0) {
          setSelectedPlanId(data[0].id);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError(String(err));
        }
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (selectedPlanId) {
      fetchDialogue(selectedPlanId);
    } else {
      Promise.resolve().then(() => setDialogue(null));
    }
  }, [selectedPlanId]);

  const selectedPlan = plans.find((p) => p.id === selectedPlanId);

  return (
    <>
      <Topbar planName={selectedPlan?.goal} />
      <Sidebar
        plans={plans}
        selectedPlanId={selectedPlanId}
        onSelect={setSelectedPlanId}
      />
      <main className="flex-1 flex flex-col relative h-full bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background">
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
      </main>
    </>
  );
}
