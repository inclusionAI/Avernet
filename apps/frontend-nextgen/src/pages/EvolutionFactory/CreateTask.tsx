import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Spin } from '@/components/ui/Spin';
import type { CreateDraft } from '@/domain/evolution/createTask';
import {
  buildCreateRequest,
  initialDraft,
  matchesDiagnosisTarget,
  validateDiagnosisSource,
} from '@/domain/evolution/createTask';
import { completedDiagnosis, isTaskType, labelOf, taskTypes } from '@/domain/evolution/types';
import { history, useLocation } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CreateTaskTarget } from './CreateTaskTarget';
import { DiagnosisSettings } from './DiagnosisSettings';
import { EvolutionSettings } from './EvolutionSettings';
import PackForm from './PackForm';
import { Choice, ErrorBox, Field, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

function CreateTaskContent() {
  const rawType = new URLSearchParams(useLocation().search).get('type') ?? '';
  const type = isTaskType(rawType) ? rawType : null;
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const [draft, setDraft] = useState<CreateDraft>(() => initialDraft(type ?? 'diagnose'));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [sourcePage, setSourcePage] = useState(1);
  const [rangeDays, setRangeDays] = useState('7');
  const [botPage, setBotPage] = useState(1);
  const set = <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  useEffect(() => {
    if (type) {
      setDraft(initialDraft(type));
      setRangeDays('7');
      setError('');
      setSourcePage(1);
    }
  }, [type]);
  useEffect(() => {
    setDraft((d) => ({
      ...d,
      botId: '',
      ownerId: '',
      sourceDiagnosisTaskId: '',
      benchDomainId: '',
      trainDomainId: '',
      testDomainId: '',
    }));
    setSourcePage(1);
    setBotPage(1);
  }, [identity.key]);
  const models = useResource(
    useCallback(() => evolutionApi.models(), [identity.key]),
    identity.ready,
  );
  const bots = useResource(
    useCallback(() => evolutionApi.bots(botPage), [identity.key, botPage]),
    identity.ready,
  );
  const domains = useResource(
    useCallback(() => evolutionApi.domains(), [identity.key]),
    identity.ready && ['bench', 'bench_optimize'].includes(rawType),
  );
  const diagnoses = useResource(
    useCallback(() => evolutionApi.tasks({ page: sourcePage, pageSize: 50 }), [sourcePage, identity.key]),
    identity.ready && type === 'optimize',
  );
  useEffect(() => {
    if (models.data) setDraft((d) => ({ ...d, model: d.model || models.data!.defaultModel }));
  }, [models.data, type]);
  if (!type)
    return (
      <Empty title="任务类型不存在" action={<Button onClick={() => history.push('/evolution-factory')}>返回</Button>} />
    );
  if (identity.loading) return <Spin tip="正在获取用户身份…" />;
  if (identity.error) return <ErrorBox message={identity.error} />;
  const ownDomains = domains.data?.filter((d) => d.status !== 'archived') ?? [];
  const sources =
    diagnoses.data?.tasks.filter((task) => completedDiagnosis(task) && matchesDiagnosisTarget(task, draft)) ?? [];
  const submit = async (event: React.FormEvent) => {
    Tracert.click(Points.evolution.createSubmit);
    event.preventDefault();
    if (submitting.current) return;
    setError('');
    try {
      const request = buildCreateRequest(draft);
      submitting.current = true;
      setBusy(true);
      if (type === 'optimize') {
        const source = await evolutionApi.task(draft.sourceDiagnosisTaskId);
        validateDiagnosisSource(source, draft);
      }
      const task = await evolutionApi.create(request.endpoint, request.data);
      if (!task.task_id) throw new Error('服务未返回任务 ID，请先刷新任务列表确认创建结果，勿重复提交');
      history.replace(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}?created=1`, {
        createdTask: task,
        spaceKey: identity.key,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败');
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };
  const modelField = (label: string) => (
    <Field label={`${label} *`}>
      <Choice
        label="模型"
        value={draft.model}
        disabled={models.loading}
        options={[
          ...new Set(
            [models.data?.defaultModel, ...(models.data?.models ?? [])].filter((v): v is string => Boolean(v)),
          ),
        ].map((value) => ({ value, label: value }))}
        onChange={(v) => set('model', v)}
      />
    </Field>
  );
  const roundsField = (
    <Field label="最大优化轮数 *">
      <Input
        type="number"
        required
        min={1}
        max={100}
        value={draft.maxRounds}
        onChange={(e) => set('maxRounds', Number(e.target.value))}
      />
    </Field>
  );
  const domainField = (key: 'benchDomainId' | 'trainDomainId' | 'testDomainId', label: string) => (
    <Field label={`${label} *`} help="执行评测模板中已发布的 Bench。">
      <Choice
        label={label}
        value={
          draft[key]
            ? JSON.stringify([
                draft[
                  key === 'benchDomainId'
                    ? 'benchDomainOwnerUserId'
                    : key === 'trainDomainId'
                    ? 'trainBenchOwnerUserId'
                    : 'testBenchOwnerUserId'
                ] || draft.ownerId,
                draft[key],
              ])
            : ''
        }
        disabled={domains.loading}
        onChange={(v) => {
          const [owner, domain] = JSON.parse(v) as [string, string];
          const ownerKey =
            key === 'benchDomainId'
              ? 'benchDomainOwnerUserId'
              : key === 'trainDomainId'
              ? 'trainBenchOwnerUserId'
              : 'testBenchOwnerUserId';
          setDraft((d) => ({ ...d, [key]: domain, [ownerKey]: owner }));
        }}
        options={ownDomains.map((d) => ({
          value: JSON.stringify([d.ownerUserId, d.domainId]),
          label: `${d.name} (${d.ownerUserId}/${d.domainId})`,
        }))}
      />
    </Field>
  );
  return (
    <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
      <form onSubmit={submit} className="w-full min-w-0 space-y-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Button
            variant="ghost"
            style={{ height: 'auto', whiteSpace: 'normal' }}
            type="button"
            onClick={() => history.push('/evolution-factory')}
          >
            返回任务列表
          </Button>
          <span>/</span>
          <span>创建{labelOf(type)}</span>
        </div>
        <header className="flex items-start gap-3 pb-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="返回任务列表"
            onClick={() => history.push('/evolution-factory')}
          >
            <ArrowLeft size={18} />
          </Button>
          <div>
            <h1 className="text-[22px] font-semibold">创建{labelOf(type)}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {taskTypes.find((item) => item.type === type)?.description}
            </p>
          </div>
        </header>
        <ErrorBox message={error} />
        <ErrorBox message={models.error} retry={models.reload} />
        {!models.loading && !models.error && !models.data?.defaultModel && (
          <ErrorBox message="服务未配置默认模型，请联系管理员检查模型配置。" />
        )}
        <CreateTaskTarget
          draft={draft}
          set={set}
          setDraft={setDraft}
          bots={bots}
          botPage={botPage}
          setBotPage={setBotPage}
        />
        {type === 'diagnose' && (
          <DiagnosisSettings
            draft={draft}
            set={set}
            setDraft={setDraft}
            rangeDays={rangeDays}
            setRangeDays={setRangeDays}
            modelField={modelField}
          />
        )}
        <EvolutionSettings
          type={type}
          draft={draft}
          set={set}
          domains={domains}
          diagnoses={diagnoses}
          sources={sources}
          sourcePage={sourcePage}
          setSourcePage={setSourcePage}
          modelField={modelField}
          domainField={domainField}
          roundsField={roundsField}
        />
        <p className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-muted-foreground">
          运行前维护：沿用现有行为，清理历史进化会话并重启目标 Bot 的 OpenClaw Gateway，可能影响正在运行的会话和任务。
        </p>
        <footer className="sticky bottom-0 flex justify-center gap-3 border-t border-divider bg-background py-4">
          <Button type="button" variant="outline" disabled={busy} onClick={() => history.push('/evolution-factory')}>
            取消
          </Button>
          <Button type="submit" loading={busy} disabled={!identity.ready || !models.data?.defaultModel || !draft.botId}>
            创建任务
          </Button>
        </footer>
      </form>
    </main>
  );
}

export default function CreateTask() {
  const context = useEvolutionContext();
  const type = new URLSearchParams(useLocation().search).get('type');
  return type === 'pack' || type === 'pack_restore' ? (
    <PackForm key={`${context.key}:${type}`} restore={type === 'pack_restore'} />
  ) : (
    <CreateTaskContent key={context.key} />
  );
}
