"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import {
  ApiError,
  createNode,
  createPlanWithContract,
  getPlan,
  listPlans,
  type EvidenceKind,
  type NodeLevel,
  type PlanSummary,
  type PlanTree,
} from "@/lib/api";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const LOAD_FAILED = "取计划时出了意外错误";

const LEVEL_LABELS: Record<NodeLevel, string> = {
  stage: "阶段",
  task: "任务",
  checkpoint: "周打卡",
};

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

export default function NewPage() {
  const [tree, setTree] = useState<PlanTree | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [targetPlanId, setTargetPlanId] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [pending, setPending] = useState(false);

  // 成果契约计划
  const [contractTitle, setContractTitle] = useState("");
  const [outcome, setOutcome] = useState("");
  const [value, setValue] = useState("");
  const [successStatement, setSuccessStatement] = useState("");
  const [criteria, setCriteria] = useState(["", ""]);
  const [evidenceKind, setEvidenceKind] = useState<EvidenceKind>("repository");
  const [evidenceDescription, setEvidenceDescription] = useState("");
  const [goal, setGoal] = useState("");

  // 建节点
  const [level, setLevel] = useState<NodeLevel>("task");
  const [title, setTitle] = useState("");
  const [deliverable, setDeliverable] = useState("");
  const [purpose, setPurpose] = useState("");
  const [whyNow, setWhyNow] = useState("");
  const [stageCriteria, setStageCriteria] = useState(["", ""]);
  const [stageEvidenceKind, setStageEvidenceKind] = useState<EvidenceKind>("repository");
  const [stageEvidenceDescription, setStageEvidenceDescription] = useState("");
  const [contractCriterionIds, setContractCriterionIds] = useState<string[]>([]);
  const [parentId, setParentId] = useState("");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    getPlan()
      .then((data) => {
        setTree(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
    listPlans()
      .then(setPlans)
      .catch(() => setPlans([]));
  }, []);

  async function refresh() {
    try {
      setTree(
        await getPlan(targetPlanId === "" ? undefined : Number(targetPlanId)),
      );
      setPlans(await listPlans());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  const planId = targetPlanId === "" ? (tree?.plan?.id ?? null) : Number(targetPlanId);
  const stages = tree?.stages ?? [];

  async function onCreatePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const created = await createPlanWithContract({
        goal: goal.trim() || undefined,
        contract: {
          title: contractTitle.trim(),
          outcome: outcome.trim(),
          value: value.trim(),
          success_statement: successStatement.trim(),
          acceptance_criteria: criteria.map((text) => ({ text: text.trim(), required: true })),
          evidence_requirements: [{ kind: evidenceKind, required: true, description: evidenceDescription.trim() }],
        },
      });
      setFeedback({ ok: true, text: `成果计划已建立 #${created.id}：${created.contract.title}（契约第 ${created.contract.version} 版）` });
      setContractTitle("");
      setOutcome("");
      setValue("");
      setSuccessStatement("");
      setCriteria(["", ""]);
      setEvidenceDescription("");
      setGoal("");
      setTargetPlanId(String(created.id));
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建成果计划失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onCreateNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (planId === null) return;
    setPending(true);
    setFeedback(null);
    try {
      const stageCriterionInputs = stageCriteria.filter((text) => text.trim()).map((text) => ({ text: text.trim(), required: true }));
      const stageEvidenceInputs = [{ kind: stageEvidenceKind, required: true, description: stageEvidenceDescription.trim() }];
      const created = await createNode({
        planId,
        level,
        title,
        parentId: level === "stage" ? null : Number(parentId),
        deliverable: level === "stage" ? deliverable : null,
        dueDate: dueDate === "" ? null : dueDate,
        purpose: level === "stage" ? purpose : null,
        whyNow: level === "stage" ? whyNow : null,
        acceptanceCriteria: level === "stage" && tree?.completion_mode === "outcome" ? stageCriterionInputs : null,
        evidenceRequirements: level === "stage" && tree?.completion_mode === "outcome" ? stageEvidenceInputs : null,
        contractCriterionIds: level === "stage" && tree?.completion_mode === "outcome" ? contractCriterionIds : null,
      });
      setFeedback({
        ok: true,
        text: `已在计划 #${planId} 下创建${LEVEL_LABELS[level]} #${created.id}：${created.title}`,
      });
      setTitle("");
      setDeliverable("");
      setPurpose("");
      setWhyNow("");
      setStageCriteria(["", ""]);
      setStageEvidenceDescription("");
      setContractCriterionIds([]);
      setDueDate("");
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建节点失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="dark min-h-screen px-6 pb-10 pt-20 md:px-10">
      <div className="max-w-5xl mx-auto space-y-10">
        <div className="space-y-2">
          <Link
            href="/workbench"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary transition-colors mb-4"
          >
            <ChevronLeft className="size-4" />
            返回工作台
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">新建计划与节点</h1>
          <p className="text-sm text-muted-foreground">
            手动新建顶级计划，或为现有计划手动补充阶段、任务与周打卡节点。
          </p>
        </div>

        {loadError !== null && (
          <div className="p-4 rounded-md bg-destructive/10 text-destructive border border-destructive/20 text-sm">
            <strong>加载失败：</strong>
            {loadError}
          </div>
        )}

        {feedback !== null && (
          <div
            className={`p-4 rounded-md border text-sm ${
              feedback.ok
                ? "bg-success/10 text-success border-success/20"
                : "bg-destructive/10 text-destructive border-destructive/20"
            }`}
          >
            {feedback.text}
          </div>
        )}

        <div className="grid md:grid-cols-2 gap-6 items-start">
          <Card className="card-border-gradient">
            <CardHeader>
              <CardTitle>① 创建成果契约计划</CardTitle>
              <CardDescription>先说清结果、价值、怎样算够，再进入执行；这不会创建一个伪装成已验收的旧计划。</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={onCreatePlan} className="space-y-4">
                <div className="space-y-2"><Label htmlFor="contract-title">成果名称（必填）</Label><Input id="contract-title" value={contractTitle} onChange={(event) => setContractTitle(event.target.value)} placeholder="例如：可演示的 API 项目" required className="input-glow" /></div>
                <div className="space-y-2"><Label htmlFor="outcome">最终结果（必填）</Label><textarea id="outcome" value={outcome} onChange={(event) => setOutcome(event.target.value)} placeholder="最终要交出什么外部结果" required rows={2} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm" /></div>
                <div className="space-y-2"><Label htmlFor="value">为什么值得做（必填）</Label><textarea id="value" value={value} onChange={(event) => setValue(event.target.value)} placeholder="它对你的主线、痛点或机会有什么价值" required rows={2} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm" /></div>
                <div className="space-y-2"><Label htmlFor="success-statement">做到什么算够（必填）</Label><textarea id="success-statement" value={successStatement} onChange={(event) => setSuccessStatement(event.target.value)} placeholder="一句可观察的成功定义" required rows={2} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm" /></div>
                <fieldset className="space-y-2"><legend className="text-sm font-medium">验收条件（至少 2 条，必填）</legend>{criteria.map((criterion, index) => <Input key={index} id={`criterion-${index + 1}`} aria-label={`验收条件 ${index + 1}`} value={criterion} onChange={(event) => setCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder={`验收条件 ${index + 1}：如何观察到结果已达到标准`} required className="input-glow" />)}</fieldset>
                <div className="space-y-2"><Label htmlFor="evidence-kind">必需证据类型</Label><select id="evidence-kind" value={evidenceKind} onChange={(event) => setEvidenceKind(event.target.value as EvidenceKind)} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm"><option value="repository">代码仓库</option><option value="link">网页链接</option><option value="document">文档</option><option value="demo">演示 / 录屏</option><option value="screenshot">截图</option><option value="text">文字结果</option><option value="other">其他</option></select></div>
                <div className="space-y-2"><Label htmlFor="evidence-description">证据要求说明（必填）</Label><Input id="evidence-description" value={evidenceDescription} onChange={(event) => setEvidenceDescription(event.target.value)} placeholder="验收时需要看到什么证据" required className="input-glow" /></div>
                <div className="space-y-2"><Label htmlFor="goal">计划显示目标（可选）</Label><Input id="goal" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="留空则使用成果名称" className="input-glow" /></div>
                <div className="flex items-center justify-between mt-4 gap-4"><p className="text-xs text-muted-foreground">后端会校验完整契约并一次性创建 outcome 计划。</p><Button type="submit" disabled={pending || !contractTitle.trim() || !outcome.trim() || !value.trim() || !successStatement.trim() || criteria.some((item) => !item.trim()) || !evidenceDescription.trim()} className="btn-primary">{pending ? "正在创建…" : "创建成果计划"}</Button></div>
              </form>
            </CardContent>
          </Card>

          <Card className="card-border-gradient">
            <CardHeader>
              <CardTitle>② 添加三级节点</CardTitle>
              {planId === null && (
                <CardDescription>
                  尚未选择目标计划。请先在左侧新建计划，或在下拉中指定。
                </CardDescription>
              )}
            </CardHeader>
            <CardContent>
              {planId !== null && (
                <form onSubmit={onCreateNode} className="space-y-4">
                  <div className="space-y-2">
                    <Label>目标归属计划：</Label>
                    <Select
                      value={targetPlanId === "" ? "latest" : targetPlanId}
                      onValueChange={(value) => {
                        const next = value === "latest" ? "" : value;
                        setTargetPlanId(next);
                        getPlan(next === "" ? undefined : Number(next)).then(
                          setTree,
                        );
                      }}
                    >
                      <SelectTrigger className="input-glow">
                        <SelectValue placeholder="请选择计划" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="latest">
                          （最新建的计划）#{tree?.plan?.id} {tree?.plan?.goal}
                        </SelectItem>
                        {plans.map((p) => (
                          <SelectItem key={p.id} value={String(p.id)}>
                            #{p.id}：{p.goal}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="grid grid-cols-[1fr_2fr] gap-4">
                    <div className="space-y-2">
                      <Label>节点层级：</Label>
                      <Select
                        value={level}
                        onValueChange={(value) => setLevel(value as NodeLevel)}
                      >
                        <SelectTrigger className="input-glow">
                          <SelectValue placeholder="选择层级" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="task">任务（可打勾/跳过）</SelectItem>
                          <SelectItem value="stage">阶段（大阶段容器）</SelectItem>
                          <SelectItem value="checkpoint">周打卡（节奏检查点）</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="title">节点标题（必填）：</Label>
                      <Input
                        id="title"
                        value={title}
                        onChange={(event) => setTitle(event.target.value)}
                        placeholder="输入阶段或任务标题"
                        required
                        className="input-glow"
                      />
                    </div>
                  </div>

                  {level === "stage" ? (
                    <div className="space-y-4">
                      <div className="space-y-2"><Label htmlFor="deliverable">阶段交付结果</Label><Input id="deliverable" value={deliverable} onChange={(event) => setDeliverable(event.target.value)} placeholder="例如：输出一份可测试运行的代码仓库" className="input-glow" /></div>
                      {tree?.completion_mode === "outcome" && tree.contract && <>
                        <div className="space-y-2"><Label htmlFor="stage-purpose">阶段目的</Label><textarea id="stage-purpose" value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder="这一阶段为最终成果解决什么问题" rows={2} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm" required /></div>
                        <div className="space-y-2"><Label htmlFor="stage-why-now">为什么现在做</Label><textarea id="stage-why-now" value={whyNow} onChange={(event) => setWhyNow(event.target.value)} placeholder="为什么当前阶段排在这里" rows={2} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm" required /></div>
                        <fieldset className="space-y-2"><legend className="text-sm font-medium">阶段验收条件（至少 1 条）</legend>{stageCriteria.map((criterion, index) => <Input key={index} id={`stage-criterion-${index + 1}`} aria-label={`阶段验收条件 ${index + 1}`} value={criterion} onChange={(event) => setStageCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder={`条件 ${index + 1}：如何观察到这一阶段达标`} required className="input-glow" />)}</fieldset>
                        <fieldset className="space-y-2"><legend className="text-sm font-medium">承接成果契约条件（可选）</legend>{tree.contract.acceptance_criteria.map((criterion) => <label key={criterion.id} className="flex items-start gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={contractCriterionIds.includes(criterion.id)} onChange={(event) => setContractCriterionIds((current) => event.target.checked ? [...current, criterion.id] : current.filter((id) => id !== criterion.id))} className="mt-0.5 accent-white" />{criterion.text}</label>)}</fieldset>
                        <div className="space-y-2"><Label htmlFor="stage-evidence-kind">阶段必需证据类型</Label><select id="stage-evidence-kind" value={stageEvidenceKind} onChange={(event) => setStageEvidenceKind(event.target.value as EvidenceKind)} className="input-glow w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm"><option value="repository">代码仓库</option><option value="link">网页链接</option><option value="document">文档</option><option value="demo">演示 / 录屏</option><option value="screenshot">截图</option><option value="text">文字结果</option><option value="other">其他</option></select></div>
                        <div className="space-y-2"><Label htmlFor="stage-evidence-description">阶段证据说明</Label><Input id="stage-evidence-description" value={stageEvidenceDescription} onChange={(event) => setStageEvidenceDescription(event.target.value)} placeholder="验收时需要看到什么" required className="input-glow" /></div>
                      </>}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <Label>所属父阶段（必填）：</Label>
                      <Select
                        value={parentId}
                        onValueChange={(value) => setParentId(value)}
                        required
                      >
                        <SelectTrigger className="input-glow">
                          <SelectValue placeholder="请选择归属哪个阶段…" />
                        </SelectTrigger>
                        <SelectContent>
                          {stages.map((stage) => (
                            <SelectItem key={stage.id} value={String(stage.id)}>
                              阶段 #{stage.id}：{stage.title}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="due">建议截止日期（可选）：</Label>
                    <Input
                      id="due"
                      type="date"
                      value={dueDate}
                      onChange={(event) => setDueDate(event.target.value)}
                      className="input-glow block"
                    />
                  </div>

                  <div className="flex items-center justify-between mt-4">
                    <p className="text-xs text-muted-foreground">新建节点实时进台账</p>
                    <Button
                      type="submit"
                      disabled={
                        pending ||
                        title.trim() === "" ||
                        (level !== "stage" && parentId === "") ||
                        (level === "stage" && tree?.completion_mode === "outcome" && (purpose.trim() === "" || whyNow.trim() === "" || stageCriteria.some((item) => !item.trim()) || stageEvidenceDescription.trim() === ""))
                      }
                      className="btn-primary"
                    >
                      {pending ? "正在添加…" : "添加节点"}
                    </Button>
                  </div>
                </form>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
