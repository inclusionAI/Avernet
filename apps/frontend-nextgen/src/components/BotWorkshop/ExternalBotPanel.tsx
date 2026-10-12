import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import { BotRegistrationDialog } from '@/components/Workspace/IdentitySelector/BotRegistrationDialog';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { useExternalBots } from '@/hooks/useExternalBots';
import { externalBotToGeneralConfig, type ExternalBotDomain } from '@/services/botWorkshop/externalBotService';
import { PlugZap, RefreshCw, Search, Settings2 } from 'lucide-react';
import { useMemo, useState } from 'react';

function statusTone(bot: ExternalBotDomain): 'success' | 'warning' | 'neutral' {
  if (bot.status === 'online' && bot.reachability === 'reachable') return 'success';
  if (bot.status === 'online') return 'warning';
  return 'neutral';
}

function statusLabel(bot: ExternalBotDomain): string {
  if (bot.status === 'online' && bot.reachability === 'reachable') return '在线';
  if (bot.status === 'online') return '不可达';
  if (bot.status === 'hidden') return '已隐藏';
  return '离线';
}

export function ExternalBotPanel({ active }: { active: boolean }) {
  const externalBots = useExternalBots(active);
  const [keyword, setKeyword] = useState('');
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [configBot, setConfigBot] = useState<ExternalBotDomain>();
  const visibleItems = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    if (!normalized) return externalBots.items;
    return externalBots.items.filter(
      (bot) => bot.name.toLowerCase().includes(normalized) || bot.id.toLowerCase().includes(normalized),
    );
  }, [externalBots.items, keyword]);
  const columns = useMemo<DataTableColumn<ExternalBotDomain>[]>(
    () => [
      {
        id: 'name',
        header: 'Bot 名称',
        width: 'w-[260px]',
        cell: (bot) => (
          <div className="flex min-w-0 items-center gap-3">
            <Avatar name={bot.name} src={bot.avatarUrl} size={32} />
            <span className="truncate font-medium text-foreground">{bot.name}</span>
          </div>
        ),
      },
      {
        id: 'id',
        header: 'Bot ID',
        width: 'w-[300px]',
        cell: (bot) => <code className="block truncate text-xs text-muted-foreground">{bot.id}</code>,
      },
      {
        id: 'description',
        header: '描述',
        cell: (bot) => <span className="block truncate text-xs text-muted-foreground">{bot.description || '—'}</span>,
      },
      {
        id: 'status',
        header: '状态',
        width: 'w-[96px]',
        cell: (bot) => <Badge tone={statusTone(bot)}>{statusLabel(bot)}</Badge>,
      },
      {
        id: 'actions',
        header: '操作',
        width: 'w-[120px]',
        align: 'end',
        cell: (bot) => (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Settings2 className="size-3.5" aria-hidden />}
            onClick={() => setConfigBot(bot)}
          >
            通用配置
          </Button>
        ),
      },
    ],
    [],
  );

  if (externalBots.loading) return <Skeleton.Card />;
  if (externalBots.error)
    return (
      <Empty
        title="外部 Bot 列表加载失败"
        description={externalBots.error}
        action={
          <Button
            variant="secondary"
            leftIcon={<RefreshCw className="size-4" />}
            onClick={() => void externalBots.retry()}
          >
            重试
          </Button>
        }
      />
    );

  return (
    <section className="space-y-4" aria-label="外部 Bot 管理">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-full max-w-sm">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={keyword}
            className="pl-9"
            placeholder="搜索外部 Bot 名称或 ID"
            aria-label="搜索外部 Bot"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </div>
        <Button leftIcon={<PlugZap className="size-4" aria-hidden />} onClick={() => setRegistrationOpen(true)}>
          接入外部 Bot
        </Button>
      </div>
      {visibleItems.length ? (
        <DataTable columns={columns} rows={visibleItems} getRowKey={(bot) => bot.id} ariaLabel="外部 Bot 列表" />
      ) : (
        <Empty
          title={keyword ? '没有符合条件的外部 Bot' : '暂无已接入的外部 Bot'}
          description={keyword ? '尝试更换搜索关键词。' : '接入后，可在此查看并管理 Bot 的协作配置。'}
          action={keyword ? undefined : <Button onClick={() => setRegistrationOpen(true)}>接入外部 Bot</Button>}
        />
      )}
      <BotRegistrationDialog open={registrationOpen} onClose={() => setRegistrationOpen(false)} />
      {configBot ? (
        <BotGeneralConfigDialog bot={externalBotToGeneralConfig(configBot)} onClose={() => setConfigBot(undefined)} />
      ) : null}
    </section>
  );
}
