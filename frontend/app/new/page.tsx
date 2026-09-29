"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import {
  ApiError,
  createNode,
  createPlan,
  getPlan,
  listPlans,
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

  // 建计划
  const [goal, setGoal] = useState("");

  // 建节点
  const [level, setLevel] = useState<NodeLevel>("task");
  const [title, setTitle] = useState("");
  const [deliverable, setDeliverable] = useState("");
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
      const created = await createPlan(goal);
      setFeedback({ ok: true, text: `成功建立计划 #${created.id}：${created.goal}` });
      setGoal("");
      setTargetPlanId(String(created.id));
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "建计划失败，原因不明") });
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
      const created = await createNode({
        planId,
        level,
        title,
        parentId: level === "stage" ? null : Number(parentId),
        deliverable: level === "stage" ? deliverable : null,
        dueDate: dueDate === "" ? null : dueDate,
      });
      setFeedback({
        ok: true,
        text: `已在计划 #${planId} 下创建${LEVEL_LABELS[level]} #${created.id}：${created.title}`,
      });
      setTitle("");
      setDeliverable("");
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
              <CardTitle>① 创建新计划</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={onCreatePlan} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="goal">计划核心目标（必填）：</Label>
                  <Input
                    id="goal"
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                    placeholder="例如：Web 后端工程基础与实战上线"
                    required
                    className="input-glow"
                  />
                </div>
                <div className="flex items-center justify-between mt-4">
                  <p className="text-xs text-muted-foreground">
                    新计划建好后可直接作为活动目标
                  </p>
                  <Button
                    type="submit"
                    disabled={pending || goal.trim() === ""}
                    className="btn-primary"
                  >
                    {pending ? "正在创建…" : "创建新计划"}
                  </Button>
                </div>
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
                    <div className="space-y-2">
                      <Label htmlFor="deliverable">阶段交付物（验收指标）：</Label>
                      <Input
                        id="deliverable"
                        value={deliverable}
                        onChange={(event) => setDeliverable(event.target.value)}
                        placeholder="例如：输出一份可测试运行的代码仓库"
                        className="input-glow"
                      />
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
                        (level !== "stage" && parentId === "")
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
