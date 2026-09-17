"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  askMaterial,
  decideProposal,
  getProfile,
  listProposals,
  type AskResult,
  type BlueprintPayload,
  type Judgment,
  type PlanReplanPayload,
  type ProfileView,
  type Proposal,
  type StageAdvancePayload,
  type MaterialJudgmentPayload,
} from "@/lib/api";

/**
 * T14：提案裁定页（`/ask` 页并入这里之后，它同时承担两件事）。
 *
 * 上半页是「判一个资料」：提交一份资料，模型给四问判断，结论落成一条待裁定提案。
 * 下半页是**待裁定提案**列表，按 `kind` 分流渲染：
 *
 * - `material_judgment` 资料判断：四问答案 + 依据的档案 id；批准只记账（判断本身已是结论）。
 * - `stage_advance` 阶段推进：批准 = 进下一阶段；若后面没有更多阶段，批准 = **把计划收尾**
 *   （唯一会真的改结构的一种）。
 * - `plan_replan` 计划重排：三个出路选一个，批准后只记下方向——改节点字段的写入口还没有，
 *   界面把选中方向的原文摆出来，照着手工改。
 * - `plan_blueprint` 计划蓝图（T26）：**按勾选建树**——勾中的阶段 / 任务才会进计划，
 *   没勾的直接丢弃（树是版本化的，想要可以再出一版）。
 * - `profile_change` 档案变更：还没有生产者（未来的档案提炼会走这里），先按通用形态显示。
 *
 * 裁定全部走台账并留理由（SPEC 第 8 节：AI 只产出提案，写入必须经你裁定）。
 */

const QUESTION_LABELS: Record<keyof Judgment, string> = {
  worth_learning: "① 值不值得学",
  depth_target: "② 学到什么程度",
  intensity: "③ 板块分级",
  time_budget: "④ 时间预算",
};

