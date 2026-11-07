import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain } from '@/services/botWorkshop';
import { botContainerService, type ContainerInstance, type ContainerSnapshot } from '@/services/botWorkshop/botContainerService';
import { Check, Copy, RefreshCw } from 'lucide-react';
import React from 'react';
import { toast } from 'sonner';

export interface ContainerInstancesModalProps {
  bot?: BotDomain;
  /** 容器接口要求显式携带的当前用户 ID。 */
  userId?: string;
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

export function ContainerInstancesModal({ bot, userId, canRestart = false, onClose }: ContainerInstancesModalProps) {
  const [snapshot, setSnapshot] = React.useState<ContainerSnapshot>();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [restartingId, setRestartingId] = React.useState<string>();
  const [copiedId, setCopiedId] = React.useState<string>();
  const load = React.useCallback(async () => {
    if (!bot || !userId || bot.runtime.engine === 'teclaw') return;
    setLoading(true);
    setError('');
    try {
      setSnapshot(await botContainerService.list(bot.id, userId));
    } catch (cause) {
      setSnapshot(undefined);
      setError(cause instanceof Error ? cause.message : '容器实例加载失败');
    } finally {
      setLoading(false);
    }
  }, [bot, userId]);

  React.useEffect(() => {
    if (bot) void load();
  }, [bot, load]);

  const restart = async (instance: ContainerInstance) => {
    if (!bot || !userId || bot.runtime.engine === 'teclaw' || instance.status !== 'abnormal' || !canRestart) return;
    setRestartingId(instance.id);
    try {
      await botContainerService.restart(bot.id, instance.id, userId);
      toast.success(`实例 ${instance.id} 已开始重启`);
      await load();
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : '实例重启失败');
    } finally {
      setRestartingId(undefined);
    }
  };

  const copyProviderId = async (instance: ContainerInstance) => {
    if (!instance.providerInstanceId || !navigator.clipboard) return;
    await navigator.clipboard.writeText(instance.providerInstanceId);
    setCopiedId(instance.id);
    window.setTimeout(() => setCopiedId((current) => (current === instance.id ? undefined : current)), 1500);
  };

  return (
    <Modal open={Boolean(bot)} onOpenChange={(open) => !open && onClose()}>
      <ModalContent size="xl" className="max-h-[85vh] p-0">
        <ModalHeader className="border-b border-divider px-6 py-5">
          <ModalTitle>容器实例 · {bot?.name}</ModalTitle>
          <ModalDescription>查看运行态服务 Bot 的实例健康状态；仅 Owner 可以重启异常实例。</ModalDescription>
        </ModalHeader>
        <div className="min-h-40 px-6 py-5">
          {bot && !userId ? <Spin tip="正在获取当前用户身份…" /> : null}
          {loading && !snapshot ? <Spin tip="正在加载容器实例…" /> : null}
          {error ? (
            <Empty
              title="容器实例加载失败"
              description={error}
              action={
                <Button variant="secondary" onClick={() => void load()}>
                  重试
                </Button>
              }
            />
          ) : null}
          {snapshot ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 border-y border-divider bg-muted/50 px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span>共 <strong>{snapshot.summary.total}</strong> 个实例</span>
                  <span className="text-success"><strong>{snapshot.summary.healthy}</strong> 活跃</span>
                  {snapshot.summary.abnormal ? (
                    <span className="text-destructive">
                      <strong>{snapshot.summary.abnormal}</strong> 异常
                    </span>
                  ) : null}
                  {snapshot.summary.restarting ? (
                    <span className="text-primary">
                      <strong>{snapshot.summary.restarting}</strong> 重启中
                    </span>
                  ) : null}
                </div>
                <span className="text-xs text-muted-foreground">引擎类型：{bot?.runtime.engine || '—'}</span>
              </div>
              {snapshot.instances.length ? (
                <>
                  <div className="overflow-hidden rounded-lg border border-border">
                    <div className="grid grid-cols-[minmax(0,1fr)_112px_96px] gap-4 border-b border-divider bg-muted/30 px-4 py-2 text-xs font-medium text-muted-foreground">
                      <span>实例名称</span>
                      <span className="text-center">心跳状态</span>
                      <span className="text-center">操作</span>
                    </div>
                    {snapshot.instances.map((instance) => (
                      <div
                        key={instance.id}
                        className="grid grid-cols-[minmax(0,1fr)_112px_96px] items-center gap-4 border-b border-divider px-4 py-3 last:border-b-0"
                      >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{instance.providerInstanceId || instance.id}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">ID: {instance.id}</p>
                        {instance.providerInstanceId ? (
                          <div className="mt-1 flex min-w-0 items-center gap-1 font-mono text-xs text-muted-foreground">
                            <span className="truncate">Device ID: {instance.providerInstanceId}</span>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-5 shrink-0"
                              aria-label={`复制 ${instance.providerInstanceId}`}
                              leftIcon={
                                copiedId === instance.id ? <Check className="size-3 text-success" /> : <Copy className="size-3" />
                              }
                              onClick={() => void copyProviderId(instance)}
                            />
                          </div>
                        ) : null}
                      </div>
                      <span
                        className={`justify-self-center rounded-full border px-2 py-0.5 text-xs font-medium ${statusClass[instance.status]}`}
                      >
                        {statusLabel[instance.status]}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        className="justify-self-center"
                        disabled={!canRestart || instance.status !== 'abnormal' || restartingId === instance.id}
                        loading={restartingId === instance.id}
                        leftIcon={<RefreshCw aria-hidden className="size-3.5" />}
                        onClick={() => void restart(instance)}
                      >
                        重启
                      </Button>
                      </div>
                    ))}
                  </div>
                  <p className="border-t border-divider bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
                    重启操作将中断该实例上的活跃对话。
                  </p>
                </>
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
