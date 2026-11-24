import { EvalFileInput } from '@/components/BotWorkshop/ServiceBotEvaluation/EvalFileInput';
import { StandardEvaluationFlowSteps } from '@/components/BotWorkshop/ServiceBotEvaluation/StandardEvaluationFlowSteps';
import { evaluationStatusLabel, evaluationStatusTone } from '@/components/BotWorkshop/ServiceBotEvaluation/evaluationStatus';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { useStandardEvaluationFlow } from '@/hooks/useStandardEvaluationFlow';
import { EVALUATION_GOAL_INPUT_ENABLED } from '@/services/botWorkshop/standardEvaluationWorkflow';
import { ExternalLink, RefreshCw } from 'lucide-react';

interface RestartEvaluationPanelProps {
  botId: string;
  botVersion: string;
  ownerId?: string;
  enabled?: boolean;
  canStart?: boolean;
  onFinished?: () => void;
}

const MASA_AGENT_EVALUATION_URL = 'https://masa.alipay.com/evaluation/agent/projects';

export function RestartEvaluationPanel({
  botId,
  botVersion,
  ownerId,
  enabled = true,
  canStart = true,
  onFinished,
}: RestartEvaluationPanelProps) {
  const {
    sampleSets,
    sampleSetLoading,
    sampleSetError,
    selectedSampleSetId,
    selectedSampleSet,
    setSelectedSampleSetId,
    reloadSampleSets,
    goalType,
    setGoalType,
    goalFile,
    setGoalFile,
    goalPrompt,
    setGoalPrompt,
    goalReady,
    running,
    steps,
    environment,
    evaluationTask,
    reportUrl,
    error,
    startEvaluation,
    retryStep,
    retryingStep,
  } = useStandardEvaluationFlow({
    botId,
    botVersion,
    ownerId,
    enabled,
    canStart,
    onFinished,
  });

  const sampleSetGuideVisible =
    Boolean(sampleSetError) || (!sampleSetLoading && sampleSets.length === 0);

  const goalItems = sampleSets.map((item) => ({
    id: item.uuid,
    label: `${item.name}（${item.sample_count} 条）`,
  }));


  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <div className="flex flex-col gap-2 border-b border-divider pb-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="m-0 text-sm font-semibold text-foreground">重新发起评测</h2>
            <p className="m-0 mt-1 text-xs leading-5 text-muted-foreground">
              适合需要重新发起评测并重新编排执行的链路。环境编排由 TeamClaw 后端完成。节点：创建评测环境 → 确认评测样本集 → 评测执行 → 评测报告 → 评测结果保存。
            </p>
          </div>
        </div>

        {sampleSetGuideVisible ? (
          <div
            className={`flex flex-col gap-2 rounded-lg border px-4 py-3 text-xs ${
              sampleSetError
                ? 'border-destructive/30 bg-destructive/5 text-destructive'
                : 'border-border bg-muted/20 text-muted-foreground'
            }`}
          >
            <p className="m-0 font-medium">
              {sampleSetError || '暂未获取到评测样本集。'}
            </p>
            <p className="m-0 break-words">
              请前往 MASA 平台智能体评测专区完成智能体注册。注册时，评测模式请选择
              <strong className="font-semibold">【评测环境 Eval 区】</strong>。
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <a
                className="inline-flex items-center gap-1 text-primary hover:underline"
                href={MASA_AGENT_EVALUATION_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink className="size-3.5" />
                前往 MASA 智能体评测专区
              </a>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<RefreshCw className="size-3.5" />}
                onClick={() => void reloadSampleSets()}
              >
                重新获取样本集
              </Button>
            </div>
          </div>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5 md:col-span-2">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="sample-set-id">
              评测样本集
            </label>
            <Select
              value={selectedSampleSetId}
              onValueChange={setSelectedSampleSetId}
              disabled={sampleSetLoading || running || sampleSets.length === 0}
            >
              <SelectTrigger id="sample-set-id" className="h-9 text-sm">
                <SelectValue placeholder={sampleSetLoading ? '正在加载样本集…' : '请选择评测样本集'} />
              </SelectTrigger>
              <SelectContent>
                {goalItems.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedSampleSet ? (
              <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
                当前样本数：{selectedSampleSet.sample_count}
                {selectedSampleSet.description ? ` · ${selectedSampleSet.description}` : null}
              </div>
            ) : null}
          </div>

          {EVALUATION_GOAL_INPUT_ENABLED ? (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="goal-type">
                评测目标格式
              </label>
              <Select value={goalType} onValueChange={(value) => setGoalType(value as typeof goalType)} disabled={running}>
                <SelectTrigger id="goal-type" className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="json">JSON</SelectItem>
                  <SelectItem value="semantic">自然语义</SelectItem>
                </SelectContent>
              </Select>
              <p className="m-0 text-xs text-muted-foreground">评测目标作为任务元数据保存，当前不驱动 MASA。</p>
            </div>
          ) : null}
          {EVALUATION_GOAL_INPUT_ENABLED && goalType === 'json' ? (
            <EvalFileInput
              label="评测目标文件"
              accept=".json"
              supportedFileTypes="JSON"
              file={goalFile}
              onSelect={setGoalFile}
            />
          ) : null}
        </div>
        {EVALUATION_GOAL_INPUT_ENABLED && goalType === 'semantic' ? (
          <Textarea
            className="min-h-28 text-sm"
            placeholder="例如：评估该 Bot 在金融政策问答场景下的准确率和安全性。"
            value={goalPrompt}
            onChange={(event) => setGoalPrompt(event.target.value)}
            disabled={running}
          />
        ) : null}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-sm font-semibold text-foreground">评测流程编排</h3>
          {environment ? (
            <Badge tone={environment.build_status === 'active' ? 'success' : 'primary'}>
              环境状态：{environment.build_status}
            </Badge>
          ) : null}
        </div>
        <StandardEvaluationFlowSteps
          steps={steps}
          running={running}
          enabled={enabled}
          canStart={canStart}
          retryingStep={retryingStep}
          onRetry={(step) => void retryStep(step)}
        />
        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-divider pt-4">
          <Button
            disabled={!goalReady || !selectedSampleSetId || sampleSetLoading || running || !canStart || !enabled}
            onClick={() => void startEvaluation()}
          >
            {running ? '评测执行中…' : '开始评测'}
          </Button>
        </div>
      </section>

      <section className="space-y-4">
        {evaluationTask ? (
          <div className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 text-xs md:grid-cols-3">
            <div>
              <p className="m-0 font-medium text-muted-foreground">任务编号</p>
              <p className="m-0 mt-1 truncate text-foreground">{evaluationTask.task_id || evaluationTask.id}</p>
            </div>
            <div>
              <p className="m-0 font-medium text-muted-foreground">任务状态</p>
              <p className="m-0 mt-1 text-foreground">
                <Badge tone={evaluationStatusTone(evaluationTask.status)}>
                  {evaluationStatusLabel(evaluationTask.status)}
                </Badge>
              </p>
            </div>
            <div>
              <p className="m-0 font-medium text-muted-foreground">评测环境</p>
              <p className="m-0 mt-1 text-foreground">{environment?.build_status ?? '—'}</p>
            </div>
          </div>
        ) : null}
        {reportUrl ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/30 p-4 text-xs">
            <div className="min-w-0">
              <p className="m-0 font-medium text-foreground">本次评测报告</p>
              <p className="m-0 mt-1 truncate text-muted-foreground">{reportUrl}</p>
            </div>
            <a
              className="inline-flex items-center gap-1 text-primary hover:underline"
              href={reportUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink className="size-3.5" />
              打开报告
            </a>
          </div>
        ) : null}
        {error ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-xs text-destructive">
            {error}
          </div>
        ) : null}
        <p className="m-0 text-xs leading-5 text-muted-foreground">
          评测结果用于校验服务 Bot 是否可以提交试运行。
        </p>
      </section>
    </div>
  );
}