const KIND_TITLES: Record<string, string> = {
  material_judgment: "资料判断",
  stage_advance: "阶段推进",
  plan_replan: "计划重排",
  plan_blueprint: "计划蓝图",
  profile_change: "档案变更",
};

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 四问答案的公共渲染：每问都要指回它的档案 id（后端把「指不回去」判为不合格）。 */
function JudgmentView({ judgment }: { judgment: Judgment }) {
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

/** 勾选的公共渲染：一条路径就是一段。`"2"` = 整段；`"2.1"` = 其中第 2 件任务。 */
function blueprintPaths(payload: BlueprintPayload): string[] {
  return (payload.stages ?? []).map((_, index) => String(index));
}

/**
 * 这条蓝图当前勾了哪些。没有记录时默认**整份都要**——不勾就点批准等于全采纳
 * （后端也是这个口径：不传 `selected` = 整份）。
 */
function selectionOf(
  proposal: Proposal,
  selections: Record<number, string[]>,
): string[] {
  const stored = selections[proposal.id];
  if (stored !== undefined) return stored;
  if (proposal.kind !== "plan_blueprint") return [];
  return blueprintPaths(proposal.payload as unknown as BlueprintPayload);
}

export default function ProposalsPage() {
  const [profile, setProfile] = useState<ProfileView | null>(null);

  const [rawText, setRawText] = useState("");
  const [asking, setAsking] = useState(false);
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [askError, setAskError] = useState<string | null>(null);

  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [decideError, setDecideError] = useState<string | null>(null);
  /** 裁定后的回执文案，按提案 id 存（裁定完这条就从列表里消失）。 */
  const [notes, setNotes] = useState<string[]>([]);
  /** 驳回理由与重排方向，都按提案 id 存。 */
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [options, setOptions] = useState<Record<number, string>>({});
  /** 蓝图的勾选（T26），按提案 id 存；没有记录 = 整份都要（见 `selectionOf`）。 */
  const [selections, setSelections] = useState<Record<number, string[]>>({});
  /** 正在填驳回理由的那条；null = 没人在填。 */
  const [rejectingId, setRejectingId] = useState<number | null>(null);

  // setState 放进 .then 回调（effect 体内同步 setState 会被 eslint 拦）
  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    listProposals()
      .then(setProposals)
      .catch((cause: unknown) => setListError(messageOf(cause, "取提案时出了意外错误")));
  }, []);

  function refresh() {
    listProposals()
      .then(setProposals)
      .catch((cause: unknown) => setListError(messageOf(cause, "取提案时出了意外错误")));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAskError(null);
    setAskResult(null);
    try {
      const result = await askMaterial(rawText);
      setAskResult(result);
      refresh(); // 新提案立刻出现在下面的列表里
    } catch (cause) {
      setAskError(messageOf(cause, "问模型失败，原因不明"));
    } finally {
      setAsking(false);
    }
  }

  async function onDecide(proposalId: number, approved: boolean, option?: string) {
    setDeciding(true);
    setDecideError(null);
    // 收尾回执要说清动的是哪个计划：先把这条提案的 plan_id 记下来（它马上会从列表消失）
    const target = (proposals ?? []).find((item) => item.id === proposalId);
    const planId = typeof target?.payload?.plan_id === "number" ? target.payload.plan_id : null;
    const selected = target === undefined ? [] : selectionOf(target, selections);
    try {
      const done = await decideProposal(proposalId, {
        approved,
        reason: reasons[proposalId] ?? undefined,
        option,
        // 蓝图：把这一版勾中的部分带过去（没勾的部分后端直接丢弃）
        selected: target?.kind === "plan_blueprint" ? selected : undefined,
      });
      const text = approved
        ? done.effect === "plan_closed"
          ? `已批准：计划 #${planId} 收尾了——计划表里它不再算当前计划。`
          : done.effect === "blueprint_built"
            ? `已批准：计划 #${done.built?.plan_id ?? planId} 里建了 ` +
              `${done.built?.stages.length ?? 0} 个新阶段、${done.built?.tasks.length ?? 0} 件任务` +
              `${done.built?.notes.length ? `。${done.built.notes.join("；")}` : "。"}`
            : done.effect === "replan_recorded"
              ? `已批准，记下你选的方向「${option}」——改节点字段的写入口还没有，照这条方向的原文手工改。`
              : "已批准，只记账：这项不会改计划或档案。"
        : "已驳回，理由进了台账。";
      setNotes((previous) => [text, ...previous]);
      setProposals((previous) => (previous ?? []).filter((item) => item.id !== proposalId));
      setRejectingId(null);
      refresh();
    } catch (cause) {
      setDecideError(messageOf(cause, "裁定失败，原因不明"));
    } finally {
      setDeciding(false);
    }
  }

  return (
    <main>
      <h1>待裁定提案</h1>
      <p>
        <Link href="/">← 回计划表</Link>
        {" · "}
        <Link href="/candidates">候选清单</Link>
        {" · "}
        <Link href="/profile">长期档案</Link>
      </p>

      <p>
        <small>
          这里做两件事：上面<strong>判一份具体资料</strong>（四问），下面裁定 agent
          算出来的待办提案。要问「学什么方向」去
          <Link href="/candidates">候选清单</Link>页。
        </small>
      </p>

      <section>
        <h2>判一个资料：要不要学</h2>
        {profile !== null && (
          <p>
            <small>
              判据来自你的长期档案：现有 <strong>{profile.items.length}</strong> 条
              {profile.items.length === 0 && "——先补档案，否则只会得到「依据不足」"}
            </small>
          </p>
        )}
        <form onSubmit={onSubmit}>
          <p>
            <label htmlFor="raw">我发现了什么（必填）：</label>
            <br />
            <textarea
              id="raw"
              rows={3}
              cols={60}
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="例如：我看到一个 Rust 异步编程教程，要不要学？"
              required
            />
          </p>
          <button type="submit" disabled={asking || deciding}>
            {asking ? "正在问模型…（四问几秒到几十秒）" : "问一下"}
          </button>
        </form>

        {askError !== null && (
          <p role="alert">
            <strong>失败：</strong>
            {askError}
          </p>
        )}

        {askResult !== null && (
          <section>
            <h3>四问结果</h3>
            <JudgmentView judgment={askResult.judgment} />
            <p>
              <small>
                已存成<strong>待裁定</strong>提案 #{askResult.proposal_id}（依据了{" "}
                {askResult.profile_basis.total} 条档案，调了 {askResult.calls} 次模型），
                就在下面那条列表里。
              </small>
            </p>
          </section>
        )}
      </section>

      <section>
        <h2>待裁定（{proposals === null ? "…" : proposals.length} 条）</h2>

        {listError !== null && (
          <p role="alert">
            <strong>取提案失败：</strong>
            {listError}
          </p>
        )}
        {decideError !== null && (
          <p role="alert">
            <strong>裁定失败：</strong>
            {decideError}
          </p>
        )}

        {notes.length > 0 && (
          <ul role="status">
            {notes.map((text, index) => (
              <li key={index}>
                <small>{text}</small>
              </li>
            ))}
          </ul>
        )}

        {proposals !== null && proposals.length === 0 && (
          <p role="status">没有待裁定的提案。</p>
        )}

        <ol>
          {(proposals ?? []).map((proposal) => (
            <li key={proposal.id}>
              <ProposalCard
                proposal={proposal}
                deciding={deciding}
                rejecting={rejectingId === proposal.id}
                reason={reasons[proposal.id] ?? ""}
                option={options[proposal.id] ?? ""}
                onReason={(value) => setReasons((p) => ({ ...p, [proposal.id]: value }))}
                onOption={(value) => setOptions((p) => ({ ...p, [proposal.id]: value }))}
                onSelection={(value) => setSelections((p) => ({ ...p, [proposal.id]: value }))}
                selection={selectionOf(proposal, selections)}
                onStartReject={() => setRejectingId(proposal.id)}
                onCancelReject={() => setRejectingId(null)}
                onApprove={() => onDecide(proposal.id, true, options[proposal.id])}
                onReject={() => onDecide(proposal.id, false)}
              />
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}

function ProposalCard({
  proposal,
  deciding,
  rejecting,
  reason,
  option,
  selection,
  onReason,
  onOption,
  onSelection,
  onStartReject,
  onCancelReject,
  onApprove,
  onReject,
}: {
  proposal: Proposal;
  deciding: boolean;
  rejecting: boolean;
  reason: string;
  option: string;
  selection: string[];
  onReason: (value: string) => void;
  onOption: (value: string) => void;
  onSelection: (value: string[]) => void;
  onStartReject: () => void;
  onCancelReject: () => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  const isReplan = proposal.kind === "plan_replan"; // 批准重排必须先选一个方向
  const isBlueprint = proposal.kind === "plan_blueprint";
  // 蓝图一个都没勾时批准会被后端拒（400）——索性在按钮上先拦住，并说清为什么
  const nothingTicked = isBlueprint && selection.length === 0;

  return (
    <>
      <p>
        <strong>{KIND_TITLES[proposal.kind] ?? proposal.kind}</strong>
        <small>（提案 #{proposal.id}，提出于 {proposal.created_at.slice(0, 16).replace("T", " ")}）</small>
      </p>
      {proposal.reason !== null && <p>{proposal.reason}</p>}

      <ProposalBody
        proposal={proposal}
        option={option}
        onOption={onOption}
        selection={selection}
        onSelection={onSelection}
      />

      <p>
        <button
          type="button"
          onClick={onApprove}
          disabled={deciding || (isReplan && option === "") || nothingTicked}
          title={
            isReplan && option === ""
              ? "先选一个方向"
              : nothingTicked
                ? "至少要勾一个阶段或任务"
                : undefined
          }
        >
          批准
        </button>{" "}
        {rejecting ? (
          <>
            <button type="button" onClick={onReject} disabled={deciding || reason.trim() === ""}>
              确认驳回
            </button>{" "}
            <button type="button" onClick={onCancelReject} disabled={deciding}>
              取消
            </button>
          </>
        ) : (
          <button type="button" onClick={onStartReject} disabled={deciding}>
            驳回
          </button>
        )}
      </p>

      {rejecting && (
        <p>
          <label htmlFor={`reason-${proposal.id}`}>驳回理由（必填，进台账）：</label>
          <input
            id={`reason-${proposal.id}`}
            value={reason}
            onChange={(event) => onReason(event.target.value)}
            placeholder="例如：这份判断依据太单薄 / 时机不对"
          />
        </p>
      )}
    </>
  );
}

/** 按 `kind` 分流渲染 payload——形状是后端定的，这里只按期转一次类型。 */
function ProposalBody({
  proposal,
  option,
  onOption,
  selection,
  onSelection,
}: {
  proposal: Proposal;
  option: string;
  onOption: (value: string) => void;
  selection: string[];
  onSelection: (value: string[]) => void;
}) {
  if (proposal.kind === "material_judgment") {
    const payload = proposal.payload as unknown as MaterialJudgmentPayload;
    return (
      <>
        <p>
          <small>资料原文：{payload.source_text}</small>
        </p>
        {payload.judgment !== undefined && <JudgmentView judgment={payload.judgment} />}
        <p>
          <small>
            批准只记账：这份判断本身已是结论，不会改计划或档案；想落成新方向，去
            <Link href="/candidates">候选清单</Link>页采纳一条候选。
          </small>
        </p>
      </>
    );
  }

  if (proposal.kind === "stage_advance") {
    const payload = proposal.payload as unknown as StageAdvancePayload;
    return (
      <>
        <p>
          <small>
            阶段「{payload.stage_title}」的检查点已全部收尾
            {payload.done !== undefined && `（完成 ${payload.done} / 跳过 ${payload.skipped ?? 0}）`}。
          </small>
        </p>
        {payload.next_stage_id === null ? (
          <p>
            <strong>后面没有更多阶段了——批准就是把计划 #{payload.plan_id} 收尾。</strong>
          </p>
        ) : (
          <p>
            批准 = 进入下一阶段「{payload.next_stage_title}」。
            <small>（批准不改数据：当前阶段是「第一个没收尾的阶段」，上一个收尾后它自己就往前走了。）</small>
          </p>
        )}
      </>
    );
  }

  if (proposal.kind === "plan_replan") {
    const payload = proposal.payload as unknown as PlanReplanPayload;
    return (
      <>
        <p>
          <small>
            {payload.week} · {payload.why}
            {payload.lag_days > 0 && `（落后 ${payload.lag_days} 天）`}
          </small>
        </p>
        <p>选一个方向（批准时记进台账）：</p>
        <ul>
          {(payload.options ?? []).map((item) => (
            <li key={item.kind}>
              <label>
                <input
                  type="radio"
                  name={`option-${proposal.id}`}
                  value={item.kind}
                  checked={option === item.kind}
                  onChange={() => onOption(item.kind)}
                />
                <strong>{item.label}</strong>：{item.detail}
              </label>
            </li>
          ))}
        </ul>
        <p>
          <small>
            批准只记下这个方向——<strong>改节点字段的写入口还没有</strong>，
            计划要照上面这句话手工改。
          </small>
        </p>
      </>
    );
  }

  if (proposal.kind === "plan_blueprint") {
    const payload = proposal.payload as unknown as BlueprintPayload;
    const stages = payload.stages ?? [];
    const whole = (index: number) => selection.includes(String(index));
    const tickedTask = (index: number, taskIndex: number) =>
      whole(index) || selection.includes(`${index}.${taskIndex}`);

    /** 勾/取消一个阶段：勾 = 整段（丢掉它下面逐条勾的），取消 = 这一段全不要。 */
    function toggleStage(index: number) {
      if (whole(index)) {
        onSelection(selection.filter((item) => item !== String(index)));
        return;
      }
      onSelection([
        ...selection.filter((item) => !item.startsWith(`${index}.`)),
        String(index),
      ]);
    }

    /** 勾/取消一件任务：整段被勾着时，先把它拆成逐条勾，再动这一条。 */
    function toggleTask(index: number, taskIndex: number) {
      const path = `${index}.${taskIndex}`;
      if (whole(index)) {
        const rest = stages[index].tasks
          .map((_, other) => `${index}.${other}`)
          .filter((other) => other !== path);
        onSelection([...selection.filter((item) => item !== String(index)), ...rest]);
        return;
      }
      onSelection(
        selection.includes(path)
          ? selection.filter((item) => item !== path)
          : [...selection, path],
      );
    }

    return (
      <>
        <p>
          计划 #{payload.plan_id} 的这一版蓝图（
          {stages.length} 个阶段，
          {stages.reduce((total, item) => total + (item.tasks?.length ?? 0), 0)} 件任务
          {payload.candidate_id !== undefined && `，出自候选 #${payload.candidate_id}`}）。
          <br />
          <small>
            <strong>勾中的才会建进计划，没勾的直接丢弃</strong>——树是版本化的，
            想要别的东西可以沿对话再出一版（新版会顶掉这一版）。
          </small>
        </p>
        <p>
          <small>目标：{payload.goal}</small>
        </p>
        <ol>
          {stages.map((item, index) => (
            <li key={`${item.title}-${index}`}>
              <label>
                <input
                  type="checkbox"
                  checked={whole(index)}
                  onChange={() => toggleStage(index)}
                />
                <strong>{item.title}</strong>
              </label>
              {item.why !== "" && (
                <>
                  <br />
                  <small>{item.why}</small>
                </>
              )}
              <br />
              <small>要交的东西：{item.deliverable}</small>
              {item.tasks?.length > 0 && (
                <ul>
                  {item.tasks.map((task, taskIndex) => (
                    <li key={`${task.title}-${taskIndex}`}>
                      <label>
                        <input
                          type="checkbox"
                          checked={tickedTask(index, taskIndex)}
                          onChange={() => toggleTask(index, taskIndex)}
                        />
                        {task.title}
                      </label>
                      <small>{task.due_date === null ? "（没定日期）" : `（${task.due_date}）`}</small>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
        <p>
          <small>
            同名阶段不会重复建：采纳候选时已经建了同名阶段，任务会挂到它下面。
            它「要交的东西」写不进去（改节点字段的写入口还没有），批准后会告诉你哪几条这样。
          </small>
        </p>
      </>
    );
  }

  return (
    <p>
      <small>
        这类提案（{proposal.kind}）还没有生产者，界面按通用形态显示：
        <code>{JSON.stringify(proposal.payload)}</code>
      </small>
    </p>
  );
}
