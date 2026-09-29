import type { Judgment } from "@/lib/api";

const QUESTIONS: { key: keyof Judgment; title: string; hint: string }[] = [
  { key: "worth_learning", title: "值不值得学", hint: "与主线和痛点的关系" },
  { key: "depth_target", title: "学到什么程度", hint: "目标深度" },
  { key: "intensity", title: "哪些深学，哪些略过", hint: "板块取舍" },
  { key: "time_budget", title: "投入多少时间", hint: "当前精力下的预算" },
];

export const QUESTION_LABELS: Record<keyof Judgment, string> = {
  worth_learning: "① 值不值得学（对主线/痛点的贡献）",
  depth_target: "② 学到什么程度（浅尝/够用/熟练/精通）",
  intensity: "③ 板块分级（深学 vs 过一遍）",
  time_budget: "④ 时间预算（按当前精力与时间排期）",
};

export function JudgmentView({ judgment }: { judgment: Judgment }) {
  return (
    <div className="divide-y divide-white/[0.08] border-y border-white/[0.12]">
      {QUESTIONS.map(({ key, title, hint }, index) => {
        const item = judgment[key];
        return (
          <section key={key} className="grid gap-3 py-6 md:grid-cols-[9rem_minmax(0,1fr)] md:gap-8">
            <div>
              <span className="font-mono text-[11px] tabular-nums text-white/35">{String(index + 1).padStart(2, "0")} / 04</span>
              <h3 className="mt-1 text-[14px] font-medium text-white/85">{title}</h3>
              <p className="mt-1 text-[11px] text-white/35">{hint}</p>
            </div>
            <div className="min-w-0">
              <p className="max-w-[68ch] whitespace-pre-wrap text-[15px] leading-7 text-white/80">{item.answer}</p>
              <div className="mt-3 text-[11px] text-white/40">
                依据档案：{item.profile_item_ids.length ? item.profile_item_ids.map((id) => `#${id}`).join(" · ") : "不足，请补充档案"}
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}
