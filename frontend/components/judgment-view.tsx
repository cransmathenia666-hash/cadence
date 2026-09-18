import type { Judgment } from "@/lib/api";

/**
 * 四问答案的公共渲染（`/judge` 与 `/proposals` 共用）。
 *
 * 每问都要指回它的档案 id——后端把「指不回去又不说明依据不足」判为不合格，
 * 所以这里不能只显示答案、把依据藏起来。
 */
export const QUESTION_LABELS: Record<keyof Judgment, string> = {
  worth_learning: "① 值不值得学",
  depth_target: "② 学到什么程度",
  intensity: "③ 板块分级",
  time_budget: "④ 时间预算",
};

export function JudgmentView({ judgment }: { judgment: Judgment }) {
  return (
    <>
      {(Object.keys(QUESTION_LABELS) as (keyof Judgment)[]).map((key) => (
        <p key={key}>
          <strong>{QUESTION_LABELS[key]}：</strong>
          {judgment[key].answer}
          <br />
          <small>
            依据：
            {judgment[key].profile_item_ids.length === 0
              ? "（无）"
              : judgment[key].profile_item_ids.map((id) => `#${id}`).join("、")}
          </small>
        </p>
      ))}
    </>
  );
}