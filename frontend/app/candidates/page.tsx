"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createPlan,
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

/**
 * T14：候选清单的正式页面（「我不知道该学什么」的入口）。
 *
 * 与 `/ask` 时代那个临时入口的区别：
 * - **进来就有东西看**：挂载时用 `GET /api/candidates` 取最近一轮的候选（连你已经
 *   裁定过的也显示状态），不用重新问一次模型；重新问一次是「再要一轮」。
 * - 采纳 / 否决都在这里做完：采纳会在**候选自带的计划**里自动建一个同名阶段
 *   （后端的事），否决必须写理由——它成为下次的禁区。
 * - 采纳之后能就地开**规划对话**（T26，SPEC 决策 36）：先把意向聊清楚，再让它出一版
 *   蓝图（阶段 / 任务），蓝图去「蓝图待批」页勾选采纳。
 *
 * 取数、状态、错误在这里管；判定全在后端（SPEC 第 10 节：前端不做业务计算）。
 */

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
  expired: "已过期（被新一轮顶掉，不算否决）",
};

/** 一份清单里的一条候选：新问的和重看库里那轮的，都统一成这个形状。 */
type Row = {
  id: number;
  title: string;
  kind: string;
  why: string;
  depthTarget: string;
  isRecommended: boolean;
  status: string;
  rejectReason: string | null;
  /** 依据的档案 id。库里的候选没存这列，重看时是 null。 */
  basis: number[] | null;
  /** 这一轮针对的计划；null = 「新方向（不属于任何计划）」。采纳落点看它。 */
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

/** 助手那一侧存的是 JSON 原文——这里摊成人话显示（追问槽位同理）。 */
function ChatMessageView({ item }: { item: ChatMessage }) {
  if (item.role !== "assistant") {
    return (
      <p>
        <strong>我：</strong>
        {item.content}
      </p>
    );
  }
  let data: { questions?: string[]; ready?: boolean; note?: string } | null = null;
  try {
    data = JSON.parse(item.content);
  } catch {
    data = null; // 解析不了就原样显示，别让一条坏数据整页白掉
  }
  if (data === null || typeof data !== "object") {
    return (
      <p>
        <strong>它：</strong>
        {item.content}
      </p>
    );
  }
  return (
    <p>
      <strong>它：</strong>
      {data.note}
      {(data.questions ?? []).length > 0 && (
        <ol>
          {(data.questions ?? []).map((question, index) => (
            <li key={index}>{question}</li>
          ))}
        </ol>
      )}
      {data.ready === true && <small>（它说信息够了，可以出方案）</small>}
    </p>
  );
}

/**
 * 规划对话（T26）：聊清意向 → 出一版蓝图。
 *
 * 每一轮 1 次调用、整段上限 6 轮（SPEC 决策 6 修订 / 36）；出方案前至少要聊过一轮。
 * 聊完就去「蓝图待批」页勾选采纳——蓝图不会自己建进计划。
 */
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
  /** 服务器也说不出落点时，由你在这里指一次；说一次它就记进对话里了。 */
  const [chosenPlan, setChosenPlan] = useState("");

  /**
   * 落点以**服务器**的答案为准：`view.plan_id` 是「这段对话记在哪个计划名下」。
   * 为什么不能只信 prop（`landingPlans`）：那个映射活在页面内存里，刷新一次就没了——
   * 而「新方向」的候选自己查不出归属，于是「够了，出方案」会带着空计划发过去。
   */
  const effectivePlanId =
    view?.plan_id ?? (chosenPlan === "" ? planId : Number(chosenPlan));

  useEffect(() => {
    getPlanChat(candidateId, planId)
      .then(setView)
      .catch((cause: unknown) => setError(messageOf(cause, "取这段对话时出了意外错误")));
  }, [candidateId, planId]);

  async function refresh() {
    setView(await getPlanChat(candidateId, effectivePlanId));
  }

  async function onSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await sayPlanChat(candidateId, message, effectivePlanId);
      setMessage("");
      await refresh();
      setNotice(
        `第 ${done.turns_used} / ${done.max_turns} 轮：` +
          (done.reply.ready ? "它说信息够了，可以出方案。" : "它又问了几个问题，答完再发。"),
      );
    } catch (cause) {
      setError(messageOf(cause, "这一轮没成功"));
      await refresh().catch(() => undefined); // 你那句话后端已经记下了，刷新能看到
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await generateBlueprint(candidateId, effectivePlanId);
      await refresh();
      setNotice(
        `已出第 ${created.version} 版蓝图（提案 #${created.proposal_id}，` +
          `${created.stages.length} 个阶段）；` +
          (created.superseded_ids.length > 0
            ? `顶掉了旧的 #${created.superseded_ids.join("、#")}。`
            : "") +
          "去「蓝图待批」页勾选采纳——不勾的部分直接丢弃。",
      );
    } catch (cause) {
      setError(messageOf(cause, "出方案失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p>
        <strong>规划对话：{title}</strong>{" "}
        <small>
          （{view === null ? "…" : `已聊 ${view.turns_used} / ${view.max_turns} 轮`}
          {effectivePlanId !== null && `；记在计划 #${effectivePlanId} 名下`}
          {planId === null && "；这条候选是「新方向」采纳进来的，落在哪个计划待确认"}
          ）
        </small>
      </p>

      {view !== null && effectivePlanId === null && (
        <p>
          <label htmlFor={`chat-plan-${candidateId}`}>这段对话属于哪个计划（必选）：</label>{" "}
          <select
            id={`chat-plan-${candidateId}`}
            value={chosenPlan}
            onChange={(event) => setChosenPlan(event.target.value)}
          >
            <option value="">请选择…</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>
                计划 #{item.id}：{item.goal}
              </option>
            ))}
          </select>
          <br />
          <small>
            这条候选来自「新方向」的提问，它自己不带计划归属——落点是你采纳那一刻现选的，
            只活在那个页面上，刷新一次就只剩对话里记着的了。真的一次都没聊过、又刷新过页面时，
            在这里指一次；说一句它就记进这段对话，之后不用再指。
          </small>
        </p>
      )}

      <p>
        <small>
          先把意向聊清楚（能投入多少时间、先做哪块、想交出什么），聊够了让它出一版蓝图：
          阶段 → 任务，带每个阶段的交付物。蓝图是**提案**，要在「蓝图待批」页勾选才会建进计划。
        </small>
      </p>

      {(view?.messages ?? []).map((item, index) => (
        <ChatMessageView key={index} item={item} />
      ))}
      {view !== null && view.messages.length === 0 && (
        <p role="status">
          <small>还没聊过。说一句你想怎么安排，它就会开始问。</small>
        </p>
      )}

      <form onSubmit={onSend}>
        <textarea
          rows={2}
          cols={60}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="例如：我每周大概能投入 6 小时，想先把接口写通，最后交一个能访问的小服务"
          required
        />
        <br />
        <button type="submit" disabled={busy || message.trim() === "" || effectivePlanId === null}>
          {busy ? "正在说…" : "说这一句"}
        </button>{" "}
        <button
          type="button"
          onClick={onGenerate}
          disabled={busy || view === null || !view.can_generate || effectivePlanId === null}
          title={
            effectivePlanId === null
              ? "先指明这段对话属于哪个计划"
              : view !== null && !view.can_generate
                ? "先聊过一轮再出方案"
                : undefined
          }
        >
          够了，出方案
        </button>
      </form>

      {view?.blueprint != null && (
        <p>
          <small>
            这个计划当前有一版待裁定蓝图（提案 #{view.blueprint.id}）——
            去<Link href="/blueprints">蓝图待批</Link>页勾选。再出一次会顶掉它。
          </small>
        </p>
      )}
      {error !== null && (
        <p role="alert">
          <strong>对话失败：</strong>
          {error}
        </p>
      )}
      {notice !== null && (
        <p role="status">
          <small>{notice}</small>
        </p>
      )}
    </div>
  );
}

