import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Spin } from '@/components/ui/Spin';
import { Textarea } from '@/components/ui/Textarea';
import type { CreateDraft } from '@/domain/evolution/createTask';
import {
  buildCreateRequest,
  initialDraft,
  localDate,
  matchesDiagnosisTarget,
  validateDiagnosisSource,
} from '@/domain/evolution/createTask';
import { completedDiagnosis, isTaskType, labelOf, taskTypes } from '@/domain/evolution/types';
import { evolutionApi } from '@/services/backendApi/clawevolve/controller';
import { history, useLocation } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import PackForm from './PackForm';
import { Choice, ErrorBox, Field, Section, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

export default function CreateTask() {
  const context = useEvolutionContext();
  const type = new URLSearchParams(useLocation().search).get('type');
  return type === 'pack' || type === 'pack_restore' ? (
    <PackForm key={`${context.key}:${type}`} restore={type === 'pack_restore'} />
  ) : (
    <CreateTaskContent key={context.key} />
  );
}
function CreateTaskContent() {
  const rawType = new URLSearchParams(useLocation().search).get('type') ?? '';
  const type = isTaskType(rawType) ? rawType : null;
  const identity = useEvolutionContext();
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
  const botItems = bots.data?.items ?? [];
  const ownDomains = domains.data?.filter((d) => d.status !== 'archived') ?? [];
  const sources =
    diagnoses.data?.tasks.filter((task) => completedDiagnosis(task) && matchesDiagnosisTarget(task, draft)) ?? [];
  const submit = async (event: React.FormEvent) => {
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
          <button type="button" onClick={() => history.push('/evolution-factory')}>
            返回任务列表
          </button>
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
        <Section title="任务信息" index={1}>
          <div className="max-w-[480px] space-y-5">
            <Field label="任务名称 *">
              <Input
                required
                maxLength={128}
                placeholder="请输入任务名称"
                value={draft.taskName}
                onChange={(e) => set('taskName', e.target.value)}
              />
            </Field>
            <Field label="备注（可选）">
              <Input
                maxLength={1000}
                placeholder="补充对任务的内容、范围或任务要求"
                value={draft.remark}
                onChange={(e) => set('remark', e.target.value)}
              />
            </Field>
          </div>
        </Section>
        <Section title="进化对象" index={2}>
          <ErrorBox message={bots.error} retry={bots.reload} />
          <div
            className="max-h-[380px] space-y-2 overflow-y-auto rounded-xl border bg-muted/20 p-2"
            role="radiogroup"
            aria-label="请选择 Bot"
          >
            {botItems.map((bot) => {
              const selected = draft.botId === bot.botId && draft.ownerId === bot.ownerId && draft.botEnv === bot.env;
              return (
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  key={JSON.stringify([bot.ownerId, bot.botId, bot.env])}
                  className={`flex w-full items-center justify-between gap-4 rounded-xl border bg-card p-4 text-left ${
                    selected ? 'border-primary' : 'border-border'
                  }`}
                  onClick={() =>
                    setDraft((d) => ({
                      ...d,
                      botId: bot.botId,
                      ownerId: bot.ownerId,
                      botEnv: bot.env,
                      unambiguousEnvironment: bot.unambiguousEnvironment,
                      sourceDiagnosisTaskId: '',
                      benchDomainId: '',
                      trainDomainId: '',
                      testDomainId: '',
                    }))
                  }
                >
                  <span className="min-w-0 space-y-2">
                    <strong className="block truncate">{bot.botName || bot.botId}</strong>
                    <span className="block break-all font-mono text-xs text-muted-foreground">
                      {bot.ownerId} / {bot.botId} / {bot.env}
                    </span>
                    <span className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>
                        {bot.deviceProvider === 'baas'
                          ? 'BaaS · 推荐'
                          : bot.deviceProvider === 'arca'
                          ? 'ARCA · 不推荐'
                          : '平台未知'}
                      </span>
                      <span>{bot.activeEngine}</span>
                      <span>
                        {bot.hasServiceBot ? '已有服务 Bot' : bot.botType === 'service' ? '服务型 Bot' : '普通 Bot'}
                      </span>
                    </span>
                  </span>
                  <span
                    className={`h-4 w-4 shrink-0 rounded-full border ${
                      selected ? 'border-primary bg-primary' : 'border-border'
                    }`}
                  />
                </button>
              );
            })}
            {!bots.loading && !botItems.length && (
              <p className="p-4 text-sm text-muted-foreground">当前空间暂无支持 OpenClaw 的 Bot</p>
            )}
          </div>
          {(botPage > 1 || botItems.length === 50) && (
            <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
              <span>第 {botPage} 页</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={botPage === 1}
                onClick={() => setBotPage((p) => p - 1)}
              >
                上一页
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={botItems.length < 50}
                onClick={() => setBotPage((p) => p + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </Section>
        {type === 'diagnose' && (
          <>
            <Section title="诊断范围" index={3}>
              <fieldset className="mb-5">
                <legend className="mb-2 text-sm font-medium">范围模式</legend>
                <div className="flex flex-wrap gap-5 text-sm">
                  {[
                    ['range', '按时间范围'],
                    ['explicit', '按精确 Session'],
                  ].map(([value, label]) => (
                    <label key={value} className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="session-range"
                        checked={draft.explicitSessions === (value === 'explicit')}
                        onChange={() => set('explicitSessions', value === 'explicit')}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>
              {draft.explicitSessions ? (
                <div className="max-w-[480px]">
                  <Field label="Session 标识 *" help="每行一个，1到20个，重复标识会去重。">
                    <Textarea
                      required
                      rows={6}
                      placeholder={'session_id_001\nsession_id_002'}
                      value={draft.sessionText}
                      onChange={(e) => set('sessionText', e.target.value)}
                    />
                  </Field>
                </div>
              ) : (
                <div className="grid gap-x-10 gap-y-5 md:grid-cols-2">
                  <Field label="会话时间范围">
                    <Choice
                      label="会话时间范围"
                      value={rangeDays}
                      options={['7', '14', '30', '90'].map((value) => ({ value, label: `最近 ${value} 天` }))}
                      onChange={(value) => {
                        setRangeDays(value);
                        const now = new Date();
                        const start = new Date(now);
                        start.setDate(start.getDate() - Number(value));
                        setDraft((d) => ({ ...d, startDate: localDate(start), endDate: localDate(now) }));
                      }}
                    />
                  </Field>
                  <Field label="最多诊断 Session 数量">
                    <Input
                      type="number"
                      required
                      min={1}
                      max={1000}
                      value={draft.maxSessions}
                      onChange={(e) => set('maxSessions', Number(e.target.value))}
                    />
                  </Field>
                </div>
              )}
            </Section>
            <Section title="诊断设置" index={4}>
              <div className={`grid gap-x-10 gap-y-5 ${draft.explicitSessions ? 'max-w-[480px]' : 'md:grid-cols-3'}`}>
                {modelField('模型')}
                {!draft.explicitSessions && (
                  <>
                    <Field label="Bad Case 数量">
                      <Input
                        type="number"
                        required
                        min={0}
                        max={100}
                        value={draft.badCases}
                        onChange={(e) => set('badCases', Number(e.target.value))}
                      />
                    </Field>
                    <Field label="Good Case 数量">
                      <Input
                        type="number"
                        required
                        min={0}
                        max={100}
                        value={draft.goodCases}
                        onChange={(e) => set('goodCases', Number(e.target.value))}
                      />
                    </Field>
                  </>
                )}
              </div>
              <div className="mt-5 max-w-[480px]">
                <Field label="关注问题 *">
                  <Textarea
                    required
                    maxLength={1000}
                    rows={3}
                    value={draft.focus}
                    onChange={(e) => set('focus', e.target.value)}
                  />
                </Field>
              </div>
            </Section>
          </>
        )}
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
        <p className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-muted-foreground">
          运行前维护：沿用现有行为，清理历史进化会话并重启目标 Bot 的 OpenClaw Gateway，可能影响正在运行的会话和任务。
        </p>
        <footer className="sticky bottom-0 flex justify-center gap-3 border-t border-border bg-background py-4">
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
