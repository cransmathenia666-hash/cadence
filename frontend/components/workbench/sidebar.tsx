import { PlanSummary } from "@/lib/api";
import { CheckCircle2, Plus } from "lucide-react";
import Link from "next/link";

export function Sidebar({
  plans,
  selectedPlanId,
  onSelect,
}: {
  plans: PlanSummary[];
  selectedPlanId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <aside className="w-[230px] border-r border-white/[0.04] bg-[#08080a] pt-20 pb-6 flex flex-col h-full z-20 hidden md:flex shrink-0">
      <div className="px-5 mb-4 flex items-center justify-between">
        <h2 className="text-[11px] font-semibold text-muted/50 tracking-widest uppercase">
          Plans
        </h2>
        {/* TODO: 点击新建任务的真实交互在本轮以外 */}
        <Link
          href="/new"
          className="text-[11px] text-muted/60 hover:text-white/90 transition-colors flex items-center gap-1 cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          新建
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-hide px-3 space-y-1">
        {plans.map((plan) => {
          const isSelected = plan.id === selectedPlanId;
          const isDone = plan.status === "closed";
          const isVoid = plan.status === "void";
          const isActive = plan.status === "active";
          const isWeak = isDone || isVoid;
          
          let progress = 0;
          if (plan.stages > 0) {
            progress = Math.round((plan.stages_finished / plan.stages) * 100);
          }

          if (isSelected) {
            return (
              <div
                key={plan.id}
                onClick={() => onSelect(plan.id)}
                className="group relative bg-white/[0.03] rounded-lg p-3.5 cursor-pointer border border-white/[0.06] shadow-sm"
              >
                <div className="absolute left-0 top-3.5 bottom-3.5 w-[3px] bg-white rounded-r-full shadow-[0_0_12px_rgba(255,255,255,0.9)]"></div>
                <h3 className="text-[13px] font-medium text-primary leading-snug mb-2.5 pr-1">
                  {plan.goal}
                </h3>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-muted/80 font-medium">
                    {isActive ? "进行中" : plan.status}
                  </span>
                  <div className="w-20 bg-black/50 rounded-full h-1.5 border border-white/[0.04] overflow-hidden">
                    <div
                      className="bg-white/80 h-full rounded-full"
                      style={{ width: `${progress}%` }}
                    ></div>
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div
              key={plan.id}
              onClick={() => onSelect(plan.id)}
              className="group relative rounded-lg p-3.5 cursor-pointer hover:bg-white/[0.02] transition-colors border border-transparent"
            >
              <h3
                className={`text-[13px] font-medium transition-colors leading-snug line-clamp-2 ${
                  isWeak
                    ? "text-muted/40 group-hover:text-muted/70"
                    : "text-muted/60 group-hover:text-muted/90"
                }`}
              >
                {plan.goal}
              </h3>
              <div className="flex items-center justify-between mt-2 opacity-40 group-hover:opacity-60 transition-opacity">
                <span className="text-[11px]">
                  {isActive ? "进行中" : plan.status}
                </span>
                {isDone && <CheckCircle2 className="w-3.5 h-3.5" />}
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
