import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { evolutionApi } from '@/services/backendApi/clawevolve/controller';
import { history, useLocation } from '@umijs/max';
import { useCallback, useState } from 'react';
import { Choice, ErrorBox, EvolutionHeader, Field, Section, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

/** Auxiliary version actions: deliberately not a sixth primary task type. */
export default function PackForm({ restore }: { restore: boolean }) {
  const identity = useEvolutionContext();
  const [page, setPage] = useState(1);
  const bots = useResource(
    useCallback(() => evolutionApi.bots(page), [identity.key, page]),
    identity.ready,
  );
  const packs = useResource(
    useCallback(() => evolutionApi.packs(), [identity.key]),
    identity.ready && restore,
  );
  const [target, setTarget] = useState(''),
    [name, setName] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [packId, setPackId] = useState(new URLSearchParams(useLocation().search).get('packId') || '');
  const bot = bots.data?.items.find((b) => JSON.stringify([b.ownerId, b.botId, b.env]) === target);
  const compatible = (packs.data?.items ?? []).filter(
    (p) => p.userId === bot?.ownerId && p.botId === bot?.botId && p.status === 'available',
  );
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !bot) return;
    setError('');
    setBusy(true);
    try {
      if (restore && !compatible.some((p) => p.packId === packId))
        throw new Error('请选择与目标 Bot 匹配的已登记 Pack');
      const task = await evolutionApi.create(restore ? 'pack-restores' : 'packs', {
        taskName: name,
        userId: bot.ownerId,
        botId: bot.botId,
        botEnv: bot.env,
        ...(restore ? { packId } : {}),
      });
      if (!task.task_id) throw new Error('未返回任务ID，请先刷新列表确认结果');
      history.push(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="h-full overflow-y-auto bg-background p-6">
      <div className="space-y-5">
        <EvolutionHeader tab="versions" />
        <form onSubmit={(e) => void submit(e)}>
          <Section title={restore ? '应用 Pack' : '创建 Pack'}>
            <p className="mb-4 text-sm text-muted-foreground">
              {restore
                ? '将已登记的 Pack 应用到原目标 Bot；按原服务执行与校验。'
                : '为当前 Bot 创建主动快照，不改变已有进化业务流程。'}
            </p>
            <ErrorBox message={identity.error || error || bots.error || packs.error} />
            <div className="max-w-[560px] space-y-4">
              <Field label="任务名称">
                <Input required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="目标 Bot">
                <Choice
                  label="目标 Bot"
                  value={target}
                  onChange={setTarget}
                  options={(bots.data?.items ?? []).map((b) => ({
                    value: JSON.stringify([b.ownerId, b.botId, b.env]),
                    label: `${b.botName} · ${b.ownerId}/${b.botId}/${b.env}`,
                  }))}
                />
              </Field>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={page === 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  上一页
                </Button>
                <span className="text-xs">第{page}页</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={page * 50 >= (bots.data?.total ?? 0)}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页
                </Button>
              </div>
              {restore && (
                <Field label="已登记 Pack">
                  <Choice
                    label="Pack"
                    value={packId}
                    onChange={setPackId}
                    options={compatible.map((p) => ({
                      value: String(p.packId),
                      label: `${p.packId} · ${p.sourceKind}`,
                    }))}
                  />
                </Field>
              )}
              <p className="text-xs text-muted-foreground">
                提交后会在目标 Bot 执行{restore ? '恢复' : '快照'}操作，请确认目标和来源。
              </p>
              <Button
                type="submit"
                loading={busy}
                disabled={
                  !identity.ready || !bot || !name.trim() || (restore && !compatible.some((p) => p.packId === packId))
                }
              >
                确认{restore ? '应用' : '创建'}
              </Button>
              <Button type="button" variant="ghost" onClick={() => history.push('/evolution-factory?tab=versions')}>
                返回
              </Button>
            </div>
          </Section>
        </form>
      </div>
    </main>
  );
}
