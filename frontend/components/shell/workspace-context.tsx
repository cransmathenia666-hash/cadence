"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useSearchParams } from "next/navigation";
import { ApiError, listPlans, type PlanSummary } from "@/lib/api";

type WorkspaceValue = {
  plans: PlanSummary[];
  selectedPlanId: number | null;
  setSelectedPlanId: (id: number | null) => void;
  refreshPlans: () => Promise<void>;
  loadError: string | null;
};

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace(): WorkspaceValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace 必须在 WorkspaceProvider 内使用");
  }
  return ctx;
}

function messageOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause);
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const searchParams = useSearchParams();
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [selectedPlanId, setSelectedPlanIdState] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const selectionInitializedRef = useRef(false);
  const appliedQueryPlanIdRef = useRef<string | null>(null);

  const requestedPlanId = searchParams.get("plan_id");
  const requestedPlanIdNumber = requestedPlanId === null ? null : Number(requestedPlanId);
  const hasRequestedPlanId =
    requestedPlanIdNumber !== null && Number.isInteger(requestedPlanIdNumber) && requestedPlanIdNumber > 0;

  const setSelectedPlanId = useCallback((id: number | null) => {
    setSelectedPlanIdState(id);
  }, []);

  const fallbackSelection = useCallback((data: PlanSummary[], current: number | null): number | null => {
    if (current !== null && data.some((plan) => plan.id === current)) return current;
    const active = data.find((plan) => plan.status === "active");
    return active?.id ?? data[0]?.id ?? null;
  }, []);

  const applyPlans = useCallback((data: PlanSummary[]) => {
    setPlans(data);
    setLoadError(null);
    setSelectedPlanIdState((current) => {
      const next = fallbackSelection(data, current);
      selectionInitializedRef.current = true;
      return next;
    });
  }, [fallbackSelection]);

  const refreshPlans = useCallback(async () => {
    try {
      applyPlans(await listPlans(true));
    } catch (cause) {
      setLoadError(messageOf(cause));
    }
  }, [applyPlans]);

  useEffect(() => {
    let alive = true;
    listPlans(true)
      .then((data) => {
        if (!alive) return;
        applyPlans(data);
      })
      .catch((cause: unknown) => {
        if (alive) setLoadError(messageOf(cause));
      });
    return () => {
      alive = false;
    };
  }, [applyPlans]);

  useEffect(() => {
    if (!selectionInitializedRef.current || plans.length === 0) return;
    setSelectedPlanIdState((current) => {
      if (appliedQueryPlanIdRef.current !== requestedPlanId) {
        appliedQueryPlanIdRef.current = requestedPlanId;
        const requested = hasRequestedPlanId ? requestedPlanIdNumber : null;
        if (requested !== null && plans.some((plan) => plan.id === requested)) return requested;
      }
      return fallbackSelection(plans, current);
    });
  }, [fallbackSelection, hasRequestedPlanId, plans, requestedPlanId, requestedPlanIdNumber]);

  return (
    <WorkspaceContext.Provider
      value={{ plans, selectedPlanId, setSelectedPlanId, refreshPlans, loadError }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
