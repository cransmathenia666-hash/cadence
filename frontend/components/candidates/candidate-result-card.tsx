import { ArrowRight, Check, Clock3 } from "lucide-react";

export type CandidateActionResult =
  | {
      kind: "adopted";
      candidateId: number;
      candidateTitle: string;
      planId: number;
      planGoal: string | null;
      stageId: number | null;
      stageTitle: string;
    }
  | {
      kind: "blueprint";
      candidateId: number;
      candidateTitle: string;
      planId: number;
      planGoal: string | null;
      proposalId: number;
      version: number;
    };

type CandidateResultCardProps = {
  result: CandidateActionResult;
  onContinuePlanning: (candidateId: number, planId: number) => void;
  onEnterWorkbench: (planId: number) => void;
  onGoToProposals: (planId: number) => void;
};

function planLabel(result: CandidateActionResult): string {
  // V-01：回执优先显示计划名，不拿裸编号代替说明；拿不到名字时不编。
  return result.planGoal ? `计划「${result.planGoal}」` : "所选计划";
}

export function CandidateResultCard({
  result,
  onContinuePlanning,
  onEnterWorkbench,
  onGoToProposals,
}: CandidateResultCardProps) {
  const isAdopted = result.kind === "adopted";

  return (
    <section
      aria-label={isAdopted ? "候选采纳结果" : "蓝图生成结果"}
      className={`rounded-2xl border px-5 py-4 shadow-[0_8px_28px_rgba(0,0,0,0.18)] ${
        isAdopted
          ? "border-green/20 bg-green/[0.045]"
          : "border-amber-500/20 bg-amber-500/[0.045]"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full ${
            isAdopted ? "bg-green/15" : "bg-amber-500/15"
          }`}
        >
          {isAdopted ? (
            <Check className="size-3 text-green stroke-[3]" />
          ) : (
            <Clock3 className="size-3 text-amber-500" />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <h2 className="text-[14px] font-semibold text-white/90">
              {isAdopted ? "候选已采纳，已完成交接" : "蓝图已生成，等待提案批准"}
            </h2>
          </div>

          {isAdopted ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                候选「{result.candidateTitle}」已落入{planLabel(result)}，并建立阶段「
                {result.stageTitle}」。
              </p>
              <p className="text-[12px] text-white/50">
                这里只是采纳候选并落入计划，不代表蓝图已经批准；下一步可继续规划对话。
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => onContinuePlanning(result.candidateId, result.planId)}
                  className="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-white/85"
                >
                  继续规划对话 <ArrowRight className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onEnterWorkbench(result.planId)}
                  className="inline-flex items-center gap-2 rounded-full border border-white/[0.12] px-4 py-2 text-[12px] font-medium text-white/80 transition-colors hover:border-white/[0.3] hover:text-white"
                >
                  进入工作台 <ArrowRight className="size-3.5" />
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                候选「{result.candidateTitle}」的蓝图已生成，归属{planLabel(result)}，已进入提案裁定。
                <span className="ml-1 text-white/55">（第 {result.version} 版蓝图）</span>
              </p>
              <p className="text-[12px] text-white/50">
                蓝图目前仍在等待批准，尚未写入计划结构。
              </p>
              <div className="pt-1">
                <button
                  type="button"
                  onClick={() => onGoToProposals(result.planId)}
                  className="inline-flex items-center gap-2 rounded-full bg-amber-500 px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-amber-400"
                >
                  去提案裁定 <ArrowRight className="size-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
