import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { CreateDraft } from '@/domain/evolution/createTask';
import type { Task, TaskPage, TaskType } from '@/domain/evolution/types';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Choice, ErrorBox, Field, Section } from './shared';

export function EvolutionSettings({
  type,
  draft,
  set,
  domains,
  diagnoses,
  sources,
  sourcePage,
  setSourcePage,
  modelField,
  domainField,
  roundsField,
}: {
  type: TaskType;
  draft: CreateDraft;
  set: <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => void;
  domains: { error: string; reload: () => void };
  diagnoses: { data?: TaskPage; loading: boolean; error: string; reload: () => void };
  sources: Task[];
  sourcePage: number;
  setSourcePage: Dispatch<SetStateAction<number>>;
  modelField: (label: string) => ReactNode;
  domainField: (key: 'benchDomainId' | 'trainDomainId' | 'testDomainId', label: string) => ReactNode;
  roundsField: ReactNode;
}) {
  return (
    <>
      {type === 'bench' && (
        <Section title="评测配置" index={3}>
          <ErrorBox message={domains.error} retry={domains.reload} />
          <div className="grid gap-x-10 gap-y-5 md:grid-cols-2">
            {domainField('benchDomainId', '评测模板')}
            {modelField('评测模型')}
          </div>
        </Section>
      )}
      {type === 'bench_optimize' && (
        <Section title="优化配置" index={3}>
          <ErrorBox message={domains.error} retry={domains.reload} />
          <div className="grid gap-x-10 gap-y-5 md:grid-cols-2">
            {domainField('trainDomainId', '训练评测模板')}
            {domainField('testDomainId', '测试评测模板')}
          </div>
          <div className="mt-5 max-w-[480px] space-y-5">
            {modelField('优化模型')}
            <Field label="优化目标 *">
              <Textarea
                required
                rows={3}
                value={draft.objective}
                onChange={(e) => set('objective', e.target.value)}
                placeholder="请描述本次优化的目标"
              />
            </Field>
            <div className="max-w-[200px]">{roundsField}</div>
          </div>
        </Section>
      )}
      {type === 'optimize' && (
        <Section title="诊断配置" index={3}>
          <ErrorBox message={diagnoses.error} retry={diagnoses.reload} />
          <div className="grid gap-x-10 gap-y-5 md:grid-cols-3">
            <Field label="诊断任务输入 *">
              <Choice
                label="来源诊断"
                value={draft.sourceDiagnosisTaskId}
                disabled={diagnoses.loading}
                options={sources.map((t) => ({ value: t.task_id, label: `${t.task_name} (${t.task_id})` }))}
                onChange={(v) => set('sourceDiagnosisTaskId', v)}
              />
            </Field>
            {modelField('优化模型')}
            {roundsField}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>仅当前 Bot 的诊断及 Plan 已完成任务；第 {sourcePage} 页。</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={sourcePage === 1}
              onClick={() => {
                setSourcePage((p) => p - 1);
                set('sourceDiagnosisTaskId', '');
              }}
            >
              上一页
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={sourcePage >= (diagnoses.data?.totalPages ?? 1)}
              onClick={() => {
                setSourcePage((p) => p + 1);
                set('sourceDiagnosisTaskId', '');
              }}
            >
              下一页
            </Button>
          </div>
        </Section>
      )}
      {type === 'full' && (
        <Section title="进化配置" index={3}>
          <div className="grid gap-x-10 gap-y-5 md:grid-cols-2">
            {modelField('规划模型')}
            {roundsField}
          </div>
          <div className="mt-5">
            <Field label="目标 *">
              <Textarea
                required
                maxLength={2000}
                rows={3}
                value={draft.goal}
                onChange={(e) => set('goal', e.target.value)}
                placeholder="请输入一句话进化目标和成功标准"
              />
            </Field>
          </div>
        </Section>
      )}
    </>
  );
}
