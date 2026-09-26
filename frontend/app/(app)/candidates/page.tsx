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
import {
  getFindSession,
  patchFindSession,
  pushFindRound,
  type FindRound,
} from "@/components/candidates/find-session";

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
  // 「找」的进行时状态以模块级会话为准（切页不丢，见 find-session.ts）；
  // 这里只是它的镜像，挂载时取一次初值，之后每次变更双写。
  const session = getFindSession();
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);

  const [planChoice, setPlanChoice] = useState("");
  const [rawText, setRawTextState] = useState(session.rawText);
  const [asking, setAskingState] = useState(session.asking);
  const [askError, setAskErrorState] = useState<string | null>(session.error);
  const [fresh, setFreshState] = useState<FindResult | null>(session.fresh);
  const [stored, setStoredState] = useState<CandidateList | null>(session.stored);
  const [history, setHistory] = useState<FindRound[]>(session.history);
  const [clarifyAnswer, setClarifyAnswer] = useState("");

  const setRawText = (val: string) => {
    setRawTextState(val);
    patchFindSession({ rawText: val });
  };
  const setAsking = (val: boolean) => {
    setAskingState(val);
    patchFindSession({ asking: val });
  };
  const setAskError = (val: string | null) => {
    setAskErrorState(val);
    patchFindSession({ error: val });
  };
  const setFresh = (val: FindResult | null) => {
    setFreshState(val);
    patchFindSession({ fresh: val });
  };
  const setStored = (val: CandidateList | null) => {
    setStoredState(val);
    patchFindSession({ stored: val });
  };

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
    // 等待期间不清旧结果——最坏要等几分钟，界面不能变白（2026-09-26 走查）
    const previousClarify = fresh?.clarify ?? null;
    setAsking(true);
    setAskError(null);
    try {
      const found = await findCandidates(
        text,
        planChoice === "" ? undefined : Number(planChoice),
        clarAns
      );
      pushFindRound({
        requestText: text,
        clarifyAnswered:
          clarAns && previousClarify
            ? { question: previousClarify.question, answer: clarAns }
            : null,
        recommended: found.recommended_start ?? null,
        count: found.candidates.length,
        shape: found.shape,
      });
      setHistory([...getFindSession().history]);
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
      // 采纳成功即点亮右栏进入规划对话
      if (accepted) {
        await openChat(candidateId);
      }
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
        <ChatBox
          view={null}
          effectivePlanId={null}
          plans={plans}
          chosenPlan={""}
          setChosenPlan={() => {}}
          onSend={async () => {}}
          onGenerate={async () => {}}
          busy={asking}
          clarifyData={fresh?.clarify}
          onClarifySubmit={(e) => { e.preventDefault(); ask(rawText, clarifyAnswer); }}
          clarifyAnswer={clarifyAnswer}
          setClarifyAnswer={setClarifyAnswer}
        />
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
      
      {/* Full width Prompt Input Row */}
      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12 mb-10 mt-4">
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
      </div>

      <div className="max-w-[1400px] mx-auto w-full px-4 md:px-8 xl:px-12 grid grid-cols-1 lg:grid-cols-[440px_1fr] xl:grid-cols-[480px_1fr] gap-8 xl:gap-10 items-start">
        
        {/* Left Column (Main) */}
        <div className="flex flex-col w-full min-w-0">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-4">
              <h1 className="text-[20px] font-semibold text-primary/90 tracking-tight">候选清单</h1>
              {profile !== null && (
                <span className="px-3 py-1 bg-white/[0.03] border border-white/[0.06] rounded-full text-[11px] font-medium text-white/60 shadow-sm shrink-0">
                  长期档案 {profile.items.length} 条
                </span>
              )}
            </div>
          </div>

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

          {asking && (
            <div className="shimmer bg-surface2/40 border border-white/[0.06] rounded-xl p-4 mb-6 text-[13px] text-white/60">
              正在找候选…通常 20–30 秒，最坏几分钟。上一轮结果与已答的追问都还在下面，切走再回来也不会丢。
            </div>
          )}

          {history.length > 0 && (
            <div className="mb-8 flex flex-col gap-2">
              <div className="text-[11px] uppercase tracking-widest font-semibold text-white/[0.35]">
                历史轮次（新在上）
              </div>
              {history.map((round, index) => (
                <div
                  key={index}
                  className="text-[12px] text-white/40 leading-relaxed border-l-2 border-white/[0.08] pl-3"
                >
                  第 {history.length - index} 轮 ·〈
                  {round.requestText.slice(0, 36)}
                  {round.requestText.length > 36 ? "…" : ""}〉
                  {round.clarifyAnswered && (
                    <>
                      {" "}
                      · 追问「{round.clarifyAnswered.question.slice(0, 22)}
                      {round.clarifyAnswered.question.length > 22 ? "…" : ""}」答：
                      {round.clarifyAnswered.answer.slice(0, 16)}
                      {round.clarifyAnswered.answer.length > 16 ? "…" : ""}
                    </>
                  )}
                  {round.recommended && (
                    <>
                      {" "}
                      · 推荐「{round.recommended.slice(0, 18)}
                      {round.recommended.length > 18 ? "…" : ""}」（{round.count} 条候选）
                    </>
                  )}
                </div>
              ))}
            </div>
          )}

          {rows && rows.length > 0 ? (
            <div className="mt-2">
              <div className="flex items-end justify-between mb-5">
                <h2 className="text-[14px] font-medium text-primary/80">
                  {isPath 
                    ? `一条完整路径（含 ${rows[0]?.steps.length ?? 0} 个步骤）`
                    : `推荐候选（${rows.length} 条，待裁定 ${pendingCount} 条）`}
                </h2>
                <span className="text-[11px] text-white/40">
                  {isPath ? "整条采纳或否决，去留在生成蓝图时调整" : "采纳将新建阶段，否决将永久拉黑"}
                </span>
              </div>
              
              <div className="flex flex-col gap-1">
                {rows.map((row) => (
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
                    setChattingId={openChat}
                  />
                ))}
              </div>
            </div>
          ) : (
            !asking && !askError && (
              <div className="flex flex-col items-center justify-center py-20 text-center border border-white/[0.04] rounded-2xl bg-surface2/30">
                <div className="text-[14px] text-white/60 mb-2 font-medium">输入你的需求，或等待引擎推荐</div>
                <div className="text-[12px] text-white/40">候选清单将展示在此处</div>
              </div>
            )
          )}
        </div>

        {/* Right Column (ChatBox/Blueprint) */}
        <div className="sticky top-24 hidden lg:flex flex-col h-[calc(100vh-8rem)] w-full">
          {renderRightPanel()}
        </div>

        {/* Mobile Right Panel fallback if they scroll down */}
        <div className="lg:hidden mt-12 w-full h-[600px]">
          {renderRightPanel()}
        </div>

      </div>
    </div>
  );
}
