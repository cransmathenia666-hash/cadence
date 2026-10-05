import { ChevronRight } from "lucide-react";
import { type KnowledgeEvidence, type ProfileChangePayload, type Proposal } from "@/lib/api";
import { CATEGORY_LABELS } from "./types";

/**
 * 档案变更提案的正文。两个来源共用这一个渲染：
 * - 计划对话提炼的（T28）：只有 category + content + why；
 * - 知识库扫描产的（2026-10-05 KB-03）：payload 带 `action` 与知识库出处——
 *   supersede 显示要换下的旧句子，存疑（uncertain）卡整条标黄、不给批准（卡上的
 *   批准按钮由 ProposalCard 一并禁掉），出处逐条给相对路径 + 逐字摘录 + 行号。
 */
export function ProfileChangeSection({ proposal }: { proposal: Proposal }) {
  const payload = proposal.payload as unknown as ProfileChangePayload;
  const action = payload.action ?? "add";
  const isSupersede = action === "supersede";
  const isUncertain = action === "uncertain";
  const evidence: KnowledgeEvidence[] = Array.isArray(payload.evidence) ? payload.evidence : [];

  return (
    <section aria-label="拟写入档案的内容" className="max-w-[76ch]">
      <div className="border-t border-white/15 pt-5">
        <p className="text-[13px] text-white/65">
          归入 <span className="font-medium text-white/85">{CATEGORY_LABELS[payload.category] ?? payload.category}</span>
          {payload.plan_id && <span className="ml-3">· 来自计划 #{payload.plan_id}</span>}
          {isSupersede && <span className="ml-3">· 取代现行条目</span>}
          {isUncertain && <span className="ml-3 text-amber-300">· 存疑</span>}
        </p>
        <blockquote className="mt-8 border-l border-white/35 pl-5 text-[clamp(1.25rem,2.2vw,1.75rem)] font-medium leading-[1.5] tracking-[-0.015em] text-white [overflow-wrap:anywhere]">
          {payload.content || (isUncertain ? "（这张存疑卡没有给出可写入的句子）" : "（未提供内容）")}
        </blockquote>
      </div>

      {isUncertain && (
        <div
          role="note"
          aria-label="存疑说明"
          className="mt-6 border-l border-amber-400/70 pl-4 text-[13px] leading-6 text-amber-100"
        >
          <span className="font-medium">存疑卡，不可批准。</span>
          {payload.uncertainty_reason ?? "提炼这一步自己拿不准。"}
          想让它进档案，就重新扫描让它产出确切的候选；不想要，就驳回并写一句理由。
        </div>
      )}

      {isSupersede && payload.target_content && (
        <div className="mt-8 border-t border-white/10 pt-5">
          <p className="text-[12px] text-white/55">
            将取代的现行条目{payload.target_profile_id ? `（#${payload.target_profile_id}）` : ""}
          </p>
          <p className="mt-2 border-l border-white/20 pl-4 text-[14px] leading-6 text-white/55 [overflow-wrap:anywhere]">
            「{payload.target_content}」
          </p>
        </div>
      )}

      {payload.duplicate_hint && (
        <p className="mt-6 border-l border-amber-400/60 pl-4 text-[13px] leading-6 text-amber-100">
          <span className="font-medium">可能与已有档案近似：</span>
          {payload.duplicate_hint}。这只是提示，不阻止你裁定。
        </p>
      )}

      {evidence.length > 0 && (
        <section aria-label="知识库来源" className="mt-9 border-t border-white/10 pt-5">
          <h3 className="text-[12px] font-medium uppercase tracking-[0.14em] text-white/50">来源</h3>
          <ul className="mt-4 space-y-5">
            {evidence.map((item, index) => (
              <li key={`${item.relative_path}-${index}`}>
                <p className="font-mono text-[12px] text-white/65 [overflow-wrap:anywhere]">
                  {item.relative_path}
                  <span className="ml-2 font-sans text-white/40">
                    第 {item.line_start}–{item.line_end} 行 · {item.root_alias}
                  </span>
                </p>
                <blockquote className="mt-2 border-l border-green/40 pl-4 text-[14px] leading-6 text-white/75 [overflow-wrap:anywhere]">
                  「{item.excerpt}」
                </blockquote>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-[11px] leading-5 text-white/40">
            摘录是扫描时逐字核对过的；批准时会再核对一次——原文件里已找不到的，这条提案会被退回待裁定。
          </p>
        </section>
      )}

      {payload.why && (
        <details className="group mt-9 border-t border-white/10 pt-4">
          <summary className="inline-flex cursor-pointer list-none items-center gap-2 text-[13px] text-white/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/70">
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
            为什么建议这样记
          </summary>
          <p className="mt-4 max-w-[70ch] text-[14px] leading-7 text-white/75">{payload.why}</p>
        </details>
      )}
    </section>
  );
}
