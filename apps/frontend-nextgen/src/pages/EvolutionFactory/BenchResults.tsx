import type { JsonObject } from '@/domain/evolution/types';
import { array, object, present, ResultCard, RunLink, text, Values } from './OutputPrimitives';
import { Hint } from './shared';

export function BenchResults({ output, stepId }: { output: JsonObject; stepId?: string }) {
  const benchCases = object(output.benchCases),
    domains = object(output.benchDomains),
    baseline = object(output.baseline);
  return (
    <>
      {present(benchCases) && (
        <ResultCard title="训练 / 测试 Case">
          <Values
            rows={[
              ['训练集', benchCases.trainCount],
              ['测试集', benchCases.testCount],
            ]}
          />
          {present(domains) && (
            <div className="mt-3 space-y-1 break-all text-xs text-muted-foreground">
              <p>Train Domain：{text(domains.trainBenchDomainId)}</p>
              <p>Test Domain：{text(domains.testBenchDomainId)}</p>
            </div>
          )}
          {array(benchCases.items).length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-primary">
                查看 Case 明细 · {array(benchCases.items).length}
              </summary>
              <div className="mt-3 divide-y divide-divider">
                {array(benchCases.items).map((item, i) => {
                  const template = object(item.template);
                  return (
                    <div key={i} className="space-y-1 py-2 text-xs">
                      <p className="break-all">
                        <span className="mr-2 rounded bg-background px-1.5 py-0.5">{text(item.split)}</span>
                        {text(item.sourceCaseId ?? item.taskId)}
                      </p>
                      <p className="break-all text-muted-foreground">
                        {text(template.templateName)} · v{text(template.version)}
                      </p>
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </ResultCard>
      )}
      {present(baseline) && (
        <ResultCard title="Train / Test Baseline">
          <div className="grid gap-3 sm:grid-cols-2">
            {(['train', 'test'] as const).map((role) => {
              const result = object(baseline[role]);
              if (!present(result)) return null;
              const values = object(result.metrics),
                producedHere = Boolean(stepId && result.producerStepId === stepId);
              return (
                <div key={role} className="min-w-0 rounded-lg bg-background/80 p-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h5 className="text-sm font-semibold">
                      {role === 'train' ? 'Train · 训练基线' : 'Test · 测试基线'}
                    </h5>
                    {typeof result.producerStepId === 'string' && (
                      <span className="rounded bg-muted px-2 py-0.5 text-xs">
                        {producedHere ? '本节点生成' : '复用基线'}
                      </span>
                    )}
                  </div>
                  <Values
                    rows={[
                      ['得分', values.score],
                      ['通过率', values.passRate],
                      ['Case 数量', values.caseCount],
                      ['满分', values.maxScore],
                    ]}
                  />
                  {typeof result.domainId === 'string' && (
                    <Hint content={result.domainId}>
                      <p className="mt-3 truncate text-xs text-muted-foreground">Domain：{result.domainId}</p>
                    </Hint>
                  )}
                  {typeof result.producerStepId === 'string' && !producedHere && (
                    <p className="mt-2 break-all text-xs text-muted-foreground">来源 Step：{result.producerStepId}</p>
                  )}
                  <RunLink runId={result.benchRunId} />
                </div>
              );
            })}
          </div>
        </ResultCard>
      )}
      {typeof output.benchRunId === 'string' && (
        <ResultCard title="Bench 结果">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Values
              rows={[
                ['得分 / 满分', `${text(object(output.metrics).score)} / ${text(object(output.metrics).maxScore)}`],
                ['通过率', object(output.metrics).passRate],
                ['Case 数量', object(output.metrics).caseCount],
              ]}
            />
            <RunLink runId={output.benchRunId} />
          </div>
        </ResultCard>
      )}
    </>
  );
}
