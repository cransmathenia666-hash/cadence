"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
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
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshPlans = async () => {
    try {
      const data = await listPlans(true);
      setPlans(data);
      setLoadError(null);
      return;
    } catch (cause) {
      setLoadError(messageOf(cause));
    }
  };

  useEffect(() => {
    let alive = true;
    listPlans(true)
      .then((data) => {
        if (!alive) return;
        setPlans(data);
        setLoadError(null);
        // 首次加载后自动选中：优先进行中的计划，否则第一个
        setSelectedPlanId((current) => {
          if (current !== null) return current;
          const active = data.find((p) => p.status === "active");
          return active ? active.id : (data[0]?.id ?? null);
        });
      })
      .catch((cause: unknown) => {
        if (alive) setLoadError(messageOf(cause));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{ plans, selectedPlanId, setSelectedPlanId, refreshPlans, loadError }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
