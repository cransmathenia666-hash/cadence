import { ArrowRight, Check, Clock3 } from "lucide-react";

/**
 * 候选动作的回执卡（OC-05 新语义）：
 * - `adopted`：采纳 = **已进入规划会话**（node_id 恒为 null，不再有「已建阶段」）；
 *   `message` / `planning_status` 是后端给的，界面原样展示，不自行改写业务判定。
 * - `blueprint`：蓝图已生成、等提案裁定；「新方向」的蓝图 `planId` 为 null
 *   （正式计划在批准那一刻才创建）。
 */
export type CandidateActionResult =
  | {
      kind: "adopted";
      candidateId: number;
      candidateTitle: string;
      /** 采纳创建的规划会话；后端没给（异常回执）时为 null。 */
      planningSessionId: number | null;
      /** 后端算好的规划进度（needs_blueprint 等）；只展示。 */
      planningStatus: string | null;
      /** 后端回执里的下一步说明；原样展示。 */
      message: string | null;
      /** 规划落点；null = 按「新方向」采纳，还没有正式计划。 */
      landingPlanId: number | null;
      landingPlanGoal: string | null;
    }
  | {
      kind: "blueprint";
      candidateId: number;
      candidateTitle: string;
      /** 「新方向」会话生成的蓝图为 null——批准时才创建正式计划。 */
      planId: number | null;
      planGoal: string | null;
      proposalId: number;
      version: number;
    };

type CandidateResultCardProps = {
  result: CandidateActionResult;
  onContinuePlanning: (candidateId: number, planId: number | null) => void;
  onEnterWorkbench: (planId: number) => void;
  onGoToProposals: (planId: number | null) => void;
};

function planLabel(planId: number | null, planGoal: string | null): string {
  // V-01：回执优先显示计划名，不拿裸编号代替说明；拿不到名字时不编。
  return planGoal ? `计划「${planGoal}」` : planId !== null ? `计划 #${planId}` : "所选计划";
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
              {isAdopted ? "候选已采纳，已进入规划会话" : "蓝图已生成，等待提案批准"}
            </h2>
          </div>

          {isAdopted ? (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                候选「{result.candidateTitle}」
                {result.landingPlanId !== null
                  ? `的规划落点是${planLabel(result.landingPlanId, result.landingPlanGoal)}，规划会话已开启。`
                  : "已按「新方向」采纳——还没有正式计划，蓝图批准时才创建计划与阶段。"}
              </p>
              {result.message && (
                <p className="text-[12px] leading-relaxed text-white/60">{result.message}</p>
              )}
              {!result.message && (
                <p className="text-[12px] text-white/50">
                  下一步在规划对话里把成果和验收标准聊清楚，再生成蓝图。
                </p>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => onContinuePlanning(result.candidateId, result.landingPlanId)}
                  className="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                >
                  继续规划对话 <ArrowRight className="size-3.5" />
                </button>
                {result.landingPlanId !== null && (
                  <button
                    type="button"
                    onClick={() => onEnterWorkbench(result.landingPlanId as number)}
                    className="inline-flex items-center gap-2 rounded-full border border-white/[0.12] px-4 py-2 text-[12px] font-medium text-white/80 transition-colors hover:border-white/[0.3] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                  >
                    查看落点计划 <ArrowRight className="size-3.5" />
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="mt-2 space-y-2 text-[13px] text-white/70">
              <p>
                候选「{result.candidateTitle}」的蓝图已生成，已进入提案裁定。
                <span className="ml-1 text-white/55">（第 {result.version} 版蓝图）</span>
              </p>
              <p className="text-[12px] text-white/50">
                {result.planId !== null
                  ? `落点是${planLabel(result.planId, result.planGoal)}；批准前不会写入计划结构。`
                  : "这是「新方向」的蓝图——批准那一刻才会创建正式计划与阶段，现在还没有任何计划被改动。"}
              </p>
              <div className="pt-1">
                <button
                  type="button"
                  onClick={() => onGoToProposals(result.planId)}
                  className="inline-flex items-center gap-2 rounded-full bg-amber-500 px-4 py-2 text-[12px] font-medium text-black transition-colors hover:bg-amber-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
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
