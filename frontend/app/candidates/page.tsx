"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  findCandidates,
  generateBlueprint,
  getPlanChat,
  getProfile,
  listCandidates,
  listPlans,
  sayPlanChat,
  verdictCandidate,
  type CandidateList,
  type ChatMessage,
  type FindResult,
  type PlanChatView,
  type PlanSummary,
  type ProfileView,
} from "@/lib/api";

const KIND_LABELS: Record<string, string> = {
  concept: "概念",
  doc: "资料",
  project: "项目",
  course: "课程",
};

const STATUS_LABELS: Record<string, string> = {
  proposed: "待裁定",
  accepted: "已采纳",
  rejected: "已否决",
  expired: "已过期",
};

type Row = {
  id: number;
  title: string;
  kind: string;
  why: string;
  depthTarget: string;
  isRecommended: boolean;
  status: string;
  rejectReason: string | null;
  basis: number[] | null;
  planId: number | null;
};

function rowsFromFind(found: FindResult): Row[] {
  return found.candidates.map((item, index) => ({
    id: found.candidate_ids[index],
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.title === found.recommended_start,
    status: "proposed",
    rejectReason: null,
    basis: item.profile_item_ids,
    planId: found.plan_id,
  }));
}

function rowsFromStored(stored: CandidateList): Row[] {
  return stored.candidates.map((item) => ({
    id: item.id,
    title: item.title,
    kind: item.kind,
    why: item.why,
    depthTarget: item.depth_target,
    isRecommended: item.is_recommended === 1,
    status: item.status,
    rejectReason: item.reject_reason,
    basis: null,
    planId: item.plan_id,
  }));
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

function ChatMessageView({ item }: { item: ChatMessage }) {
  const isUser = item.role === "user";
  if (isUser) {
    return (
      <div className="chat-bubble user">
        <span className="bubble-role">你</span>
        <div>{item.content}</div>
      </div>
    );
  }

  let data: { questions?: string[]; ready?: boolean; note?: string } | null = null;
  try {
    data = JSON.parse(item.content);
  } catch {
    data = null;
  }

  if (data === null || typeof data !== "object") {
    return (
      <div className="chat-bubble assistant">
        <span className="bubble-role">AI 规划助手</span>
        <div style={{ whiteSpace: "pre-wrap" }}>{item.content}</div>
      </div>
    );
  }

  return (
    <div className="chat-bubble assistant">
      <span className="bubble-role">AI 规划助手</span>
      {data.note && <div style={{ marginBottom: "6px" }}>{data.note}</div>}
      {(data.questions ?? []).length > 0 && (
        <ol style={{ paddingLeft: "18px", margin: "6px 0" }}>
          {(data.questions ?? []).map((question, index) => (
            <li key={index} style={{ marginBottom: "2px" }}>{question}</li>
          ))}
        </ol>
      )}
      {data.ready === true && (
        <div style={{ marginTop: "6px", color: "var(--success)", fontWeight: 600, fontSize: "12px" }}>
          ✓ AI 提示：意向信息已充分，可以随时生成阶段蓝图。
        </div>
      )}
    </div>
  );
}

function ChatBox({
  candidateId,
  planId,
  title,
  plans,
}: {
  candidateId: number;
  planId: number | null;
  title: string;
  plans: PlanSummary[];
}) {
  const [view, setView] = useState<PlanChatView | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [chosenPlan, setChosenPlan] = useState("");

  const effectivePlanId: number | null =
    view?.plan_id ?? (planId !== null ? planId : chosenPlan === "" ? null : Number(chosenPlan));

  useEffect(() => {
    getPlanChat(candidateId, planId ?? undefined)
      .then(setView)
      .catch((cause: unknown) => setError(messageOf(cause, "取规划对话记录失败")));
  }, [candidateId, planId]);

  async function refresh() {
    setView(await getPlanChat(candidateId, effectivePlanId ?? undefined));
  }

  async function onSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await sayPlanChat(candidateId, message, effectivePlanId ?? undefined);
      setMessage("");
      await refresh();
      setNotice(
        `已回复（第 ${done.turns_used}/${done.max_turns} 轮）：` +
          (done.reply.ready ? "意向已明确，可点击生成蓝图方案。" : "助手补充了针对性问题，请继续作答。"),
      );
    } catch (cause) {
      setError(messageOf(cause, "规划对话请求失败"));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await generateBlueprint(candidateId, effectivePlanId ?? undefined);
      await refresh();
      setNotice(
        `第 ${done.version} 版蓝图已生成并存入「待裁定提案」（提案 #${done.proposal_id}）——请前往该页勾选所需阶段与任务。`,
      );
    } catch (cause) {
      setError(messageOf(cause, "生成蓝图方案失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-strong)",
        borderRadius: "var(--radius-md)",
        padding: "16px",
        marginTop: "12px",
      }}
    >
      <div className="flex-between" style={{ marginBottom: "10px" }}>
        <div>
          <span style={{ fontWeight: 600, fontSize: "14px" }}>深度意向规划：{title}</span>
          <span style={{ fontSize: "12px", color: "var(--text-muted)", marginLeft: "8px" }}>
            ({view === null ? "…" : `已进行 ${view.turns_used}/${view.max_turns} 轮`}
            {effectivePlanId !== null && ` · 归属计划 #${effectivePlanId}`})
          </span>
        </div>
        {view?.blueprint != null && (
          <Link href="/proposals" className="badge badge-in_progress" style={{ textDecoration: "none" }}>
            待批蓝图 #{view.blueprint.id} →
          </Link>
        )}
      </div>

      {view !== null && effectivePlanId === null && (
        <div className="alert alert-warning" style={{ fontSize: "12px" }}>
          <label htmlFor={`chat-plan-${candidateId}`} style={{ fontWeight: 600, marginRight: "8px" }}>
            该候选为「新方向」，请先指定注入的计划：
          </label>
          <select
            id={`chat-plan-${candidateId}`}
            value={chosenPlan}
            onChange={(event) => setChosenPlan(event.target.value)}
          >
            <option value="">请选择目标计划…</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>
                计划 #{item.id}：{item.goal}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="chat-container">
        {view !== null && view.messages.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--text-muted)", padding: "16px 0" }}>
            先聊清楚你的时间投入、首选切入点与验收目标，聊透后再出蓝图。
          </div>
        )}
        {(view?.messages ?? []).map((item, index) => (
          <ChatMessageView key={index} item={item} />
        ))}
      </div>

      <form onSubmit={onSend} style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <textarea
          rows={2}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="例如：我每周预计能投入 5 小时，希望重点攻克接口设计，最终输出一个可演示的工程"
          disabled={busy || effectivePlanId === null}
          style={{ width: "100%" }}
          required
        />
        <div className="flex-between">
          <button
            type="button"
            className="sm"
            onClick={onGenerate}
            disabled={busy || view === null || !view.can_generate || effectivePlanId === null}
          >
            {busy ? "生成中…" : "意向已达成，让 AI 生成蓝图方案"}
          </button>
          <button
            type="submit"
            className="primary sm"
            disabled={busy || message.trim() === "" || effectivePlanId === null}
          >
            {busy ? "回复中…" : "发送回复"}
          </button>
        </div>
      </form>

      {error !== null && (
        <div className="alert alert-danger" style={{ marginTop: "10px", fontSize: "12px" }}>
          {error}
        </div>
      )}
      {notice !== null && (
        <div className="alert alert-info" style={{ marginTop: "10px", fontSize: "12px" }}>
          {notice}
        </div>
      )}
    </div>
  );
}

