import type { Judgment } from "@/lib/api";

export const QUESTION_LABELS: Record<keyof Judgment, string> = {
  worth_learning: "① 值不值得学（对主线/痛点的贡献）",
  depth_target: "② 学到什么程度（浅尝/够用/熟练/精通）",
  intensity: "③ 板块分级（深学 vs 过一遍）",
  time_budget: "④ 时间预算（按当前精力与时间排期）",
};

export function JudgmentView({ judgment }: { judgment: Judgment }) {
  const keys = Object.keys(QUESTION_LABELS) as (keyof Judgment)[];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", margin: "10px 0" }}>
      {keys.map((key) => {
        const item = judgment[key];
        return (
          <div
            key={key}
            style={{
              background: "var(--bg-subtle)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              padding: "10px 12px",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: "13px", color: "var(--text-main)", marginBottom: "4px" }}>
              {QUESTION_LABELS[key]}
            </div>
            <div style={{ fontSize: "13px", lineHeight: "1.5", color: "var(--text-main)" }}>
              {item.answer}
            </div>
            <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--text-muted)" }}>
              档案依据：
              {item.profile_item_ids.length === 0 ? (
                <span>（依据不足）</span>
              ) : (
                item.profile_item_ids.map((id) => (
                  <span
                    key={id}
                    className="badge badge-not_started"
                    style={{ marginLeft: "4px", fontSize: "10px" }}
                  >
                    档案 #{id}
                  </span>
                ))
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
