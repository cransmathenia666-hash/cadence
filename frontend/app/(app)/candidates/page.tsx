"use client";

import { useEffect, useState } from "react";
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
  type FindResult,
  type PlanSummary,
  type ProfileView,
  type PlanChatView,
} from "@/lib/api";

import { PromptInput } from "@/components/candidates/prompt-input";
import { CandidateCard } from "@/components/candidates/candidate-card";
import { ChatBox } from "@/components/candidates/chat-box";

function rowsFromFind(found: FindResult) {
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
    landingPlanId: null,
    shape: found.shape,
    steps: found.steps,
  }));
}

function rowsFromStored(stored: CandidateList) {
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
    landingPlanId: item.landing_plan_id,
    shape: item.shape,
    steps: item.steps,
  }));
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
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
  const [clarifyAnswer, setClarifyAnswer] = useState("");

  const [verdicting, setVerdicting] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [adoptingId, setAdoptingId] = useState<number | null>(null);
  
  const [freshPlanId, setFreshPlanId] = useState<number | null>(null);
  const [landingPlans, setLandingPlans] = useState<Record<number, number>>({});
  const [decidedNow, setDecidedNow] = useState<Record<number, string>>({});
  const [chattingId, setChattingId] = useState<number | null>(null);

  const [chatViews, setChatViews] = useState<Record<number, PlanChatView | null>>({});
  const [chatBusy, setChatBusy] = useState<Record<number, boolean>>({});
  const [chatChosenPlan, setChatChosenPlan] = useState<Record<number, string>>({});

  useEffect(() => {
    getProfile().then(setProfile).catch(() => setProfile(null));
    listPlans().then(setPlans).catch(() => setPlans([]));
    listCandidates().then(setStored).catch(() => setStored(null));
  }, []);

  async function openChat(id: number | null) {
    if (id === null || chattingId === id) {
      setChattingId(null);
      return;
    }
    setChattingId(id);
    const r = rows?.find(row => row.id === id);
    const pId = landingPlans[id] ?? r?.landingPlanId ?? r?.planId ?? undefined;
    try {
      const view = await getPlanChat(id, pId);
      setChatViews(prev => ({ ...prev, [id]: view }));
    } catch (err) {
      console.error(err);
    }
  }

  async function ask(text: string, clarAns: string | null) {
    setAsking(true);
    setAskError(null);
    setFresh(null);
    try {
      const found = await findCandidates(
        text,
        planChoice === "" ? undefined : Number(planChoice),
        clarAns
      );
      setFresh(found);
      setStored(null);
      setClarifyAnswer("");
    } catch (cause) {
      setAskError(messageOf(cause, "请求候选清单失败，原因不明"));
    } finally {
      setAsking(false);
    }
  }

  async function onAdoptIntoNewPlan(candidateId: number, title: string, goalStr: string) {
    const goal = goalStr.trim() || title;
    setVerdicting(true);
    setVerdictError(null);
    setFreshPlanId(null);
    let createdId: number | null = null;
    try {
      const created = await createPlan(goal);
      createdId = created.id;
      setFreshPlanId(created.id);
      setPlans(await listPlans());
    } catch (cause) {
      setVerdictError(messageOf(cause, "新建计划失败，原因不明"));
      setVerdicting(false);
      return;
    }
    await onVerdict(candidateId, true, undefined, createdId);
  }

  async function onVerdict(candidateId: number, accepted: boolean, reason?: string, explicitPlanId?: number) {
    setVerdicting(true);
    setVerdictError(null);
    try {
      const done = await verdictCandidate(candidateId, accepted, reason, explicitPlanId);
      setNotes((prev) => ({
        ...prev,
        [candidateId]: accepted
          ? `已采纳：已在计划 #${done.plan_id ?? ""} 里建立初始阶段「${done.id}」`
          : "已否决：此主题已列入不可逆禁区。",
      }));
      if (done.plan_id != null) {
        setLandingPlans((prev) => ({ ...prev, [candidateId]: Number(done.plan_id) }));
      }
      setDecidedNow((prev) => ({
        ...prev,
        [candidateId]: accepted ? "accepted" : "rejected",
      }));
      setRejectingId(null);
      setAdoptingId(null);
      setStored(await listCandidates());
    } catch (cause) {
      setVerdictError(messageOf(cause, "裁定候选失败"));
    } finally {
      setVerdicting(false);
    }
  }

  const listed = fresh ? rowsFromFind(fresh) : stored ? rowsFromStored(stored) : null;
  const rows = listed?.map((row) => 
    decidedNow[row.id] === undefined ? row : { ...row, status: decidedNow[row.id] }
  ) ?? null;
  
  function getRow(id: number) {
    return rows?.find(r => r.id === id);
  }

  const pendingCount = (rows ?? []).filter(r => r.status === "proposed").length;
  const isPath = rows !== null && rows.length > 0 && rows[0].shape === "path";

  async function handleChatSend(candidateId: number, message: string, effPlanId: number) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    try {
      await sayPlanChat(candidateId, message, effPlanId);
      const view = await getPlanChat(candidateId, effPlanId);
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
    } catch (err) {
      console.error(err);
      setVerdictError(messageOf(err, "规划对话请求失败"));
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  async function handleChatGenerate(candidateId: number, effPlanId: number) {
    setChatBusy(prev => ({ ...prev, [candidateId]: true }));
    try {
      await generateBlueprint(candidateId, effPlanId);
      const view = await getPlanChat(candidateId, effPlanId);
      setChatViews(prev => ({ ...prev, [candidateId]: view }));
    } catch (err) {
      setVerdictError(messageOf(err, "生成蓝图方案失败"));
    } finally {
      setChatBusy(prev => ({ ...prev, [candidateId]: false }));
    }
  }

  function renderRightPanel() {
    if (chattingId === null) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center border border-white/[0.04] bg-surface2/30 rounded-3xl p-8 text-center shadow-sm">
          <div className="text-[13px] text-white/[0.35] font-medium tracking-wide">
            采纳候选后开始规划
          </div>
        </div>
      );
    }
    const id = chattingId;
    const r = getRow(id);
    const effPlanId = r ? (landingPlans[r.id] ?? r.landingPlanId ?? r.planId) : null;
    return (
      <ChatBox
        view={chatViews[id] ?? null}
        effectivePlanId={effPlanId}
        plans={plans}
        chosenPlan={chatChosenPlan[id] ?? ""}
        setChosenPlan={(val) => setChatChosenPlan(prev => ({ ...prev, [id]: val }))}
        onSend={(msg) => handleChatSend(id, msg, effPlanId!)}
        onGenerate={() => handleChatGenerate(id, effPlanId!)}
        busy={chatBusy[id] ?? false}
      />
    );
  }

  return (
    <div className="flex flex-col h-full bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-white/[0.02] via-background to-background min-h-screen pt-20 pb-24">
      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12 grid grid-cols-1 lg:grid-cols-[1fr_380px] xl:grid-cols-[1fr_420px] gap-8 xl:gap-12 items-start">
        
        {/* Left Column (Main) */}
        <div className="flex flex-col w-full min-w-0">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-[20px] font-semibold text-primary/90 tracking-tight mb-2">候选清单</h1>
              <p className="text-[13px] text-white/50">
                向决策引擎表达困惑或目标。引擎结合档案筛选路线，否决的题目绝不再推。
              </p>
            </div>
            {profile !== null && (
              <span className="px-3 py-1.5 bg-white/[0.03] border border-white/[0.06] rounded-full text-[12px] font-medium text-white/60 shadow-sm shrink-0 ml-4">
                长期档案 {profile.items.length} 条
              </span>
            )}
          </div>

          <PromptInput 
            plans={plans}
            planChoice={planChoice}
            setPlanChoice={setPlanChoice}
            rawText={rawText}
            setRawText={setRawText}
            onSubmit={(e) => { e.preventDefault(); ask(rawText, null); }}
            asking={asking}
            bannedCount={fresh?.banned_titles?.length ?? 0}
          />

          {askError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
              {askError}
            </div>
          )}

          {verdictError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl p-4 mb-8 text-[13px]">
              <strong>裁定失败：</strong>{verdictError}
              {freshPlanId !== null && (
                <div className="mt-1 text-red-400/80">
                  新计划已经建好了 (计划 #{freshPlanId})，落点已选中它，再点一次「确认归入」即可。
                </div>
              )}
            </div>
          )}

          {fresh?.clarify && (
            <div className="bg-amber-500/10 border border-amber-500/20 rounded-2xl p-6 mb-8">
              <div className="flex gap-3">
                <div className="text-[14px] font-medium text-amber-500/90 mt-0.5 shrink-0">AI 追问槽：</div>
                <div className="text-[14px] text-amber-400/90 leading-relaxed">{fresh.clarify.question}</div>
              </div>
              <div className="text-[12px] text-amber-500/60 mt-2 mb-5">
                关于你的一件事，一句话就能答。答完再问一轮，排序会贴得更准；问过的事它不会再问第二遍。
              </div>
              <form
                onSubmit={(e) => { e.preventDefault(); ask(rawText, clarifyAnswer); }}
                className="flex items-center gap-3"
              >
                <input
                  value={clarifyAnswer}
                  onChange={(e) => setClarifyAnswer(e.target.value)}
                  placeholder="一句话回答，例如：每周大概 5 小时"
                  className="flex-1 bg-black/40 border border-amber-500/20 rounded-xl px-4 py-2.5 text-[13px] text-primary/90 focus:border-amber-500/40 outline-none"
                  disabled={asking}
                />
                <button
                  type="submit"
                  disabled={asking || !clarifyAnswer.trim()}
                  className="px-5 py-2.5 bg-amber-500/20 text-amber-500 hover:bg-amber-500/30 rounded-xl text-[13px] font-medium transition-colors disabled:opacity-50"
                >
                  {asking ? "再问一轮中…" : "带着回答再问一轮"}
                </button>
              </form>
            </div>
          )}

          {rows && rows.length > 0 && (
            <div className="mt-4">
              <div className="flex items-end justify-between mb-5">
                <h2 className="text-[14px] font-medium text-primary/80">
                  {isPath 
                    ? `这一轮它给的是一条路（含 ${rows[0]?.steps.length ?? 0} 个先后步骤）`
                    : `推荐候选清单（${rows.length} 条，待裁定 ${pendingCount} 条）`}
                </h2>
                <span className="text-[11px] text-white/40">
                  {isPath ? "整条采纳或整条否决；步骤的去留在出蓝图时定" : "采纳将新建阶段，否决将永久拉黑"}
                </span>
              </div>
              
              <div className="flex flex-col">
                {rows.map((row, index) => {
                  const isBigCard = row.isRecommended || isPath || index === 0;
                  return (
                    <CandidateCard
                      key={row.id}
                      row={row}
                      plans={plans}
                      notes={notes}
                      isDecided={row.status !== "proposed"}
                      verdicting={verdicting}
                      rejectingId={rejectingId}
                      setRejectingId={setRejectingId}
                      rejectReason={rejectReason}
                      setRejectReason={setRejectReason}
                      adoptingId={adoptingId}
                      setAdoptingId={setAdoptingId}
                      onVerdict={onVerdict}
                      onAdoptIntoNewPlan={onAdoptIntoNewPlan}
                      chattingId={chattingId}
                      setChattingId={openChat}
                      renderChatBox={() => null} /* not used anymore */
                      isBigCard={isBigCard}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Right Column (ChatBox/Blueprint) */}
        <div className="sticky top-24 hidden lg:flex flex-col h-[calc(100vh-8rem)] w-full">
          {renderRightPanel()}
        </div>

        {/* Mobile Right Panel fallback if they scroll down */}
        <div className="lg:hidden mt-12 w-full">
          {chattingId !== null && renderRightPanel()}
        </div>

      </div>
    </div>
  );
}