export default function CandidatesPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [planChoice, setPlanChoice] = useState("");
  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [fresh, setFresh] = useState<FindResult | null>(null);
  const [stored, setStored] = useState<CandidateList | null>(null);
  const [askError, setAskError] = useState<string | null>(null);

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const [adoptingId, setAdoptingId] = useState<number | null>(null);
  const [adoptPlanChoice, setAdoptPlanChoice] = useState("");
  const [landingPlans, setLandingPlans] = useState<Record<number, number>>({});
  const [chattingId, setChattingId] = useState<number | null>(null);

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
    listCandidates()
      .then((data) => setStored(data))
      .catch(() => setStored(null));
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAskError(null);
    setFresh(null);
    try {
      const found = await findCandidates(rawText, planChoice === "" ? undefined : Number(planChoice));
      setFresh(found);
      setStored(null);
    } catch (cause) {
      setAskError(cause instanceof ApiError ? cause.message : "请求候选清单失败，原因不明");
    } finally {
      setAsking(false);
    }
  }

  async function onVerdict(candidateId: number, accepted: boolean, reason?: string, explicitPlanId?: number) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(
        candidateId,
        accepted,
        reason,
        explicitPlanId,
      );
      setNotes((previous) => ({
        ...previous,
        [candidateId]: accepted
          ? `已采纳：已在计划 #${done.plan_id ?? ""} 里建立初始阶段「${done.id}」`
          : "已否决：此主题已列入不可逆禁区。",
      }));
      if (done.plan_id !== null && done.plan_id !== undefined) {
        setLandingPlans((previous) => ({ ...previous, [candidateId]: Number(done.plan_id) }));
      }
      setRejectingId(null);
      setAdoptingId(null);
      setStored(await listCandidates());
    } catch (cause) {
      setVerdictError(cause instanceof ApiError ? cause.message : "裁定候选失败");
    } finally {
      setVerdicting(false);
    }
  }

  const rows: Row[] | null =
    fresh !== null ? rowsFromFind(fresh) : stored !== null ? rowsFromStored(stored) : null;
  const pendingCount = (rows ?? []).filter((row) => row.status === "proposed").length;

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>候选清单：我不知道该学什么</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            向决策引擎表达你的困惑或目标。引擎结合档案筛选出 3-5 条路线，否决的题目绝不再推。
          </p>
        </div>
        {profile !== null && (
          <span className="badge badge-not_started">长期档案 {profile.items.length} 条</span>
        )}
      </div>

      <div className="card">
        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "12px" }}>
            <div>
              <label htmlFor="plan" style={{ fontWeight: 600 }}>针对哪个计划（可选）：</label>
              <select
                id="plan"
                value={planChoice}
                onChange={(event) => setPlanChoice(event.target.value)}
                style={{ width: "100%" }}
              >
                <option value="">新方向（不从属于任何现有计划）</option>
                {plans.map((item) => (
                  <option key={item.id} value={item.id}>
                    计划 #{item.id}：{item.goal}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="raw" style={{ fontWeight: 600 }}>我的当前处境 / 困惑（必填）：</label>
              <input
                id="raw"
                value={rawText}
                onChange={(event) => setRawText(event.target.value)}
                placeholder="例如：我不知道该学什么，想要提升后端工程实战能力并产出作品"
                style={{ width: "100%" }}
                required
              />
            </div>
          </div>

          <div className="flex-between" style={{ marginTop: "4px" }}>
            <small style={{ color: "var(--text-muted)" }}>
              {fresh?.banned_titles && fresh.banned_titles.length > 0
                ? `已自动排除 ${fresh.banned_titles.length} 条历史否决项`
                : "将根据个人档案自动避开历史否决禁区"}
            </small>
            <button type="submit" className="primary" disabled={asking || rawText.trim() === ""}>
              {asking ? (
                <>
                  <span className="spinner" />
                  <span>正在匹配候选路线…</span>
                </>
              ) : (
                "获取推荐候选"
              )}
            </button>
          </div>
        </form>

        {askError !== null && (
          <div className="alert alert-danger" style={{ marginTop: "12px" }}>
            {askError}
          </div>
        )}
      </div>

      {verdictError !== null && (
        <div className="alert alert-danger" style={{ marginBottom: "16px" }}>
          <strong>裁定失败：</strong>
          {verdictError}
        </div>
      )}

      {fresh?.clarify && (
        <div className="alert alert-warning">
          <div>
            <strong>AI 追问槽：</strong>
            <span>{fresh.clarify.question}</span>
          </div>
          <small style={{ display: "block", marginTop: "4px" }}>
            补充此信息后再问一轮，推荐将更契合你的实际情况。
          </small>
        </div>
      )}

      {rows !== null && rows.length > 0 && (
        <div style={{ marginTop: "20px" }}>
          <div className="flex-between" style={{ marginBottom: "12px" }}>
            <h2>推荐候选清单（{rows.length} 条，待裁定 {pendingCount} 条）</h2>
            <small>采纳将新建阶段，否决将永久拉黑</small>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {rows.map((row) => {
              const isDecided = row.status !== "proposed";
              return (
                <div key={row.id} className="card" style={{ margin: 0, padding: "16px 20px" }}>
                  <div className="flex-between" style={{ marginBottom: "6px" }}>
                    <div className="flex-row gap-sm">
                      <span className="badge badge-in_progress">{KIND_LABELS[row.kind] ?? row.kind}</span>
                      <strong style={{ fontSize: "15px" }}>{row.title}</strong>
                      {row.isRecommended && (
                        <span className="badge badge-done">建议优先从这开始</span>
                      )}
                    </div>
                    <span className="badge badge-not_started">
                      {STATUS_LABELS[row.status] ?? row.status}
                    </span>
                  </div>

                  <p style={{ fontSize: "13px", color: "var(--text-main)", margin: "8px 0" }}>
                    {row.why}
                  </p>

                  <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "12px" }}>
                    建议深度：<strong>{row.depthTarget}</strong>
                    {row.planId && ` · 所属计划 #${row.planId}`}
                    {row.rejectReason && ` · 否决理由：${row.rejectReason}`}
                  </div>

                  {isDecided ? (
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                      {notes[row.id] ?? "该候选已有最终结论。"}
                    </div>
                  ) : (
                    <div className="flex-row gap-sm">
                      <button
                        type="button"
                        className="primary sm"
                        onClick={() =>
                          row.planId === null ? setAdoptingId(row.id) : onVerdict(row.id, true)
                        }
                        disabled={verdicting}
                      >
                        {row.planId === null ? "采纳（选择归属计划）" : "采纳（落入计划建阶段）"}
                      </button>

                      {rejectingId === row.id ? (
                        <div className="flex-row gap-sm">
                          <input
                            value={rejectReason}
                            onChange={(event) => setRejectReason(event.target.value)}
                            placeholder="否决理由（必填，进入永久禁区）"
                            style={{ width: "220px", fontSize: "12px" }}
                          />
                          <button
                            type="button"
                            className="danger sm"
                            onClick={() => onVerdict(row.id, false, rejectReason)}
                            disabled={verdicting || rejectReason.trim() === ""}
                          >
                            确认否决
                          </button>
                          <button
                            type="button"
                            className="sm"
                            onClick={() => setRejectingId(null)}
                            disabled={verdicting}
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="danger sm"
                          onClick={() => {
                            setRejectingId(row.id);
                            setRejectReason("");
                          }}
                          disabled={verdicting}
                        >
                          否决
                        </button>
                      )}
                    </div>
                  )}

                  {adoptingId === row.id && (
                    <div className="inline-edit-box" style={{ marginTop: "10px" }}>
                      <label style={{ fontSize: "12px", fontWeight: 600 }}>
                        请指定该候选采纳后要落到哪个计划：
                      </label>
                      <div className="flex-row gap-sm">
                        <select
                          value={adoptPlanChoice}
                          onChange={(event) => setAdoptPlanChoice(event.target.value)}
                        >
                          <option value="">选择现有计划…</option>
                          {plans.map((p) => (
                            <option key={p.id} value={p.id}>
                              计划 #{p.id}：{p.goal}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="primary sm"
                          onClick={() => onVerdict(row.id, true, undefined, Number(adoptPlanChoice))}
                          disabled={verdicting || adoptPlanChoice === ""}
                        >
                          确认归入该计划
                        </button>
                        <button
                          type="button"
                          className="sm"
                          onClick={() => setAdoptingId(null)}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  )}

                  {row.status === "accepted" && (
                    <div style={{ marginTop: "10px" }}>
                      <button
                        type="button"
                        className="sm"
                        onClick={() => setChattingId(chattingId === row.id ? null : row.id)}
                        disabled={verdicting}
                      >
                        {chattingId === row.id ? "收起意向规划对话" : "开展规划对话（细化后生成蓝图）"}
                      </button>
                    </div>
                  )}

                  {chattingId === row.id && (
                    <ChatBox
                      candidateId={row.id}
                      planId={landingPlans[row.id] ?? row.planId}
                      title={row.title}
                      plans={plans}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
