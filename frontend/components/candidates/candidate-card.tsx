import { useState } from "react";
import { Check, X, Plus, ChevronDown, ListTree, Route } from "lucide-react";
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
  chattingId,
  setChattingId,
  renderChatBox,
  isBigCard,
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
  chattingId: number | null;
  setChattingId: (id: number | null) => void;
  renderChatBox: (rowId: number) => React.ReactNode;
  isBigCard?: boolean;
}) {
  const isRowPath = row.shape === "path";
  const [adoptMode, setAdoptMode] = useState<"existing" | "new">("existing");
  const [adoptPlanChoice, setAdoptPlanChoice] = useState("");
  const [newPlanGoal, setNewPlanGoal] = useState("");

  const statusColor = 
    row.status === "accepted" ? "bg-emerald-500" :
    row.status === "rejected" ? "bg-red-500" :
    row.status === "expired" ? "bg-muted" : "bg-amber-500";

  const statusText = 
    row.status === "accepted" ? "已采纳" :
    row.status === "rejected" ? "已否决" :
    row.status === "expired" ? "已过期" : "待裁定";

  if (!isBigCard) {
    return (
      <div className="relative overflow-hidden group/row py-5 border-b border-white/[0.04] transition-colors hover:bg-white/[0.01] px-2 -mx-2 rounded-xl">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0 pr-4">
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="relative flex h-2 w-2 mr-1">
                 {row.status === "proposed" && (
                   <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60"></span>
                 )}
                 <span className={`relative inline-flex rounded-full h-2 w-2 ${statusColor}`}></span>
              </span>
              <span className="px-2.5 py-0.5 bg-white/[0.04] rounded-full text-[11px] font-medium text-white/[0.35] tracking-wide">
                {KIND_LABELS[row.kind] ?? row.kind}
              </span>
              <h3 className="text-[15px] font-medium text-primary/90 ml-1 truncate">
                {row.title}
              </h3>
            </div>
            <p className="text-[14px] text-white/60 leading-relaxed line-clamp-2 pl-3 ml-3 border-l border-white/[0.08]">
              {row.why}
            </p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-white/[0.35] mt-3 pl-6">
              <span className="flex items-center gap-1">
                建议深度 <strong className="text-white/60 font-medium ml-1">{row.depthTarget}</strong>
              </span>
              {row.planId && (
                <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-white/[0.15]">
                  所属计划 <strong className="text-white/60 font-medium ml-1">#{row.planId}</strong>
                </span>
              )}
              {row.rejectReason && (
                <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-white/[0.15] text-red-400/80">
                  否决理由: {row.rejectReason}
                </span>
              )}
            </div>
          </div>
          
          <div className="shrink-0 flex flex-col items-end gap-2 mt-1">
            {isDecided ? (
              <div className="text-[11px] text-white/[0.35] font-medium flex items-center gap-1.5 mr-2">
                <Check className="w-3 h-3" />
                {statusText}
              </div>
            ) : (
              !rejectingId && !adoptingId && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      if (row.planId !== null) {
                        onVerdict(row.id, true);
                        return;
                      }
                      setAdoptMode("existing");
                      setAdoptPlanChoice("");
                      setNewPlanGoal(row.title);
                      setAdoptingId(row.id);
                    }}
                    disabled={verdicting}
                    className="px-4 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-full text-[12px] font-medium flex items-center gap-1.5 transition-colors disabled:opacity-50"
                  >
                    <Check className="w-3.5 h-3.5 opacity-80" />
                    采纳
                  </button>
                  <button
                    onClick={() => {
                      setRejectingId(row.id);
                      setRejectReason("");
                      setAdoptingId(null);
                    }}
                    disabled={verdicting}
                    className="p-1.5 text-white/40 hover:text-white/80 hover:bg-white/5 rounded-full transition-colors disabled:opacity-50"
                    title="否决"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )
            )}
          </div>
        </div>

        {/* Inline Actions for Row */}
        {!isDecided && adoptingId === row.id && (
          <div className="mt-4 bg-black/20 rounded-xl border border-white/[0.04] p-3 pl-6">
            <div className="text-[12px] font-medium text-primary/80 mb-3">落入哪个计划？</div>
            <div className="flex items-center gap-2 p-1 bg-white/[0.04] rounded-lg w-fit mb-3">
              <button
                onClick={() => setAdoptMode("existing")}
                className={`px-3 py-1 rounded-[6px] text-[11px] font-medium transition-colors ${
                  adoptMode === "existing" ? "bg-white/[0.08] text-primary" : "text-white/40 hover:text-white/80"
                }`}
              >
                现有计划
              </button>
              <button
                onClick={() => setAdoptMode("new")}
                className={`px-3 py-1 rounded-[6px] text-[11px] font-medium transition-colors ${
                  adoptMode === "new" ? "bg-white/[0.08] text-primary" : "text-white/40 hover:text-white/80"
                }`}
              >
                新建计划
              </button>
            </div>
            {adoptMode === "existing" ? (
              <div className="flex items-center gap-2">
                <select
                  value={adoptPlanChoice}
                  onChange={(e) => setAdoptPlanChoice(e.target.value)}
                  className="w-[200px] appearance-none bg-black/40 border border-white/[0.08] rounded-lg px-3 py-1.5 text-[12px] text-primary/90 hover:border-white/[0.15] outline-none"
                >
                  <option value="">选择...</option>
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>#{p.id} {p.goal}</option>
                  ))}
                </select>
                <button
                  onClick={() => onVerdict(row.id, true, undefined, Number(adoptPlanChoice))}
                  disabled={verdicting || !adoptPlanChoice}
                  className="px-3 py-1.5 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 rounded-lg text-[12px] font-medium transition-colors disabled:opacity-50"
                >
                  确认归入
                </button>
                <button onClick={() => setAdoptingId(null)} className="p-1 text-white/40 hover:text-white/80 transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <input
                  value={newPlanGoal}
                  onChange={(e) => setNewPlanGoal(e.target.value)}
                  placeholder="新计划目标..."
                  className="w-[200px] bg-black/40 border border-white/[0.08] rounded-lg px-3 py-1.5 text-[12px] text-primary/90 focus:border-white/[0.15] outline-none"
                  disabled={verdicting}
                />
                <button
                  onClick={() => onAdoptIntoNewPlan(row.id, row.title, newPlanGoal)}
                  disabled={verdicting}
                  className="px-3 py-1.5 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 rounded-lg text-[12px] font-medium flex items-center gap-1.5 transition-colors disabled:opacity-50"
                >
                  <Plus className="w-3 h-3" />
                  {verdicting ? "建…" : "新建并采纳"}
                </button>
                <button onClick={() => setAdoptingId(null)} className="p-1 text-white/40 hover:text-white/80 transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
        {!isDecided && rejectingId === row.id && (
          <div className="mt-4 flex items-center gap-2 bg-black/20 p-1.5 rounded-full border border-white/[0.04] w-fit ml-6">
            <input
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="否决理由 (进永久禁区)"
              className="bg-transparent border-none text-[12px] text-primary w-[200px] px-3 focus:outline-none focus:ring-0"
            />
            <button
              onClick={() => onVerdict(row.id, false, rejectReason)}
              disabled={verdicting || !rejectReason.trim()}
              className="px-3 py-1 bg-red-500/20 text-red-400 rounded-full text-[11px] font-medium hover:bg-red-500/30 transition-colors disabled:opacity-50"
            >
              确认否决
            </button>
            <button
              onClick={() => setRejectingId(null)}
              disabled={verdicting}
              className="p-1 text-white/40 hover:text-white/80 transition-colors rounded-full"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
    );
  }

  // Big Card Mode
  return (
    <div className="card-border-gradient shadow-card-glow relative overflow-hidden group/card rounded-2xl bg-surface2/80 p-6 md:p-8 mb-6 transition-all">
      <div className="flex items-start justify-between mb-5">
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <span className="uppercase tracking-widest text-[11px] font-semibold text-white/[0.35]">
            {KIND_LABELS[row.kind] ?? row.kind}
          </span>
          {isRowPath ? (
            <span className="px-2.5 py-1 bg-blue-500/10 text-blue-400 rounded-full text-[11px] font-medium tracking-wide flex items-center gap-1.5">
              <Route className="w-3 h-3" />
              一条路
            </span>
          ) : (
            <span className="px-2.5 py-1 bg-purple-500/10 text-purple-400 rounded-full text-[11px] font-medium tracking-wide flex items-center gap-1.5">
              <ListTree className="w-3 h-3" />
              几个方向
            </span>
          )}
          {row.isRecommended && (
            <span className="px-2.5 py-1 bg-amber-500/10 text-amber-500 rounded-full text-[11px] font-medium tracking-wide">
              建议优先从这开始
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0 ml-4">
          <span className="relative flex h-2 w-2">
            {row.status === "proposed" && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60"></span>
            )}
            <span className={`relative inline-flex rounded-full h-2 w-2 ${statusColor}`}></span>
          </span>
          <span className="text-[11px] font-semibold text-white/40 tracking-wide uppercase">
            {statusText}
          </span>
        </div>
      </div>

      <h3 className="text-[16px] md:text-[18px] font-medium text-primary/90 mb-3 leading-snug">
        {row.title}
      </h3>

      <p className="text-[15px] text-white/60 leading-relaxed mb-6">
        {row.why}
      </p>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-white/[0.35] mb-6">
        <span className="flex items-center gap-1">
          建议深度 <strong className="text-white/60 font-medium ml-1">{row.depthTarget}</strong>
        </span>
        {row.planId && (
          <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-white/[0.15]">
            所属计划 <strong className="text-white/60 font-medium ml-1">#{row.planId}</strong>
          </span>
        )}
        {row.rejectReason && (
          <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-white/[0.15] text-red-400/80">
            否决理由: {row.rejectReason}
          </span>
        )}
      </div>

      {isRowPath && row.steps.length > 0 && (
        <div className="bg-black/20 border border-white/[0.04] rounded-xl p-5 mb-6">
          <div className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35] mb-4">
            这条路上的先后步骤（草案）
          </div>
          <ol className="space-y-4 pl-1">
            {row.steps.map((step: PathStep, index: number) => (
              <li key={index} className="text-[14px]">
                <div className="flex items-center gap-3 mb-1.5">
                  <span className="w-5 h-5 rounded-full bg-white/[0.06] flex items-center justify-center text-[10px] text-white/60 font-medium shrink-0">
                    {index + 1}
                  </span>
                  <strong className="text-primary/80 font-medium">{step.title}</strong>
                  {step.deliverable && (
                    <span className="text-[11px] text-emerald-400/80 px-2 py-0.5 rounded-full bg-emerald-500/10 ml-2 border border-emerald-500/20">
                      要交: {step.deliverable}
                    </span>
                  )}
                </div>
                {step.why && (
                  <div className="text-[13px] text-white/50 pl-8 mt-1 leading-relaxed">
                    {step.why}
                  </div>
                )}
              </li>
            ))}
          </ol>
          <div className="text-[11px] text-white/[0.35] mt-5 pl-1">
            采纳就是「这条路我走」。哪一步这轮不建，在出蓝图时不勾就行；建出来之后不想要，在计划页「跳过这一步」——两步都不进永久禁区。
          </div>
        </div>
      )}

      {isDecided ? (
        <div className="mt-5 pt-5 border-t border-white/[0.04]">
          <div className="text-[12px] text-white/50 font-medium flex items-center gap-2">
            <Check className="w-3.5 h-3.5" />
            {notes[row.id] ?? "该候选已有最终结论"}
          </div>
        </div>
      ) : (
        <div className="mt-6 pt-6 border-t border-white/[0.04] flex flex-wrap items-center gap-3">
          <button
            onClick={() => {
              if (row.planId !== null) {
                onVerdict(row.id, true);
                return;
              }
              setAdoptMode("existing");
              setAdoptPlanChoice("");
              setNewPlanGoal(row.title);
              setAdoptingId(row.id);
            }}
            disabled={verdicting}
            className="px-6 py-2.5 bg-white text-black hover:bg-gray-200 rounded-full text-[13px] font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
          >
            <Check className="w-4 h-4" />
            {row.planId === null ? "采纳 (选择归属计划)" : isRowPath ? "采纳整条路" : "采纳 (落入计划)"}
          </button>

          {rejectingId === row.id ? (
            <div className="flex items-center gap-2 bg-black/40 p-1.5 rounded-full border border-white/[0.08]">
              <input
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder={isRowPath ? "否决整条路的理由 (必填)" : "否决理由 (进永久禁区)"}
                className="bg-transparent border-none text-[13px] text-primary w-[220px] px-4 py-1 focus:outline-none focus:ring-0"
              />
              <button
                onClick={() => onVerdict(row.id, false, rejectReason)}
                disabled={verdicting || !rejectReason.trim()}
                className="px-4 py-1.5 bg-red-500/20 text-red-400 rounded-full text-[12px] font-medium hover:bg-red-500/30 transition-colors disabled:opacity-50"
              >
                确认否决
              </button>
              <button
                onClick={() => setRejectingId(null)}
                disabled={verdicting}
                className="p-2 text-white/40 hover:text-white/80 transition-colors rounded-full"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => {
                setRejectingId(row.id);
                setRejectReason("");
                setAdoptingId(null);
              }}
              disabled={verdicting}
              className="px-6 py-2.5 bg-white/[0.04] hover:bg-white/[0.08] text-white/80 rounded-full text-[13px] font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
            >
              <X className="w-4 h-4" />
              {isRowPath ? "否决整条路" : "否决"}
            </button>
          )}

          {adoptingId === row.id && (
            <div className="w-full mt-5 bg-black/30 rounded-2xl border border-white/[0.06] p-5 flex flex-col gap-5">
              <div className="text-[13px] font-medium text-primary/80">指定该候选采纳后要落到哪个计划：</div>
              <div className="flex items-center gap-2 p-1 bg-white/[0.04] rounded-xl w-fit">
                <button
                  onClick={() => setAdoptMode("existing")}
                  className={`px-4 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                    adoptMode === "existing" ? "bg-white/[0.08] text-primary shadow-sm" : "text-white/40 hover:text-white/80"
                  }`}
                >
                  选一个现有计划
                </button>
                <button
                  onClick={() => setAdoptMode("new")}
                  className={`px-4 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                    adoptMode === "new" ? "bg-white/[0.08] text-primary shadow-sm" : "text-white/40 hover:text-white/80"
                  }`}
                >
                  新建一个计划
                </button>
              </div>

              {adoptMode === "existing" ? (
                <div className="flex items-center gap-3">
                  <select
                    value={adoptPlanChoice}
                    onChange={(e) => setAdoptPlanChoice(e.target.value)}
                    className="flex-1 max-w-[300px] appearance-none bg-black/40 border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-primary/90 hover:border-white/[0.15] outline-none"
                  >
                    <option value="">选择现有计划…</option>
                    {plans.map((p) => (
                      <option key={p.id} value={p.id}>
                        #{p.id} {p.goal}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => onVerdict(row.id, true, undefined, Number(adoptPlanChoice))}
                    disabled={verdicting || !adoptPlanChoice}
                    className="px-5 py-2.5 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 rounded-xl text-[13px] font-medium transition-colors disabled:opacity-50"
                  >
                    确认归入
                  </button>
                  <button onClick={() => setAdoptingId(null)} className="p-2 text-white/40 hover:text-white/80 transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <input
                      value={newPlanGoal}
                      onChange={(e) => setNewPlanGoal(e.target.value)}
                      placeholder="新计划的目标 (不填就用标题)"
                      className="flex-1 max-w-[400px] bg-black/40 border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-primary/90 focus:border-white/[0.15] outline-none"
                      disabled={verdicting}
                    />
                    <button
                      onClick={() => onAdoptIntoNewPlan(row.id, row.title, newPlanGoal)}
                      disabled={verdicting}
                      className="px-5 py-2.5 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 rounded-xl text-[13px] font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
                    >
                      <Plus className="w-4 h-4" />
                      {verdicting ? "建…" : "新建并采纳"}
                    </button>
                    <button onClick={() => setAdoptingId(null)} className="p-2 text-white/40 hover:text-white/80 transition-colors">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="text-[11px] text-white/30 pl-1">
                    连贯动作：新建计划 → 采纳候选落入该计划，无需离开本页。
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
