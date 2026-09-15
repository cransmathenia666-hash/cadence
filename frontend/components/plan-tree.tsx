import type { Checkpoint, PlanTree, Stage } from "@/lib/api";

/**
 * 计划表里"长什么样"的那一半：只把后端给的数据画出来。
 *
 * 为什么和取数分开：取数、加载态、错误态在页面（容器）里管；这里只管展示。
 * 这样以后要改样式只动这个文件，不会碰到请求逻辑。
 */

const STATUS_LABELS: Record<string, string> = {
  not_started: "未开始",
  in_progress: "进行中",
  done: "完成",
  stuck: "卡住",
  skipped: "跳过",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** 落后的说法。null 表示无从判断（没定完成日，或已跳过）。 */
function lagLabel(lagDays: number | null): string {
  if (lagDays === null) return "无到期日";
  if (lagDays > 0) return `落后 ${lagDays} 天`;
  if (lagDays < 0) return `提前 ${-lagDays} 天`;
  return "按时";
}

function CheckpointItem({ checkpoint }: { checkpoint: Checkpoint }) {
  return (
    <li>
      {checkpoint.title} — <strong>{statusLabel(checkpoint.status)}</strong>
      {checkpoint.due_date !== null && <>，到期 {checkpoint.due_date}</>}
      {checkpoint.lag_days !== null && <>，{lagLabel(checkpoint.lag_days)}</>}
    </li>
  );
}

function StageItem({ stage }: { stage: Stage }) {
  const { progress } = stage;
  const open = progress.open_titles;

  return (
    <li>
      <h3>
        {stage.title} — <strong>{statusLabel(stage.status)}</strong>
      </h3>
      <ul>
        {stage.deliverable !== null && <li>交付物：{stage.deliverable}</li>}
        {stage.due_date !== null && <li>计划完成日：{stage.due_date}</li>}
        {stage.lag_days !== null && <li>{lagLabel(stage.lag_days)}</li>}
        <li>
          检查点 {progress.settled} / {progress.total} 收尾（完成 {progress.done}、跳过{" "}
          {progress.skipped}）
          {progress.complete
            ? "，已全部收尾"
            : open.length > 0
              ? `，还开着：${open.join("、")}`
              : ""}
        </li>
      </ul>
      {stage.checkpoints.length === 0 ? (
        <p>这个阶段还没有检查点。</p>
      ) : (
        <ul>
          {stage.checkpoints.map((checkpoint) => (
            <CheckpointItem key={checkpoint.id} checkpoint={checkpoint} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** 计划的全部信息。数据全部来自 `GET /api/plan`，这里不做任何业务计算。 */
export function PlanTreeView({ tree }: { tree: PlanTree }) {
  const { plan, current_stage: currentStage, lag, stages } = tree;

  if (plan === null) {
    return <p role="status">后端连上了，但库里还没有计划。先去「建计划 / 建节点」建一个。</p>;
  }

  return (
    <div>
      <h2>计划：{plan.goal}</h2>
      <ul>
        <li>状态：{plan.status}</li>
        <li>建于：{plan.valid_from}</li>
        <li>
          落后量：
          {lag.behind ? `落后 ${lag.lag_days} 天` : "没有落后"}
          {lag.worst !== null && (
            <>（最堵的是「{lag.worst.title}」，到期 {lag.worst.due_date ?? "未定"}）</>
          )}
        </li>
      </ul>

      <h2>当前阶段</h2>
      {currentStage === null ? (
        <p>没有进行中的阶段——要么全收尾了，要么还没建阶段。</p>
      ) : (
        <ul>
          <li>名称：{currentStage.title}</li>
          <li>状态：{statusLabel(currentStage.status)}</li>
          {currentStage.deliverable !== null && <li>交付物：{currentStage.deliverable}</li>}
          <li>
            进度：{currentStage.progress.settled} / {currentStage.progress.total} 收尾
          </li>
        </ul>
      )}

      <h2>阶段与检查点</h2>
      {stages.length === 0 ? (
        <p>还没有阶段。</p>
      ) : (
        <ol>
          {stages.map((stage) => (
            <StageItem key={stage.id} stage={stage} />
          ))}
        </ol>
      )}
    </div>
  );
}
