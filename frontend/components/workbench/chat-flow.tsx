import { PlanDialogueView, decideProposal, ApiError } from "@/lib/api";
import { ChevronRight, Check, Database, GitPullRequest, Sparkles } from "lucide-react";
import { useState } from "react";

function SuggestionCard({
  suggestion,
  onDecided,
}: {
  suggestion: NonNullable<PlanDialogueView["messages"][0]["suggestion"]>;
  onDecided: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDecide = async (accept: boolean) => {
    setLoading(true);
    setError(null);
    try {
      await decideProposal(suggestion.proposal_id, { approved: accept, reason: accept ? undefined : "聊天中点击忽略" });
      onDecided();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("操作失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const isPending = suggestion.status === "pending";

  return (
    <div className="card-border-gradient p-5 shadow-card-glow relative overflow-hidden group/card mt-4">
      {isPending && (
        <div className="shimmer absolute inset-0 pointer-events-none opacity-40"></div>
      )}

      {error && (
        <div className="text-red-400 text-xs mb-2 bg-red-400/10 px-2 py-1 rounded">
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 mb-4">
        <span className="relative flex h-2 w-2">
          {isPending && (
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60"></span>
          )}
          <span
            className={`relative inline-flex rounded-full h-2 w-2 ${
              isPending
                ? "bg-amber-500"
                : suggestion.status === "accepted"
                ? "bg-emerald-500"
                : "bg-muted"
            }`}
          ></span>
        </span>
        <span
          className={`text-[11px] font-semibold tracking-wide uppercase ${
            isPending
              ? "text-amber-500/90"
              : suggestion.status === "accepted"
              ? "text-emerald-500/90"
              : "text-muted/60"
          }`}
        >
          {isPending
            ? "未确认还没生效"
            : suggestion.status === "accepted"
            ? "已生效"
            : "已忽略"}
        </span>
      </div>

      <div className="flex gap-3.5 mb-6">
        <div className="w-8 h-8 rounded bg-white/[0.03] border border-white/[0.06] flex items-center justify-center shrink-0 mt-0.5">
          <GitPullRequest className="w-4 h-4 text-white/60" />
        </div>
        <div>
          <h4 className="text-[15px] font-medium text-primary/90 mb-1.5">
            计划改动建议
          </h4>
          <p className="text-[14px] text-muted/80 leading-relaxed">
            {suggestion.summary}
          </p>
        </div>
      </div>

      {isPending && (
        <div className="flex items-center gap-3">
          <button
            onClick={() => handleDecide(true)}
            disabled={loading}
            className="btn-primary px-4 py-2.5 rounded-lg text-[13px] font-medium text-white flex-1 flex justify-center items-center gap-2"
          >
            <Check className="w-3.5 h-3.5 opacity-80" />
            {loading ? "处理中..." : "确认生效"}
          </button>
          <button
            onClick={() => handleDecide(false)}
            disabled={loading}
            className="btn-ghost px-5 py-2.5 rounded-lg text-[13px] font-medium border border-transparent"
          >
            忽略
          </button>
        </div>
      )}
    </div>
  );
}

export function ChatFlow({
  dialogue,
  onRefresh,
}: {
  dialogue: PlanDialogueView | null;
  onRefresh: () => void;
}) {
  if (!dialogue || dialogue.messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center h-full">
        <div className="text-muted/50 mb-4 flex items-center gap-2 text-sm">
          <Sparkles className="w-4 h-4" />
          <span>没有任何对话记录</span>
        </div>
        <p className="text-muted/30 text-xs">在这里说出你想学什么或改动什么...</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center justify-center">
        <span className="text-[11px] font-medium text-muted/30 tracking-widest uppercase">
          {dialogue.messages.length > 0 &&
            new Date(dialogue.messages[0].created_at).toLocaleString("zh-CN", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
        </span>
      </div>

      {dialogue.messages.map((msg, idx) => {
        if (msg.role === "user") {
          return (
            <div key={idx} className="flex justify-end w-full">
              <div className="max-w-[80%] bg-surface2/80 border border-white/[0.06] rounded-2xl rounded-tr-sm px-5 py-3.5 text-[15px] leading-relaxed text-primary shadow-sm backdrop-blur-sm">
                {msg.content}
              </div>
            </div>
          );
        }

        return (
          <div key={idx} className="flex flex-col gap-3 w-full max-w-[90%]">
            <div className="flex items-center gap-3 mb-1">
              <div className="w-7 h-7 rounded-full bg-white/[0.08] border border-white/10 flex items-center justify-center shadow-[0_0_16px_rgba(255,255,255,0.12)] backdrop-blur-sm">
                <Sparkles className="w-4 h-4 text-white/90" />
              </div>
              <span className="text-[14px] font-semibold text-primary/90 tracking-wide">
                cadence
              </span>
            </div>

            <div className="text-[15px] leading-[1.7] text-muted/90 pl-10 space-y-4">
              {msg.content.split("\n\n").map((p, i) => (
                <p key={i} className="text-primary/80">
                  {p}
                </p>
              ))}
            </div>

            {msg.run && (
              <div className="pl-10 mt-1">
                <details className="group cursor-pointer">
                  <summary className="inline-flex items-center gap-2 text-[12px] font-medium text-muted/50 hover:text-muted transition-colors select-none rounded px-1 -ml-1">
                    <ChevronRight className="w-3.5 h-3.5 transition-transform group-open:rotate-90" />
                    运行依据
                  </summary>
                  <div className="mt-3 ml-[7px] pl-4 border-l border-white/[0.06] text-[13px] text-muted/70 space-y-2.5">
                    <div className="flex items-center gap-2.5">
                      <Check className="w-3.5 h-3.5 text-emerald-500/70" />
                      <span>
                        {msg.run.stop_reason}
                      </span>
                    </div>
                    {msg.run.tools.map((t, ti) => (
                      <div key={ti} className="flex items-center gap-2.5">
                        <Database className="w-3.5 h-3.5 text-muted/40" />
                        <span>{t.summary}</span>
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            )}

            {msg.suggestion && (
              <div className="pl-10 mt-1 w-full max-w-[480px]">
                <SuggestionCard
                  suggestion={msg.suggestion}
                  onDecided={onRefresh}
                />
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
