import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain } from '@/services/botWorkshop';
import { botContainerService, type ContainerInstance, type ContainerSnapshot } from '@/services/botWorkshop/botContainerService';
import { RefreshCw } from 'lucide-react';
import React from 'react';
import { toast } from 'sonner';

export interface ContainerInstancesModalProps {
  bot?: BotDomain;
  canRestart?: boolean;
  onClose: () => void;
}

const statusLabel: Record<ContainerInstance['status'], string> = {
  healthy: '活跃',
  restarting: '重启中',
  abnormal: '异常',
  unknown: '状态未知',
};

const statusClass: Record<ContainerInstance['status'], string> = {
  healthy: 'border-success/40 bg-success/10 text-success',
  restarting: 'border-primary/40 bg-primary/10 text-primary',
  abnormal: 'border-destructive/40 bg-destructive/10 text-destructive',
  unknown: 'border-warning/40 bg-warning/10 text-warning',
};

function Detail({ label, value }: { label: string; value?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-xs text-foreground" title={value}>
        {value || '—'}
      </dd>
    </div>
  );
}

export function ContainerInstancesModal({ bot, canRestart = false, onClose }: ContainerInstancesModalProps) {
  const [snapshot, setSnapshot] = React.useState<ContainerSnapshot>();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [restartingId, setRestartingId] = React.useState<string>();
  const load = React.useCallback(async () => {
    if (!bot) return;
    setLoading(true);
    setError('');
    try {
      setSnapshot(await botContainerService.list(bot.id));
    } catch (cause) {
      setSnapshot(undefined);
      setError(cause instanceof Error ? cause.message : '容器实例加载失败');
    } finally {
      setLoading(false);
    }
  }, [bot]);

  React.useEffect(() => {
    if (bot) void load();
  }, [bot, load]);

  const restart = async (instance: ContainerInstance) => {
    if (!bot || instance.status !== 'abnormal' || !canRestart) return;
    setRestartingId(instance.id);
    try {
      await botContainerService.restart(bot.id, instance.id);
      toast.success(`实例 ${instance.id} 已开始重启`);
      await load();
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : '实例重启失败');
    } finally {
      setRestartingId(undefined);
    }
  };

  return (
    <Modal open={Boolean(bot)} onOpenChange={(open) => !open && onClose()}>
      <ModalContent size="xl" className="max-h-[85vh] p-0">
        <ModalHeader className="border-b border-border px-6 py-5">
          <ModalTitle>容器实例 · {bot?.name}</ModalTitle>
          <ModalDescription>查看运行态服务 Bot 的实例健康状态；异常实例仅 Owner 可以重启。</ModalDescription>
        </ModalHeader>
        <div className="min-h-40 px-6 py-5">
          {loading && !snapshot ? <Spin tip="正在加载容器实例…" /> : null}
          {error ? <Empty title="容器实例加载失败" description={error} action={<Button variant="secondary" onClick={() => void load()}>重试</Button>} /> : null}
          {snapshot ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/50 px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span>共 <strong>{snapshot.summary.total}</strong> 个实例</span>
                  <span className="text-success"><strong>{snapshot.summary.healthy}</strong> 活跃</span>
                  {snapshot.summary.abnormal ? <span className="text-destructive"><strong>{snapshot.summary.abnormal}</strong> 异常</span> : null}
                  {snapshot.summary.restarting ? <span className="text-primary"><strong>{snapshot.summary.restarting}</strong> 重启中</span> : null}
                </div>
                <span className="text-xs text-muted-foreground">引擎类型：{bot?.runtime.engine || '—'}</span>
              </div>
              {snapshot.instances.length ? (
                <div className="divide-y divide-border rounded-lg border border-border">
                  {snapshot.instances.map((instance) => (
                    <div key={instance.id} className="flex items-center gap-4 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{instance.providerInstanceId || instance.id}</p>
                        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                          <Detail label="实例 ID" value={instance.id} />
                          <Detail label="节点" value={instance.node} />
                          <Detail label="CPU" value={instance.cpu} />
                          <Detail label="内存" value={instance.memory} />
                        </dl>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${statusClass[instance.status]}`}>
                        {statusLabel[instance.status]}
                      </span>
                      {instance.status === 'abnormal' ? (
                        <TooltipProvider delayDuration={200}>
                          <Tooltip>
                            {/* span 承接 hover:按钮 disabled 后原生 title/内联 trigger 均不再响应。 */}
                            <TooltipTrigger asChild>
                              <span className="inline-flex">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  disabled={!canRestart || restartingId === instance.id}
                                  loading={restartingId === instance.id}
                                  leftIcon={<RefreshCw aria-hidden className="size-3.5" />}
                                  onClick={() => void restart(instance)}
                                >
                                  重启
                                </Button>
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              {canRestart ? '重启该异常实例' : '仅 Bot Owner 可以重启异常实例'}
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <Empty title="暂无运行实例" description="该服务 Bot 当前没有可展示的容器实例。" />
              )}
            </div>
          ) : null}
        </div>
      </ModalContent>
    </Modal>
  );
}
