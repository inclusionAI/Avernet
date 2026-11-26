import { Button } from '@/components/ui/Button';
import { rsiErrorMessage } from '@/services/rsi/errors';
import { getRsiStrategyOutput, isRsiTerminal, type RsiRun, type RsiStrategyOutput } from '@/services/rsi/service';
import type { RsiScope } from '@/types/rsi';
import { useEffect, useState } from 'react';

const outcomes: Record<RsiStrategyOutput['outcome'], string> = {
  not_called: '未调用模型',
  no_changes: '模型未提出有效修改',
  changes_proposed: '模型提出了修改',
  invalid_proposal: '模型输出未通过提案校验',
  cancelled: '已取消提案流程',
};
const reasons: Record<string, string> = {
  no_authorized_markdown: '授权范围内没有可更新的现有 Markdown 文件。',
  no_session_evidence: '没有可用的近期导出会话证据，未调用模型。',
  empty_edits: '模型返回了空 edits 列表。',
  identical_content: '模型返回的文件内容与父版本完全相同。',
  changed_markdown: '模型建议更新以下文件；是否成功提交请查看候选列表和 Run 结果。',
  invalid_model_proposal: '返回格式、路径范围或非空约束不满足要求，未提交候选。',
  cancelled_before_proposing: '提出建议前已取消。',
  cancelled_before_model: '调用模型前已取消。',
  cancelled_after_model: '模型返回后已取消，未提交候选。',
};

/** Mounted with a Bot/Run key; stale network results must never cross that boundary. */
export function RsiStrategyOutputPanel({ scope, run }: { scope: RsiScope; run: RsiRun }) {
  const [output, setOutput] = useState<RsiStrategyOutput | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const terminal = isRsiTerminal(run.status);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setOutput(null);
    setLoaded(false);
    setError('');
    const load = async () => {
      try {
        const result = await getRsiStrategyOutput(scope, run.id);
        if (!active) return;
        setOutput(result.output);
        setLoaded(true);
        setError('');
      } catch (cause) {
        if (active) setError(rsiErrorMessage(cause, '策略输出读取失败，不能据此判断模型没有提出修改。'));
      } finally {
        if (active && !terminal) timer = setTimeout(() => void load(), 3000);
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [scope, run.id, run.attempt, terminal, retry]);
  return (
    <section aria-label="策略模型输出" className="space-y-2 rounded-md border border-input p-3 text-xs">
      <h4 className="text-sm font-medium">策略模型输出</h4>
      <p className="text-muted-foreground">
        展示基础脱敏后的最终返回，不额外记录提示词、完整会话或隐藏推理。仍请按敏感内容处理；提案不代表评测通过或已应用。
      </p>
      {error ? (
        <>
          <p role="alert" className="text-destructive">
            {error}
          </p>
          <Button variant="outline" onClick={() => setRetry((value) => value + 1)}>
            重试读取策略输出
          </Button>
        </>
      ) : null}
      {!loaded && !error ? <p>正在读取策略输出…</p> : null}
      {loaded && !output ? (
        <p className="text-muted-foreground">
          {terminal
            ? '此执行次数未保存策略模型输出，无法回溯具体提案或不修改的理由（历史版本、调用前失败等情况）。'
            : '本次执行暂未保存模型输出，运行中将自动刷新。'}
        </p>
      ) : null}
      {output ? (
        <>
          <p className="font-medium">
            {outcomes[output.outcome]} · attempt {output.attempt}
          </p>
          <p>{reasons[output.reason] ?? output.reason}</p>
          {output.rationale ? (
            <div>
              <h5 className="font-medium">修改 / 不修改的理由</h5>
              <p className="whitespace-pre-wrap break-words">{output.rationale}</p>
            </div>
          ) : null}
          {output.edits.map((edit, index) => (
            <details key={`${index}:${edit.path}`}>
              <summary className="cursor-pointer break-all">
                {edit.path}（{edit.changed ? '建议修改' : '内容未变'}）
              </summary>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">
                {edit.content}
              </pre>
            </details>
          ))}
          {output.raw_output ? (
            <details>
              <summary className="cursor-pointer">
                模型返回文本（已脱敏{output.raw_output_truncated ? '，已截断' : ''}）
              </summary>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">
                {output.raw_output}
              </pre>
            </details>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
