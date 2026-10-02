import { Check, MessageSquareWarning, ShieldCheck } from "lucide-react";
import type {
  BlueprintReview,
  BlueprintReviewFinding,
  BlueprintReviewer,
  BlueprintReviewPoint,
} from "@/lib/api";

/** 赞同／异议的总表态徽标：一眼看出这位审查员是放行还是拦了一下。 */
function StanceBadge({ agree }: { agree: boolean }) {
  return agree ? (
    <span className="flex shrink-0 items-center gap-1 rounded-md bg-green/10 px-1.5 py-0.5 text-[10px] font-medium text-green">
      <Check className="h-3 w-3" aria-hidden="true" />
      无异议
    </span>
  ) : (
    <span className="flex shrink-0 items-center gap-1 rounded-md bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-200">
      <MessageSquareWarning className="h-3 w-3" aria-hidden="true" />
      提出异议
    </span>
  );
}

/** 一条表态：赞同说认可哪一点、为什么；反对说反对哪一点、理由和怎么调整。

    展示侧重：目标位置做扫读锚点，表态正文是主读，「建议调整」是要紧动作（仅反对方有），
    「依据」降为支撑细节。`ruled` 用于多列网格：每个格子都带上缘分隔线；堆叠列表里首条不带。 */
function ReviewPointRow({
  point,
  ruled = false,
}: {
  point: BlueprintReviewPoint;
  ruled?: boolean;
}) {
  const agree = point.stance === "agree";
  return (
    <li
      className={`space-y-2 border-t border-white/[0.06] pt-3 ${ruled ? "" : "first:border-t-0 first:pt-0"}`}
    >
      {/* 行内排布而不是 flex 换行：目标位置可能很长，态度与徽标跟着正文自然折行 */}
      <div className="text-[13px] leading-5">
        {agree ? (
          <Check className="mr-1.5 inline-block h-3.5 w-3.5 align-[-2px] text-green/90" aria-hidden="true" />
        ) : (
          <MessageSquareWarning
            className="mr-1.5 inline-block h-3.5 w-3.5 align-[-2px] text-amber-200/90"
            aria-hidden="true"
          />
        )}
        <span className={`mr-1.5 text-[11px] font-medium ${agree ? "text-green/90" : "text-amber-200/90"}`}>
          {agree ? "认可" : "反对"}
        </span>
        <span className="font-medium text-white/90">{point.target}</span>
        {!agree && (
          <span
            className={`ml-2 inline-block rounded-md px-1.5 py-0.5 text-[10px] ${
              point.severity === "revise"
                ? "bg-amber-400/10 text-amber-200"
                : "bg-white/[0.08] text-white/75"
            }`}
          >
            {point.severity === "revise" ? "已纳入修订" : "待确认"}
          </span>
        )}
      </div>
      <p className="text-[13px] leading-6 text-white/85">{point.point}</p>
      {!agree && point.adjustment && (
        <p className="text-[12px] leading-5">
          <span className="mr-1.5 text-[11px] font-medium text-amber-200/80">建议调整</span>
          <span className="text-white/80">{point.adjustment}</span>
        </p>
      )}
      <p className="text-[12px] leading-5">
        <span className="mr-1.5 text-[11px] text-white/50">依据</span>
        <span className="text-white/55">{point.reason}</span>
      </p>
    </li>
  );
}

/** 审查席上的一个座位。系统防冲突检查也占一座——它不请模型，是确定性检查。 */
function ReviewerCard({ reviewer }: { reviewer: BlueprintReviewer }) {
  const isSystem = reviewer.key === "system";
  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
      <div className="flex items-center gap-2">
        {isSystem && <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-white/50" aria-hidden="true" />}
        <h4 className="shrink-0 text-[13px] font-semibold text-white/90">{reviewer.name}</h4>
        <span className="min-w-0 truncate text-[11px] text-white/55">{reviewer.lens}</span>
        <span className="ml-auto shrink-0">
          <StanceBadge agree={reviewer.stance === "agree"} />
        </span>
      </div>
      <p className="text-[12px] leading-5 text-white/70">{reviewer.summary}</p>
      {reviewer.points.length > 0 ? (
        <ul className="space-y-3">
          {reviewer.points.map((point, index) => (
            <ReviewPointRow key={`${point.target}-${index}`} point={point} />
          ))}
        </ul>
      ) : (
        <p className="text-[12px] leading-5 text-white/55">职责范围内没有需要修改或请你确认的问题。</p>
      )}
    </article>
  );
}

/** 把修订回执对回「哪位审查员的哪条意见」，给人话位置而不是内部编号。 */
function resolutionLabel(
  reviewers: BlueprintReviewer[],
  item: BlueprintReview["revision_resolution"][number],
): string {
  if (typeof item.reviewer_key === "string" && typeof item.point_index === "number") {
    const reviewer = reviewers.find((entry) => entry.key === item.reviewer_key);
    const point = reviewer?.points[item.point_index];
    if (point) return `${reviewer?.name ?? item.reviewer_key} · ${point.target}`;
    return reviewer ? `${reviewer.name} · 第 ${item.point_index + 1} 条意见` : item.reviewer_key;
  }
  const index = item.finding_index;
  if (typeof index === "number") {
    const point = reviewers[0]?.points[index];
    if (point) return `${reviewers[0]?.name ?? "审查记录"} · ${point.target}`;
    return `问题 ${index + 1}`;
  }
  return "审查意见";
}

