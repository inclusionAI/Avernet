import { BotSubpageShell } from '@/components/BotWorkshop/BotSubpageShell';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { addressFromKey, isStage, type OperationResult, type ManagedRoutine } from '@/services/botScheduledTasks/types';
import { history, useLocation } from '@umijs/max';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { RoutineActions } from './RoutineActions';
import { RoutineList } from './RoutineList';
import { notifyRoutineResult } from './notifyRoutineResult';
import { describeFailure } from '@/services/botScheduledTasks/results';
import { RoutineDetail } from './RoutineDetail';
import { RoutineFormDialog } from './RoutineFormDialog';
import { useScheduledTasks } from './useScheduledTasks';

const ScheduledTasksPage = () => {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const botId = query.get('bot_id')?.trim() || '';
  const ownerId = query.get('entity_id') || query.get('owner_id') || undefined;
  const pageSize = [10, 20, 50].includes(Number(query.get('page_size'))) ? Number(query.get('page_size')) : 20;
  const page = Math.max(1, Math.floor(Number(query.get('page'))) || 1);
  const stage = isStage(query.get('runtime_stage'))
    ? (query.get('runtime_stage') as 'draft' | 'verify' | 'online')
    : undefined;
  const state = useScheduledTasks(botId, ownerId, { runtime_stage: stage }, page, pageSize);
  const { context, lock } = state;
  useEffect(() => {
    if (context?.bot_type !== 'personal') return;
    const next = new URLSearchParams(location.search);
    if (!next.has('runtime_stage')) return;
    next.delete('runtime_stage');
    next.set('page', '1');
    history.replace(`${location.pathname}?${next.toString()}`);
  }, [context?.bot_type, location.pathname, location.search]);
  const [editing, setEditing] = useState<ManagedRoutine | null | undefined>();
  const [revision, setRevision] = useState(0);
  const keyword = query.get('keyword') || '';
  const selectedKey = query.get('task');
  const selected =
    state.tasks.find((task) => task.key === selectedKey) ||
    addressFromKey(selectedKey, { ...state.client.scope, entityId: ownerId || context?.owner_id || undefined });
  function navigate(values: Record<string, string | undefined>, detail = false, replace = false) {
    const next = new URLSearchParams(location.search);
    Object.entries(values).forEach(([key, value]) => (value === undefined ? next.delete(key) : next.set(key, value)));
    history[replace ? 'replace' : 'push'](`/bot-workshop/scheduled-tasks${detail ? '/detail' : ''}?${next.toString()}`);
  }
  async function operate(work: () => Promise<OperationResult>) {
    try {
      const result = await work();
      notifyRoutineResult(result);
      setRevision((value) => value + 1);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败');
    }
  }
  function actions(task: ManagedRoutine) {
    return (
      <RoutineActions
        task={task}
        state={state}
        onEdit={setEditing}
        operate={operate}
        onDeleted={(removed) => {
          if (selectedKey === removed.key) navigate({ task: undefined });
        }}
      />
    );
  }
  if (!botId)
    return (
      <Empty
        title="缺少 Bot 标识"
        action={<Button onClick={() => history.push('/bot-workshop')}>返回 Bot 管理</Button>}
      />
    );
  const holder = lock?.holder_name || lock?.holder_user_id || '其他协作者';
  const other = lock?.locked && lock.holder_user_id !== state.identity.userId;
  return (
    <BotSubpageShell
      botName={context?.bot_name || botId}
      title={selectedKey ? '定时任务详情' : '定时任务'}
      subtitle={context?.bot_name || query.get('bot_name') || botId}
      backAriaLabel={selectedKey ? '返回定时任务列表' : '返回 Bot 管理'}
      onBack={() => (selectedKey ? navigate({ task: undefined }) : history.push('/bot-workshop'))}
      actions={
        <Button size="sm" variant="outline" disabled={state.loading} onClick={() => void state.load()}>
          刷新
        </Button>
      }
    >
      <div className="space-y-5">
        {context?.warning && (
          <p role="status" className="text-sm text-muted-foreground">
            {context.warning}
          </p>
        )}
        {state.identity.error && <Empty title="身份加载失败" description={state.identity.error} />}
        {context?.can_manage && (
          <section role="status" className="rounded-xl border border-border bg-card px-5 py-4">
            <p className="m-0 text-sm text-muted-foreground">
              {state.editable
                ? lock?.need_lock === false
                  ? '当前 Bot 无需编辑锁，可管理定时任务'
                  : '已持有 Bot 编辑锁，可管理定时任务'
                : !lock
                ? state.lockError
                  ? `Bot 编辑锁查询失败，当前仅可查看：${state.lockError}`
                  : '正在确认 Bot 编辑锁状态，当前仅可查看'
                : other
                ? `${holder} 正在持有 Bot 编辑锁，当前仅可查看；如需操作，请到 Bot 列表管理编辑锁`
                : '当前未持有 Bot 编辑锁，仅可查看；请到 Bot 列表获取编辑锁后再操作'}
            </p>
          </section>
        )}
        {context && !context.supported && <Empty title="当前 Bot 引擎暂不支持定时任务" />}
        {state.error && (
          <Empty
            title="任务加载失败"
            description={`${state.error}${state.tasks.length ? '；刷新未成功，下面显示上次查询结果' : ''}`}
            action={<Button onClick={() => void state.load()}>重试</Button>}
          />
        )}
        {state.failures.length > 0 && (
          <div role="status" className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            {state.tasks.length ? '部分实例查询失败，已保留取得的任务。' : '未获取到任务，存在查询失败的实例。'}
            {state.failures.map(describeFailure).join('；')}
          </div>
        )}
        {state.loading && (
          <p className="py-8 text-center text-sm text-muted-foreground" role="status">
            加载中…
          </p>
        )}
        {context?.supported &&
          !state.loading &&
          (selectedKey ? (
            selected ? (
              <RoutineDetail
                task={selected}
                context={context}
                client={state.client}
                revision={revision}
                actions={actions}
              />
            ) : (
              <Empty
                title="任务不存在或当前运行环境不可用"
                description="请返回列表刷新，确认任务仍存在于原执行目标。"
                action={<Button onClick={() => navigate({ task: undefined })}>返回任务列表</Button>}
              />
            )
          ) : (
            <RoutineList
              tasks={state.tasks}
              total={state.total}
              page={page}
              pageSize={pageSize}
              stage={stage}
              showStageFilter={context.bot_type === 'service'}
              keyword={keyword}
              canManage={context.can_manage}
              editable={state.editable}
              busy={state.busy}
              incomplete={state.failures.length > 0}
              actions={actions}
              onKeyword={(value) => navigate({ keyword: value }, false, true)}
              onStage={(value) =>
                navigate(
                  { runtime_stage: value === 'all' ? undefined : value, page: '1', keyword: undefined },
                  false,
                  true,
                )
              }
              onPage={(value) => navigate({ page: String(value) }, false, true)}
              onPageSize={(value) => navigate({ page_size: String(value), page: '1' }, false, true)}
              onCreate={() => setEditing(null)}
              onSelect={(task) => navigate({ task: task.key }, true)}
            />
          ))}
      </div>
      {editing !== undefined && context && (
        <RoutineFormDialog
          key={editing?.key || 'new'}
          context={context}
          original={editing || undefined}
          editable={state.editable && !state.busy}
          onClose={() => setEditing(undefined)}
          onLoadModels={state.loadModels}
          onSave={async (form) => {
            const result = await state.save(form, editing || undefined);
            if (result.level !== 'success') throw new Error(result.message);
            notifyRoutineResult(result);
            if (!editing && result.routine)
              navigate(
                { task: result.routine.key, runtime_stage: result.routine.runtime_stage || undefined, page: '1' },
                true,
              );
          }}
        />
      )}
    </BotSubpageShell>
  );
};
export default ScheduledTasksPage;