export default function CandidatesPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  /** 这一屏显示的是哪一轮：新问的那次带来源与禁区，从库里取的只知道原文。 */
  const [fresh, setFresh] = useState<FindResult | null>(null);
  const [storedText, setStoredText] = useState<string | null>(null);

  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  /** 计划列表与「这一轮针对哪个计划」（"" = 新方向，不属于任何计划）。 */
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [planChoice, setPlanChoice] = useState("");
  /** 「新方向」的候选采纳时要显式选落点；adoptingId = 正在选的那条。 */
  const [adoptingId, setAdoptingId] = useState<number | null>(null);
  const [adoptPlanId, setAdoptPlanId] = useState("");
  const [newPlanGoal, setNewPlanGoal] = useState("");
  /** 采纳落到哪个计划，按候选 id 记：规划对话要知道聊的是哪个计划（决策 36）。 */
  const [landingPlans, setLandingPlans] = useState<Record<number, number>>({});
  /** 正在展开规划对话的那条候选；null = 都收着。 */
  const [chattingId, setChattingId] = useState<number | null>(null);

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  /** 每条裁定后的回执文案，按候选 id 存。 */
  const [notes, setNotes] = useState<Record<number, string>>({});
  /** 正在填否决理由的那条；null = 没人在填。 */
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    listCandidates()
      .then((stored) => {
        setRows(rowsFromStored(stored));
        setStoredText(stored.raw_text);
      })
      .catch((cause: unknown) => setAskError(messageOf(cause, "取候选清单时出了意外错误")));
    listPlans()
      .then((items) => {
        setPlans(items);
        // 默认对准最新建的那个计划；下拉里随时能换成别的或「新方向」
        if (items.length > 0) {
          setPlanChoice((current) => (current === "" ? String(items[items.length - 1].id) : current));
        }
      })
      .catch(() => setPlans([]));
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAskError(null);
    setNotes({});
    setVerdictError(null);
    setRejectingId(null);
    try {
      const found = await findCandidates(rawText, planChoice === "" ? null : Number(planChoice));
      setFresh(found);
      setStoredText(null);
      setRows(rowsFromFind(found));
    } catch (cause) {
      setAskError(messageOf(cause, "问模型失败，原因不明"));
    } finally {
      setAsking(false);
    }
  }

  async function onVerdict(
    candidateId: number,
    accept: boolean,
    reason?: string,
    landingPlanId?: number | null,
  ) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accept, reason, landingPlanId);
      if (done.plan_id !== null) {
        // 记下落点：规划对话要用它（「新方向」的候选在库里没有归属可查）
        setLandingPlans((previous) => ({ ...previous, [candidateId]: done.plan_id as number }));
      }
      setRows((previous) =>
        (previous ?? []).map((row) =>
          row.id === candidateId
            ? { ...row, status: done.status, rejectReason: reason ?? null }
            : row,
        ),
      );
      setNotes((previous) => ({
        ...previous,
        [candidateId]:
          done.status === "accepted"
            ? `已采纳，并在计划 #${done.plan_id} 里自动建了阶段 #${done.node_id}——回计划表就能看到。`
            : "已否决。理由进了台账，下次「找」不会再出现这条。",
      }));
      setRejectingId(null);
      setRejectReason("");
      setAdoptingId(null);
      setAdoptPlanId("");
      setNewPlanGoal("");
    } catch (cause) {
      setVerdictError(messageOf(cause, "表态失败，原因不明"));
    } finally {
      setVerdicting(false);
    }
  }

  /** 「新方向」的候选：新建一个计划（目标默认取候选标题），再把它采纳进去。 */
  async function onCreatePlanAndAdopt(candidateId: number, goal: string) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const created = await createPlan(goal);
      setPlans(await listPlans());
      const done = await verdictCandidate(candidateId, true, undefined, created.id);
      setLandingPlans((previous) => ({ ...previous, [candidateId]: created.id }));
      setRows((previous) =>
        (previous ?? []).map((row) => (row.id === candidateId ? { ...row, status: done.status } : row)),
      );
      setNotes((previous) => ({
        ...previous,
        [candidateId]: `已采纳，新建了计划 #${created.id}「${created.goal}」并在里面建了阶段 #${done.node_id}。`,
      }));
      setAdoptingId(null);
      setNewPlanGoal("");
    } catch (cause) {
      setVerdictError(messageOf(cause, "新建计划并采纳失败，原因不明"));
    } finally {
      setVerdicting(false);
    }
  }

  const pendingCount = (rows ?? []).filter((row) => row.status === "proposed").length;

  return (
    <main>
      <h1>候选清单：我不知道该学什么</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/judge">判一份资料</Link>
        {" · "}
        <Link href="/blueprints">蓝图待批</Link>
        {" · "}
        <Link href="/proposals">待裁定提案</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          这里问的是<strong>「学什么方向」</strong>；要判断某一份具体资料值不值得学，
          去<Link href="/judge">判一份资料</Link>页问。
        </small>
      </p>

      {profile !== null && (
        <p>
          <small>
            判据来自你的长期档案：现有 <strong>{profile.items.length}</strong> 条
            {profile.items.length === 0 && "——先补档案，否则问不出东西"}
          </small>
        </p>
      )}

      <form onSubmit={onSubmit}>
        <p>
          <label htmlFor="plan">这一轮针对哪个计划：</label>
          <select id="plan" value={planChoice} onChange={(event) => setPlanChoice(event.target.value)}>
            <option value="">新方向（不属于任何计划）</option>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>
                计划 #{item.id}：{item.goal}
                {item.current_stage !== null && `（当前阶段：${item.current_stage.title}）`}
              </option>
            ))}
          </select>
          <br />
          <small>
            对准一个计划，候选会带着它的当前阶段来给（更贴手头这件事）；选「新方向」就是单纯找方向。
            采纳时就落到这个计划里——不再「偷偷落最新」。
          </small>
        </p>
        <p>
          <label htmlFor="raw">我的处境 / 想法（必填）：</label>
          <br />
          <textarea
            id="raw"
            rows={3}
            cols={60}
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            placeholder="例如：我不知道该学什么，方向是后端 + 能上线的项目"
            required
          />
        </p>
        <button type="submit" disabled={asking || verdicting}>
          {asking ? "正在问模型…（候选清单可能要一两分钟）" : "再要一轮候选清单"}
        </button>
      </form>

      {askError !== null && (
        <p role="alert">
          <strong>失败：</strong>
          {askError}
        </p>
      )}

      {verdictError !== null && (
        <p role="alert">
          <strong>表态失败：</strong>
          {verdictError}
        </p>
      )}

      {rows !== null && rows.length === 0 && (
        <p role="status">还没有候选：上面填一句处境，让模型给一份清单。</p>
      )}

      {rows !== null && rows.length > 0 && (
        <section>
          <h2>
            候选（{rows.length} 条，待裁定 {pendingCount} 条
            {fresh === null ? "，最近一轮" : "，刚问出来的"}）
          </h2>
          <p>
            <small>
              {fresh !== null ? (
                <>
                  来源：{fresh.source.name}
                  {fresh.source.networked ? "（联网）" : "（不联网，只给路线建议，链接要自己找）"}
                  ；依据 {fresh.profile_basis.total} 条档案，调了 {fresh.calls} 次模型。
                  {fresh.banned_titles.length > 0 && (
                    <>
                      <br />
                      本次禁区（你否决过的，模型不许再推）：{fresh.banned_titles.join("、")}
                    </>
                  )}
                  {fresh.feedback_lines.length > 0 && (
                    <>
                      <br />
                      上一轮还带上了 {fresh.feedback_lines.length} 轮「找」的流水（连你的否决理由原文）
                      ——不需要你复述，它自己记得。
                    </>
                  )}
                </>
              ) : (
                <>
                  {storedText === null ? "库里的候选" : `上一轮问的是「${storedText}」`}
                  ；依据的档案 id 只在问的那当刻的响应里（候选表没存这一列），
                  重看时看不到，要看依据就用上面「再要一轮」重新问。
                </>
              )}
            </small>
          </p>

          {fresh?.clarify != null && (
            <p>
              <strong>模型想先问你一句：</strong>
              {fresh.clarify.question}
              <br />
              <small>
                它说缺的是：{fresh.clarify.missing}。把答案写进上面那个输入框、再要一轮，
                清单会带着你的回答重给一遍。
                （追问只在问的那当刻的响应里，不落库；下面这份清单这一轮照给。）
              </small>
            </p>
          )}

          <ol>
            {rows.map((row) => {
              const isDecided = row.status !== "proposed";
              return (
                <li key={row.id}>
                  <p>
                    <strong>{row.title}</strong>
                    <small>
                      （{KIND_LABELS[row.kind] ?? row.kind}·建议深度 {row.depthTarget}
                      {row.isRecommended && "·建议从这里开始"}）
                    </small>
                    <br />
                    {row.why}
                    <br />
                    <small>
                      {row.basis !== null && (
                        <>
                          依据：
                          {row.basis.length === 0
                            ? "（无）"
                            : row.basis.map((id) => `#${id}`).join("、")}
                          {" · "}
                        </>
                      )}
                      候选 #{row.id} · {STATUS_LABELS[row.status] ?? row.status}
                      {row.rejectReason !== null && `（理由：${row.rejectReason}）`}
                    </small>
                  </p>

                  {isDecided ? (
                    <p role="status">
                      <small>{notes[row.id] ?? "这条已经裁定过了。"}</small>
                    </p>
                  ) : (
                    <p>
                      <button
                        type="button"
                        onClick={() =>
                          row.planId === null ? setAdoptingId(row.id) : onVerdict(row.id, true)
                        }
                        disabled={verdicting}
                      >
                        {row.planId === null
                          ? "采纳（先选落到哪个计划）"
                          : `采纳（落到计划 #${row.planId} 建阶段）`}
                      </button>{" "}
                      {rejectingId === row.id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => onVerdict(row.id, false, rejectReason)}
                            disabled={verdicting || rejectReason.trim() === ""}
                          >
                            确认否决
                          </button>{" "}
                          <button
                            type="button"
                            onClick={() => setRejectingId(null)}
                            disabled={verdicting}
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setRejectingId(row.id);
                            setRejectReason("");
                          }}
                          disabled={verdicting}
                        >
                          否决
                        </button>
                      )}
                    </p>
                  )}

                  {row.status === "accepted" && (
                    <p>
                      <button
                        type="button"
                        onClick={() => setChattingId(chattingId === row.id ? null : row.id)}
                        disabled={verdicting}
                      >
                        {chattingId === row.id ? "收起规划对话" : "规划对话（聊清意向后出方案）"}
                      </button>
                    </p>
                  )}

                  {chattingId === row.id && (
                    <ChatBox
                      candidateId={row.id}
                      planId={landingPlans[row.id] ?? row.planId}
                      title={row.title}
                      plans={plans}
                    />
                  )}

                  {adoptingId === row.id && (
                    <div>
                      <p>
                        <label htmlFor={`landing-${row.id}`}>采纳到哪个计划（必选）：</label>
                        <select
                          id={`landing-${row.id}`}
                          value={adoptPlanId}
                          onChange={(event) => setAdoptPlanId(event.target.value)}
                        >
                          <option value="">请选择…</option>
                          {plans.map((item) => (
                            <option key={item.id} value={item.id}>
                              计划 #{item.id}：{item.goal}
                            </option>
                          ))}
                        </select>{" "}
                        <button
                          type="button"
                          onClick={() => onVerdict(row.id, true, undefined, Number(adoptPlanId))}
                          disabled={verdicting || adoptPlanId === ""}
                        >
                          确认采纳到这个计划
                        </button>{" "}
                        <button type="button" onClick={() => setAdoptingId(null)} disabled={verdicting}>
                          取消
                        </button>
                      </p>
                      <p>
                        <label htmlFor={`new-plan-${row.id}`}>或者新建一个计划（目标默认取这条候选）：</label>
                        <input
                          id={`new-plan-${row.id}`}
                          value={newPlanGoal}
                          onChange={(event) => setNewPlanGoal(event.target.value)}
                          placeholder={row.title}
                        />
                        <button
                          type="button"
                          onClick={() => onCreatePlanAndAdopt(row.id, newPlanGoal.trim() || row.title)}
                          disabled={verdicting}
                        >
                          新建计划并采纳
                        </button>
                      </p>
                    </div>
                  )}

                  {rejectingId === row.id && (
                    <p>
                      <label htmlFor={`reason-${row.id}`}>否决理由（必填，进台账）：</label>
                      <input
                        id={`reason-${row.id}`}
                        value={rejectReason}
                        onChange={(event) => setRejectReason(event.target.value)}
                        placeholder="例如：和主线无关 / 现在不需要"
                      />
                    </p>
                  )}
                </li>
              );
            })}
          </ol>

          {fresh !== null && (
            <p>
              <small>
                <strong>建议先从「{fresh.recommended_start}」开始：</strong>
                {fresh.start_reason}
              </small>
            </p>
          )}
        </section>
      )}
    </main>
  );
}
