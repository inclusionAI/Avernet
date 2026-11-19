import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { Switch } from '@/components/ui/Switch';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { getBotEngineLabel } from '@/domain/botEngine';
import { useBotBrowseConfig, type BotBrowseRow } from '@/hooks/useBotBrowseConfig';
import { RefreshCw } from 'lucide-react';

function BotRow({ row, onToggle }: { row: BotBrowseRow; onToggle: (on: boolean) => void }) {
  const engineLabel = getBotEngineLabel(row.engine);
  return (
    <div className="flex min-h-[50px] items-center justify-between gap-3 rounded-lg border border-border bg-card px-2.5 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <BotDisplayAvatar botId={row.botId} name={row.botName} avatarUrl={row.avatarUrl} size={32} />
        <div className="flex min-w-0 items-center gap-1">
          <span className="truncate text-[13px] font-medium leading-[18px] text-foreground" title={row.botName}>
            {row.botName}
          </span>
          {engineLabel ? (
            <Badge tone="outline" className="h-[18px] shrink-0 rounded px-2 py-0 text-xs font-normal leading-4 text-muted-foreground">
              {engineLabel}
            </Badge>
          ) : null}
        </div>
      </div>
      <Switch
        aria-label={`${row.botName} 周期性逛社区`}
        checked={row.enabled}
        disabled={row.saving}
        loading={row.saving}
        onCheckedChange={onToggle}
      />
    </div>
  );
}

/** 头像、名称、引擎与订阅开关；备注不在此弹窗编辑，已有备注仍由服务保留。 */
export function BotBrowseConfigPanel() {
  const { rows, loading, error, gated, reload, toggle } = useBotBrowseConfig();

  if (loading) {
    return (
      <div role="status" className="space-y-2" aria-busy="true" aria-label="加载社区配置">
        {[0, 1, 2].map((key) => (
          <Skeleton.Block key={key} className="h-[50px] w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (gated) {
    return (
      <Empty
        title="需在「我的身份」下配置"
        description="周期性逛社区按员工拥有的 Bot 配置，请在身份切换器切回用户身份后重试。"
      />
    );
  }

  return (
    <div className="space-y-2">
      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-3">
          <span className="text-sm text-destructive">{error}</span>
          <Button variant="ghost" size="icon" aria-label="重试加载" onClick={reload}>
            <RefreshCw className="size-4" aria-hidden />
          </Button>
        </div>
      ) : null}
      {rows.length === 0 && !error ? (
        <Empty title="暂无 Bot" description="当前用户没有可配置的 Bot；创建 Bot 后可在此开启周期性逛社区。" />
      ) : (
        rows.map((row) => (
          <BotRow
            key={row.botId}
            row={row}
            onToggle={(on) =>
              void toggle(row.botId, on)
                .then(() => notifySuccess(on ? '已开启周期性逛社区' : '已关闭周期性逛社区'))
                .catch(() => notifyError(on ? '开启失败，请稍后重试' : '关闭失败，请稍后重试'))
            }
          />
        ))
      )}
    </div>
  );
}
