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

  return (
    <div className="card-border-gradient shadow-card-glow relative overflow-hidden group/card rounded-2xl bg-surface2/80 p-5 md:p-6 transition-all">
      <div className="flex items-start justify-between mb-4">
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 bg-white/[0.04] rounded-full text-[11px] font-medium text-muted/80 tracking-wide">
              {KIND_LABELS[row.kind] ?? row.kind}
            </span>
            {isRowPath ? (
              <span className="px-2.5 py-1 bg-blue-500/10 text-blue-400 rounded-full text-[11px] font-medium tracking-wide flex items-center gap-1.5">
                <Route className="w-3.5 h-3.5" />
                一条路
              </span>
            ) : (
              <span className="px-2.5 py-1 bg-purple-500/10 text-purple-400 rounded-full text-[11px] font-medium tracking-wide flex items-center gap-1.5">
                <ListTree className="w-3.5 h-3.5" />
                几个方向
              </span>
            )}
            {row.isRecommended && (
              <span className="px-2.5 py-1 bg-amber-500/10 text-amber-500 rounded-full text-[11px] font-medium tracking-wide">
                建议优先从这开始
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0 ml-4">
          <span className="relative flex h-2 w-2">
            {row.status === "proposed" && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60"></span>
            )}
            <span className={`relative inline-flex rounded-full h-2 w-2 ${statusColor}`}></span>
          </span>
          <span className="text-[11px] font-semibold text-muted/60 tracking-wide uppercase">
            {statusText}
          </span>
        </div>
      </div>

      <h3 className="text-[16px] font-medium text-primary/90 mb-3 leading-snug">
        {row.title}
      </h3>

      <p className="text-[14px] text-muted/80 leading-relaxed mb-4">
        {row.why}
      </p>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-muted/50 mb-5">
        <span className="flex items-center gap-1">
          建议深度 <strong className="text-primary/70 font-medium ml-1">{row.depthTarget}</strong>
        </span>
        {row.planId && (
          <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-muted/30">
            所属计划 <strong className="text-primary/70 font-medium ml-1">#{row.planId}</strong>
          </span>
        )}
        {row.rejectReason && (
          <span className="flex items-center gap-1 before:content-['·'] before:mr-4 before:text-muted/30 text-red-400/80">
            否决理由: {row.rejectReason}
          </span>
        )}
      </div>

      {isRowPath && row.steps.length > 0 && (
        <div className="bg-black/30 border border-white/[0.04] rounded-xl p-4 mb-5">
          <div className="text-[12px] font-medium text-primary/70 mb-3">
            这条路上的先后步骤（草案，不单独裁定）
          </div>
          <ol className="space-y-3 pl-1">
            {row.steps.map((step: PathStep, index: number) => (
              <li key={index} className="text-[13px]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="w-4 h-4 rounded-full bg-white/[0.08] flex items-center justify-center text-[10px] text-muted/80 font-medium shrink-0">
                    {index + 1}
                  </span>
                  <strong className="text-primary/80 font-medium">{step.title}</strong>
                  {step.deliverable && (
                    <span className="text-[11px] text-emerald-400/80 px-1.5 py-0.5 rounded bg-emerald-500/10 ml-2">
                      要交: {step.deliverable}
                    </span>
                  )}
                </div>
                {step.why && (
                  <div className="text-[12px] text-muted/60 pl-6 mt-1 leading-relaxed">
                    {step.why}
                  </div>
                )}
              </li>
            ))}
          </ol>
          <div className="text-[11px] text-muted/40 mt-4 pl-1">
            采纳就是「这条路我走」。哪一步这轮不建，在出蓝图时不勾就行；建出来之后不想要，在计划页「跳过这一步」——两步都不进永久禁区。
          </div>
        </div>
      )}

      {isDecided ? (
        <div className="mt-4 pt-4 border-t border-white/[0.04]">
          <div className="text-[12px] text-muted/60 font-medium flex items-center gap-2">
            <Check className="w-3.5 h-3.5" />
            {notes[row.id] ?? "该候选已有最终结论"}
          </div>
          {row.status === "accepted" && (
            <button
              onClick={() => setChattingId(chattingId === row.id ? null : row.id)}
              disabled={verdicting}
              className="mt-4 px-4 py-2.5 bg-white/[0.04] hover:bg-white/[0.08] text-primary/90 rounded-xl transition-colors text-[13px] font-medium flex items-center gap-2 disabled:opacity-50"
            >
              {chattingId === row.id ? "收起意向规划对话" : "开展规划对话（细化后生成蓝图）"}
              <ChevronDown className={`w-4 h-4 transition-transform ${chattingId === row.id ? "rotate-180" : ""}`} />
            </button>
          )}
          {chattingId === row.id && renderChatBox(row.id)}
        </div>
      ) : (
        <div className="mt-5 pt-5 border-t border-white/[0.04] flex flex-wrap items-center gap-3">
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
            className="btn-primary px-5 py-2.5 rounded-full text-[13px] font-medium text-white flex items-center gap-2 disabled:opacity-50"
          >
            <Check className="w-3.5 h-3.5 opacity-80" />
            {row.planId === null ? "采纳 (选择归属计划)" : isRowPath ? "采纳整条路" : "采纳 (落入计划)"}
          </button>

          {rejectingId === row.id ? (
            <div className="flex items-center gap-2 bg-black/40 p-1.5 rounded-full border border-white/[0.08]">
              <input
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder={isRowPath ? "否决整条路的理由 (必填)" : "否决理由 (进永久禁区)"}
                className="bg-transparent border-none text-[13px] text-primary w-[200px] px-3 focus:outline-none focus:ring-0"
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
                className="p-1.5 text-muted/60 hover:text-primary transition-colors rounded-full"
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
              className="btn-ghost px-5 py-2.5 rounded-full text-[13px] font-medium border border-transparent flex items-center gap-2 disabled:opacity-50"
            >
              <X className="w-3.5 h-3.5 opacity-80" />
              {isRowPath ? "否决整条路" : "否决"}
            </button>
          )}

          {adoptingId === row.id && (
            <div className="w-full mt-4 bg-black/40 rounded-2xl border border-white/[0.08] p-4 flex flex-col gap-4">
              <div className="text-[13px] font-medium text-primary/80">指定该候选采纳后要落到哪个计划：</div>
              <div className="flex items-center gap-2 p-1 bg-white/[0.04] rounded-xl w-fit">
                <button
                  onClick={() => setAdoptMode("existing")}
                  className={`px-4 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                    adoptMode === "existing" ? "bg-white/[0.08] text-primary shadow-sm" : "text-muted/60 hover:text-primary/80"
                  }`}
                >
                  选一个现有计划
                </button>
                <button
                  onClick={() => setAdoptMode("new")}
                  className={`px-4 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                    adoptMode === "new" ? "bg-white/[0.08] text-primary shadow-sm" : "text-muted/60 hover:text-primary/80"
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
                    className="flex-1 appearance-none bg-black/60 border border-white/[0.08] rounded-xl px-4 py-2 text-[13px] text-primary/90 hover:border-white/[0.15] outline-none"
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
                    className="btn-primary px-4 py-2 rounded-xl text-[12px] font-medium text-white disabled:opacity-50"
                  >
                    确认归入
                  </button>
                  <button onClick={() => setAdoptingId(null)} className="p-2 text-muted/60 hover:text-primary transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-3">
                    <input
                      value={newPlanGoal}
                      onChange={(e) => setNewPlanGoal(e.target.value)}
                      placeholder="新计划的目标 (不填就用标题)"
                      className="flex-1 bg-black/60 border border-white/[0.08] rounded-xl px-4 py-2 text-[13px] text-primary/90 focus:border-white/[0.15] outline-none"
                      disabled={verdicting}
                    />
                    <button
                      onClick={() => onAdoptIntoNewPlan(row.id, row.title, newPlanGoal)}
                      disabled={verdicting}
                      className="btn-primary px-4 py-2 rounded-xl text-[12px] font-medium text-white flex items-center gap-2 disabled:opacity-50"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      {verdicting ? "建…" : "新建并采纳"}
                    </button>
                    <button onClick={() => setAdoptingId(null)} className="p-2 text-muted/60 hover:text-primary transition-colors">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="text-[11px] text-muted/40 pl-1">
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