/** 更早的单审查员版本生成的增强稿没有 reviewers：把旧字段映射成一个审查员卡片。 */
function legacyReviewer(review: BlueprintReview): BlueprintReviewer {
  const findings: BlueprintReviewFinding[] = review.findings ?? [];
  return {
    key: "legacy",
    name: "独立审查",
    lens: "旧版格式 · 单审查员记录",
    stance: findings.length > 0 ? "disagree" : "agree",
    summary: review.summary ?? "",
    points: findings.map((finding) => ({
      stance: "disagree",
      target: finding.target,
      point: finding.problem,
      reason: finding.basis,
      adjustment: finding.recommendation,
      severity: finding.severity,
    })),
  };
}

/**
 * 增强模式的审查席：每位审查员一张卡、默认展开，赞同与反对都摆在明面上——
 * 用户不用点开任何折叠就能看出「这份稿子真的被多个 agent 各自审过」。
 * 修订对照与修订前初稿属于核对材料，按需展开。
 */
export function BlueprintReviewPanel({ review }: { review: BlueprintReview }) {
  const isLegacy = !review.reviewers?.length;
  const reviewers = isLegacy ? [legacyReviewer(review)] : review.reviewers!;
  const objecting = reviewers.filter((reviewer) => reviewer.stance === "disagree").length;
  const hasAudit = review.revision_resolution.length > 0 || review.initial != null;
  const singleCard = reviewers.length === 1;
  const gridClass =
    reviewers.length === 2
      ? "grid gap-3 md:grid-cols-2"
      : "grid gap-3 md:grid-cols-2 xl:grid-cols-3";

  return (
    <section aria-label="蓝图多 agent 审查" className="space-y-3 border-y border-white/10 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[13px] font-semibold text-white/85">
          {isLegacy ? "审查记录" : "多 agent 审查"}
        </h3>
        <p className="text-[12px] text-white/55">
          {isLegacy
            ? objecting > 0
              ? `发现 ${reviewers[0]!.points.length} 条记录，供你核对`
              : "未发现需要修改或请你确认的问题"
            : objecting > 0
              ? (
                <>
                  {`${reviewers.length} 位审查员独立复核，`}
                  <span className="font-medium text-amber-200/90">{objecting} 位提出异议</span>
                </>
              )
              : `${reviewers.length} 位审查员独立复核，均无异议`}
        </p>
      </div>

      {singleCard ? (
        /* 单审查员（旧版稿兜底）：不套卡片——身份与总评占一行，意见铺成横向网格。
           窄卡长竖条只会一边堆得太高、一边全空白，横向铺开才用得上桌面宽度。 */
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h4 className="shrink-0 text-[13px] font-semibold text-white/90">{reviewers[0]!.name}</h4>
            <StanceBadge agree={reviewers[0]!.stance === "agree"} />
            <span className="min-w-0 text-[11px] text-white/55">{reviewers[0]!.lens}</span>
          </div>
          {reviewers[0]!.summary && (
            <p className="mt-2 max-w-[75ch] text-[12px] leading-5 text-white/70">
              {reviewers[0]!.summary}
            </p>
          )}
          {reviewers[0]!.points.length > 0 ? (
            <ul className="mt-4 grid gap-x-8 gap-y-5 md:grid-cols-2 xl:grid-cols-3">
              {reviewers[0]!.points.map((point, index) => (
                <ReviewPointRow key={`${point.target}-${index}`} point={point} ruled />
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-[12px] leading-5 text-white/55">
              未发现需要修改或请你确认的问题。
            </p>
          )}
        </div>
      ) : (
        <div className={gridClass}>
          {reviewers.map((reviewer) => (
            <ReviewerCard key={reviewer.key} reviewer={reviewer} />
          ))}
        </div>
      )}

      {hasAudit && (
        <details className="text-[12px] text-white/60">
          <summary className="w-fit cursor-pointer rounded-sm py-1 underline decoration-white/25 underline-offset-4 hover:text-white/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            修订对照与修订前初稿（{review.revision_resolution.length} 条交代）
          </summary>
          <div className="mt-4 space-y-5 border-t border-white/[0.08] pt-4">
            {review.revision_resolution.length > 0 && (
              <div>
                <h4 className="mb-2 text-[12px] font-medium text-white/75">
                  修订对照 · 每条反对意见是怎么处理的
                </h4>
                <ul className="space-y-2">
                  {review.revision_resolution.map((item, index) => (
                    <li key={index}>
                      <span className="text-white/75">{resolutionLabel(reviewers, item)}：</span>
                      {item.resolution}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {review.initial && (
              <div>
                <h4 className="mb-2 text-[12px] font-medium text-white/75">修订前初稿</h4>
                <p className="mb-2">目标：{review.initial.goal}</p>
                <ol className="space-y-3">
                  {review.initial.stages.map((stage, index) => (
                    <li key={`${stage.title}-${index}`} className="border-t border-white/[0.06] pt-2">
                      <p className="font-medium text-white/75">阶段 {index + 1}：{stage.title}</p>
                      <p>阶段依据：{stage.why_now ?? stage.why ?? "未说明"}</p>
                      <p>交付物：{stage.deliverable}</p>
                      {stage.tasks.length > 0 && (
                        <p>任务：{stage.tasks.map((task) => task.title).join("、")}</p>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </details>
      )}
    </section>
  );
}
