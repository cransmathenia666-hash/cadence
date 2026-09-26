import { useState } from "react";
import { Check, X, ChevronDown } from "lucide-react";
import { PathStep } from "@/lib/api";

const KIND_LABELS: Record<string, string> = {
  concept: "概念",
  doc: "资料",
  project: "项目",
  course: "课程",
};

export function CandidateCard({
  row,
  plans,
  notes,
  isDecided,
  verdicting,
  rejectingId,
  setRejectingId,
  rejectReason,
  setRejectReason,
  adoptingId,
  setAdoptingId,
  onVerdict,
  onAdoptIntoNewPlan,
  setChattingId,
}: {
  row: {
    id: number;
    title: string;
    kind: string;
    why: string;
    depthTarget: string;
    isRecommended: boolean;
    status: string;
    rejectReason: string | null;
    planId: number | null;
    shape: string;
    steps: PathStep[];
  };
  plans: { id: number; goal: string }[];
  notes: Record<number, string>;
  isDecided: boolean;
  verdicting: boolean;
  rejectingId: number | null;
  setRejectingId: (id: number | null) => void;
  rejectReason: string;
  setRejectReason: (val: string) => void;
  adoptingId: number | null;
  setAdoptingId: (id: number | null) => void;
  onVerdict: (id: number, accept: boolean, reason?: string, planId?: number) => void;
  onAdoptIntoNewPlan: (id: number, title: string, goal: string) => void;
}) {
  const isRowPath = row.shape === "path";
  const [expanded, setExpanded] = useState(row.isRecommended || isRowPath || row.status === "proposed");
  const [adoptMode, setAdoptMode] = useState<"existing" | "new">("existing");
  const [adoptPlanChoice, setAdoptPlanChoice] = useState("");
  const [newPlanGoal, setNewPlanGoal] = useState("");

  const handleAdoptClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (row.planId) {
      onVerdict(row.id, true);
      return;
    }
    setAdoptMode("existing");
    setAdoptPlanChoice("");
    setNewPlanGoal(row.title);
    setAdoptingId(row.id);
  };

  const handleRejectClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setRejectingId(row.id);
    setRejectReason("");
    setAdoptingId(null);
  };

  return (
    <div 
      className={`relative overflow-hidden transition-colors border border-white/[0.04] rounded-2xl mb-3 ${
        row.isRecommended 
          ? 'bg-surface2/60 before:absolute before:left-0 before:top-4 before:bottom-4 before:w-[3px] before:bg-white before:rounded-r-full' 
          : 'bg-surface2/30 hover:bg-surface2/50'
      }`}
    >
      {/* Row Header */}
      <div 
        className={`flex items-center justify-between px-4 py-4 cursor-pointer select-none ${row.isRecommended ? 'pl-5' : ''}`}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3 pr-4">
          {/* Status Dot */}
          {row.status === "accepted" ? (
            <div className="w-5 h-5 rounded-full bg-emerald-500/20 flex items-center justify-center shrink-0">
              <Check className="w-3 h-3 text-emerald-500" />
            </div>
          ) : row.status === "rejected" ? (
            <div className="w-5 h-5 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
              <X className="w-3 h-3 text-red-500" />
            </div>
          ) : row.status === "expired" ? (
            <div className="w-5 h-5 rounded-full bg-white/10 flex items-center justify-center shrink-0">
              <div className="w-1.5 h-1.5 rounded-full bg-white/30" />
            </div>
          ) : (
            <div className="w-5 h-5 rounded-full border border-amber-500/30 flex items-center justify-center shrink-0 relative">
              <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              <div className="absolute inset-0 rounded-full border border-amber-500 animate-ping opacity-20" />
            </div>
          )}
          <span className="text-[12px] font-medium text-white/40 tracking-wider hidden sm:block shrink-0">
            {KIND_LABELS[row.kind] ?? row.kind}
          </span>
          <div className="text-[15px] font-medium text-primary/90 truncate">
            {row.title}
          </div>
        </div>
        
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-[12px] text-white/40 hidden md:block">
            深度 <span className="text-white/70 ml-1">{row.depthTarget}</span> <span className="mx-2 opacity-30">|</span> #{row.id}
          </div>
          
          {/* Status Badge */}
          {row.status === "accepted" && <span className="text-[11px] font-medium text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-full border border-emerald-500/20 uppercase tracking-widest hidden sm:block">Completed</span>}
          {row.status === "rejected" && <span className="text-[11px] font-medium text-red-400 bg-red-500/10 px-2.5 py-1 rounded-full border border-red-500/20 uppercase tracking-widest hidden sm:block">Rejected</span>}
          {row.status === "proposed" && <span className="text-[11px] font-medium text-amber-500 bg-amber-500/10 px-2.5 py-1 rounded-full border border-amber-500/20 uppercase tracking-widest hidden sm:block">Pending</span>}
          {row.status === "expired" && <span className="text-[11px] font-medium text-white/40 bg-white/5 px-2.5 py-1 rounded-full border border-white/10 uppercase tracking-widest hidden sm:block">Expired</span>}
          
          <ChevronDown className={`w-4 h-4 text-white/40 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </div>

      {/* Accordion Details */}
      <div 
        className="grid transition-all duration-200 ease-in-out" 
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-5 pt-1 border-t border-white/[0.04] mx-4">
            <p className="text-[14px] text-white/60 leading-relaxed mb-5 pl-8 mt-4">
              {row.why}
            </p>

            {isRowPath && row.steps.length > 0 && (
              <div className="pl-8 mb-6">
                <div className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35] mb-4">路径草案</div>
                <ol className="space-y-4">
                  {row.steps.map((step, idx) => (
                    <li key={idx} className="flex gap-3 text-[13px]">
                      <span className="text-white/30 font-medium shrink-0 w-4">{idx + 1}.</span>
                      <div>
                        <span className="text-primary/80">{step.title}</span>
                        {step.deliverable && <span className="ml-2 px-2 py-0.5 bg-white/[0.04] text-white/50 text-[11px] rounded border border-white/[0.04]">交: {step.deliverable}</span>}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {/* Actions / Verdicts */}
            <div className="pl-8">
              {isDecided ? (
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="text-[12px] text-white/50 bg-white/[0.02] p-3 rounded-xl border border-white/[0.04] inline-block">
                    {notes[row.id] ?? "该候选已有最终结论"}
                  </div>
                  {row.status === "accepted" && (
                    <button
                      onClick={() => setChattingId(row.id)}
                      className="px-4 py-2 rounded-full border border-white/[0.12] text-[13px] text-white/80 hover:border-white/[0.3] hover:text-white transition-colors"
                    >
                      进入规划对话
                    </button>
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {!adoptingId && !rejectingId && (
                    <div className="flex items-center gap-3">
                      <button 
                        onClick={handleAdoptClick} 
                        disabled={verdicting} 
                        className="px-6 py-2.5 bg-white text-black hover:bg-gray-200 rounded-full text-[13px] font-medium transition-colors disabled:opacity-50"
                      >
                        {row.planId ? "采纳" : isRowPath ? "采纳整条路" : "采纳"}
                      </button>
                      <button 
                        onClick={handleRejectClick} 
                        disabled={verdicting} 
                        className="px-6 py-2.5 border border-white/[0.08] text-white/60 hover:text-white rounded-full text-[13px] font-medium transition-colors hover:bg-white/[0.02] disabled:opacity-50"
                      >
                        否决
                      </button>
                    </div>
                  )}

                  {/* Inline Approval Bar */}
                  {adoptingId === row.id && (
                    <div className="flex flex-wrap items-center gap-4 bg-[#141416] p-3 rounded-2xl border border-white/[0.06] w-fit" onClick={e => e.stopPropagation()}>
                      <div className="text-[13px] text-white/60 font-medium whitespace-nowrap pl-2">落入哪个计划？</div>
                      
                      <div className="flex gap-1 bg-white/[0.04] p-1 rounded-full shrink-0">
                        <button 
                          onClick={() => setAdoptMode("existing")} 
                          className={`px-4 py-1.5 rounded-full text-[12px] font-medium transition-colors ${adoptMode === "existing" ? "bg-white/[0.08] text-white" : "text-white/40 hover:text-white/80"}`}
                        >
                          现有
                        </button>
                        <button 
                          onClick={() => setAdoptMode("new")} 
                          className={`px-4 py-1.5 rounded-full text-[12px] font-medium transition-colors ${adoptMode === "new" ? "bg-white/[0.08] text-white" : "text-white/40 hover:text-white/80"}`}
                        >
                          新建
                        </button>
                      </div>

                      {adoptMode === "existing" ? (
                        <select 
                          value={adoptPlanChoice} 
                          onChange={e => setAdoptPlanChoice(e.target.value)} 
                          className="appearance-none bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none flex-1 min-w-[160px] cursor-pointer hover:border-white/[0.15]"
                        >
                          <option value="">选择现有计划...</option>
                          {plans.map(p => <option key={p.id} value={p.id}>#{p.id} {p.goal}</option>)}
                        </select>
                      ) : (
                        <input 
                          value={newPlanGoal} 
                          onChange={e => setNewPlanGoal(e.target.value)} 
                          placeholder="新计划目标 (选填)..." 
                          className="bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none flex-1 min-w-[160px] focus:border-white/[0.15]" 
                        />
                      )}

                      <button 
                        onClick={() => adoptMode === "existing" ? onVerdict(row.id, true, undefined, Number(adoptPlanChoice)) : onAdoptIntoNewPlan(row.id, row.title, newPlanGoal)} 
                        disabled={verdicting || (adoptMode === "existing" && !adoptPlanChoice)} 
                        className="px-5 py-2 bg-white text-black rounded-full text-[13px] font-medium shrink-0 disabled:opacity-50 hover:bg-gray-200 transition-colors"
                      >
                        确认
                      </button>
                      <button 
                        onClick={() => setAdoptingId(null)} 
                        className="p-2 text-white/40 hover:text-white/80 transition-colors ml-auto mr-1"
                      >
                        <X className="w-4 h-4"/>
                      </button>
                    </div>
                  )}

                  {rejectingId === row.id && (
                    <div className="flex flex-wrap items-center gap-3 bg-[#141416] p-3 rounded-2xl border border-white/[0.06] w-fit" onClick={e => e.stopPropagation()}>
                      <div className="text-[13px] text-white/60 font-medium pl-2">否决理由:</div>
                      <input 
                        value={rejectReason} 
                        onChange={e => setRejectReason(e.target.value)} 
                        placeholder="必填，将进永久禁区" 
                        className="bg-black/40 border border-white/[0.08] rounded-full px-4 py-2 text-[13px] text-primary/90 outline-none w-[240px] focus:border-white/[0.15]" 
                      />
                      <button 
                        onClick={() => onVerdict(row.id, false, rejectReason)} 
                        disabled={verdicting || !rejectReason.trim()} 
                        className="px-5 py-2 bg-red-500/20 text-red-400 rounded-full text-[13px] font-medium disabled:opacity-50 hover:bg-red-500/30 transition-colors"
                      >
                        确认否决
                      </button>
                      <button 
                        onClick={() => setRejectingId(null)} 
                        className="p-2 text-white/40 hover:text-white/80 transition-colors"
                      >
                        <X className="w-4 h-4"/>
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
