import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { ServicePublication, ServicePublicationAction } from '@/domain/botEditor';
import { useServicePublications } from '@/hooks/useServicePublications';
import type { BotDomain } from '@/services/botWorkshop';
import { ArrowRight, ExternalLink, RefreshCw, RotateCw } from 'lucide-react';

const statusLabel: Record<ServicePublication['status'], string> = {
  draft: '草稿',
  deploying: '部署中',
  prestable: '预发',
  staging: '预发',
  running: '运行中',
  offline: '已下线',
};

export function ServicePublicationDrawer({
  bot,
  onClose,
  onChanged,
}: {
  bot?: BotDomain;
  onClose: () => void;
  onChanged?: () => Promise<void>;
}) {
  const publications = useServicePublications(bot?.id);
  const run = (action: ServicePublicationAction, item: ServicePublication) => {
    if (action === 'publish_staging') return publications.advance('prestable');
    if (action === 'publish_online') return publications.advance('online');
    if (action === 'restart_publish') return publications.restart(item.status === 'running' ? 'online' : 'prestable');
    if (action === 'cancel_staging') return publications.cancel();
    if (action === 'offline') return publications.offline();
    if (action === 'retry') return publications.retry();
    if (action === 'upgrade')
      return publications.upgrade(item.publicationId).then(async () => {
        await onChanged?.();
      });
    if (action === 'delete')
      return publications.deleteDraft().then(async () => {
        onClose();
        await onChanged?.();
      });
    return Promise.resolve();
  };
  const label: Record<ServicePublicationAction, string> = {
    publish_staging: '发布预发',
    publish_online: '发布上线',
    restart_publish: '重启发布',
    cancel_staging: '取消预发',
    offline: '下线',
    retry: '重试',
    upgrade: '升级',
    delete: '删除草稿',
  };
  return (
    <Drawer
      open={Boolean(bot)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent size="lg">
        <DrawerHeader>
          <DrawerTitle>{bot?.name} · 服务发布</DrawerTitle>
          <DrawerDescription>发布、审批与阶段推进均使用服务 Bot Lifecycle OpenAPI。</DrawerDescription>
        </DrawerHeader>
        {publications.loading ? (
          <Spin tip="加载发布状态…" />
        ) : publications.items.length ? (
          <div className="space-y-4">
            {publications.items.map((item) => (
              <Card key={item.cardId}>
                <CardHeader>
                  <div>
                    <CardTitle>版本 V{item.version}</CardTitle>
                    <p className="mt-1 text-xs text-[var(--color-muted)]">{item.internalStatus}</p>
                  </div>
                  <Badge
                    tone={item.status === 'running' ? 'success' : item.status === 'deploying' ? 'warning' : 'neutral'}
                  >
                    {statusLabel[item.status]}
                  </Badge>
                </CardHeader>
                <CardContent className="space-y-3">
                  {item.deployment ? (
                    <div className="rounded-lg border border-border p-3 text-xs">
                      <p className="m-0 font-medium">
                        部署：{item.deployment.target} · {item.deployment.status === 'failed' ? '失败' : '进行中'}
                      </p>
                      {item.deployment.errorMessage ? (
                        <p className="m-0 mt-1 text-destructive">{item.deployment.errorMessage}</p>
                      ) : null}
                    </div>
                  ) : null}
                  {item.approval?.required ? (
                    <div className="flex items-center justify-between rounded-lg border border-border p-3 text-xs">
                      <span>审批状态：{item.approval.status || '等待提交'}</span>
                      {item.approval.approvalUrl ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          rightIcon={<ExternalLink className="size-3" />}
                          onClick={() => window.open(item.approval?.approvalUrl, '_blank', 'noopener,noreferrer')}
                        >
                          查看审批
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    {item.availableActions.map((action) => (
                      <ConfirmDialog
                        key={action}
                        title={label[action]}
                        description={
                          action === 'upgrade'
                            ? `将基于当前运行版本 V${item.version} 创建新的升级草稿，确认继续？`
                            : `确认对 V${item.version} 执行“${label[action]}”？`
                        }
                        confirmText={action === 'upgrade' ? '确认升级' : '确定'}
                        confirmVariant={
                          action === 'offline' || action === 'cancel_staging' || action === 'delete'
                            ? 'destructive'
                            : 'primary'
                        }
                        onConfirm={() => run(action, item)}
                      >
                        <Button
                          variant={
                            action === 'publish_staging' || action === 'publish_online' ? 'primary' : 'secondary'
                          }
                          size="sm"
                          leftIcon={
                            action === 'retry' || action === 'restart_publish' || action === 'upgrade' ? (
                              <RotateCw className="size-3" />
                            ) : (
                              <ArrowRight className="size-3" />
                            )
                          }
                        >
                          {label[action]}
                        </Button>
                      </ConfirmDialog>
                    ))}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <Empty title="暂无发布版本" description="服务 Bot 尚未返回可管理的发布版本。" />
        )}
        <div className="mt-4 flex justify-end">
          <Button
            variant="secondary"
            leftIcon={<RefreshCw className="size-4" />}
            onClick={() => void publications.reload()}
          >
            刷新状态
          </Button>
        </div>
      </DrawerContent>
    </Drawer>
  );
}
