import { BotSubpageShell } from '@/components/BotWorkshop/BotSubpageShell';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { stageNames, type ManagedRoutine } from '@/services/botScheduledTasks/types';
import { history, useLocation } from '@umijs/max';
import { useState } from 'react';
import { toast } from 'sonner';
import { RoutineActions } from './RoutineActions';
import { RoutineDetail, formatTime } from './RoutineDetail';
import { RoutineFormDialog } from './RoutineFormDialog';
import { useScheduledTasks } from './useScheduledTasks';

const ScheduledTasksPage = () => {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const botId = query.get('bot_id')?.trim() || '';
  const ownerId = query.get('owner_id') || undefined;
  const state = useScheduledTasks(botId, ownerId);
  const { context, lock } = state;
  const [editing, setEditing] = useState<ManagedRoutine | null | undefined>();
  const [revision, setRevision] = useState(0);
  const keyword = query.get('keyword') || '';
  const pageSize = [10, 20, 50].includes(Number(query.get('page_size'))) ? Number(query.get('page_size')) : 10;
  const selectedKey = query.get('task');
  const selected = state.tasks.find((task) => task.key === selectedKey);
  const filtered = state.tasks.filter((task) =>
    `${task.name} ${task.command} ${task.cron} ${task.device_uuid || ''}`
      .toLowerCase()
      .includes(keyword.trim().toLowerCase()),
  );
  const page = Math.min(
    Math.max(1, Number(query.get('page')) || 1),
    Math.max(1, Math.ceil(filtered.length / pageSize)),
  );
  const visible = filtered.slice((page - 1) * pageSize, page * pageSize);
  function navigate(values: Record<string, string | undefined>, detail = false, replace = false) {
    const next = new URLSearchParams(location.search);
    Object.entries(values).forEach(([key, value]) => (value === undefined ? next.delete(key) : next.set(key, value)));
    history[replace ? 'replace' : 'push'](`/bot-workshop/scheduled-tasks${detail ? '/detail' : ''}?${next.toString()}`);
  }
  async function operate(work: () => Promise<unknown>, message: string) {
    try {
      await work();
      toast.success(message);
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
        {state.identity.error && <Empty title="身份加载失败" description={state.identity.error} />}
        {context?.can_manage && (
          <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-5 py-4">
            <p className="m-0 text-sm text-muted-foreground">
              {state.editable
                ? lock?.need_lock === false
                  ? '当前 Bot 无需编辑锁，可管理定时任务'
                  : '已持有 Bot 编辑锁，可管理定时任务'
                : other
                ? `${holder} 正在持有 Bot 编辑锁，当前可查看任务`
                : '获取 Bot 编辑锁后可创建和管理定时任务'}
            </p>
            {state.editable && lock?.locked ? (
              <ConfirmDialog
                title="释放 Bot 编辑锁"
                description="释放后将无法继续管理定时任务。"
                onConfirm={() => state.lockAction('release')}
              >
                <Button size="sm" variant="outline" disabled={state.busy}>
                  释放编辑锁
                </Button>
              </ConfirmDialog>
            ) : !state.editable ? (
              other ? (
                <ConfirmDialog
                  title="接管 Bot 编辑锁"
                  description={`将接管 ${holder} 的编辑锁，请先确认对方已停止编辑。`}
                  confirmVariant="destructive"
                  onConfirm={() => state.lockAction('steal')}
                >
                  <Button size="sm" variant="outline" disabled={state.busy}>
                    接管编辑锁
                  </Button>
                </ConfirmDialog>
              ) : (
                <Button size="sm" disabled={state.busy} onClick={() => void state.lockAction('acquire')}>
                  获取编辑锁
                </Button>
              )
            ) : null}
          </section>
        )}
        {context && !context.supported && <Empty title="当前 Bot 引擎暂不支持定时任务" />}
        {state.error && (
          <Empty
            title="任务加载失败"
            description={state.error}
            action={<Button onClick={() => void state.load()}>重试</Button>}
          />
        )}
        {state.failures.length > 0 && (
          <div role="status" className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            部分运行环境暂不可用，已展示可读取的任务。{state.failures.join('；')}
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
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="m-0 text-lg font-semibold">{selected.name}</h2>
                  {actions(selected)}
                </div>
                <RoutineDetail task={selected} context={context} client={state.client} revision={revision} />
              </>
            ) : (
              <Empty
                title="任务不存在或当前运行环境不可用"
                description="请返回列表刷新，确认任务仍存在于原执行目标。"
                action={<Button onClick={() => navigate({ task: undefined })}>返回任务列表</Button>}
              />
            )
          ) : (
            <section className="rounded-xl border border-border bg-card p-5 sm:p-6">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <Input
                  aria-label="搜索定时任务"
                  className="max-w-sm"
                  placeholder="搜索任务名称、指令、频率或实例"
                  value={keyword}
                  onChange={(event) => navigate({ keyword: event.target.value, page: '1' }, false, true)}
                />
                {context.can_manage && (
                  <Button disabled={!state.editable || state.busy} onClick={() => setEditing(null)}>
                    创建定时任务
                  </Button>
                )}
              </div>
              {visible.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs text-muted-foreground">
                      <tr>
                        {['任务', '执行目标', '频率 / 时区', '调度状态', '最近 / 下次执行', '操作'].map((title) => (
                          <th key={title} className="px-3 py-3 font-medium">
                            {title}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((task) => (
                        <tr
                          key={task.key}
                          className="cursor-pointer border-t border-border hover:bg-muted/40"
                          onClick={() => navigate({ task: task.key }, true)}
                        >
                          <td className="max-w-xs px-3 py-4">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-auto p-0 text-left font-medium text-primary"
                              onClick={(event) => {
                                event.stopPropagation();
                                navigate({ task: task.key }, true);
                              }}
                            >
                              {task.name}
                            </Button>
                            <p className="mb-0 mt-1 line-clamp-2 text-xs text-muted-foreground">{task.command}</p>
                          </td>
                          <td className="max-w-xs break-all px-3 py-4 text-xs">
                            {task.runtime_stage ? stageNames[task.runtime_stage] : '默认环境'}
                            <p className="mt-1 text-muted-foreground">{task.device_uuid || '无独立实例'}</p>
                          </td>
                          <td className="px-3 py-4 text-xs">
                            {getRoutineScheduleLabel(task.cron)}
                            <p className="mt-1 text-muted-foreground">{task.timezone}</p>
                          </td>
                          <td className="px-3 py-4">
                            {task.enabled ? '已启用' : '已停用'}
                            <p className="mt-1 text-xs text-muted-foreground">
                              {context.environments.find(
                                (item) => item.runtime_stage === (task.runtime_stage || 'draft'),
                              )?.available
                                ? '环境可用'
                                : '环境暂不可用'}
                            </p>
                          </td>
                          <td className="whitespace-nowrap px-3 py-4 text-xs">
                            {formatTime(task.lastRunAt)}
                            <p className="mt-1 text-muted-foreground">{formatTime(task.nextRunAt)}</p>
                          </td>
                          <td className="px-3 py-4">{actions(task)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty
                  title={keyword ? '暂无符合条件的任务' : '暂无定时任务'}
                  description="创建任务后，可在这里管理配置并查看历史执行实例。"
                />
              )}
              <Pagination
                current={page}
                pageSize={pageSize}
                total={filtered.length}
                onChange={(next) => navigate({ page: String(next) }, false, true)}
                onPageSizeChange={(size) => navigate({ page_size: String(size), page: '1' }, false, true)}
                pageSizeOptions={[10, 20, 50]}
                className="mt-5 justify-end"
              />
            </section>
          ))}
      </div>
      {editing !== undefined && context && (
        <RoutineFormDialog
          key={editing?.key || 'new'}
          context={context}
          original={editing || undefined}
          editable={state.editable && !state.busy}
          lockRecovery={
            context.can_manage ? (
              <ConfirmDialog
                title={other ? '接管 Bot 编辑锁' : '重新获取编辑锁'}
                description={
                  other ? `将接管 ${holder} 的编辑锁，请先确认对方已停止编辑。` : '获取后可继续保存当前输入。'
                }
                onConfirm={() => state.lockAction(other ? 'steal' : 'acquire')}
              >
                <Button size="sm" variant="outline" disabled={state.busy}>
                  重新获取编辑锁
                </Button>
              </ConfirmDialog>
            ) : undefined
          }
          onClose={() => setEditing(undefined)}
          onLoadModels={state.loadModels}
          onSave={async (form) => {
            await state.save(form, editing || undefined);
            toast.success(editing ? '任务已更新' : '任务已创建');
          }}
        />
      )}
    </BotSubpageShell>
  );
};
export default ScheduledTasksPage;
