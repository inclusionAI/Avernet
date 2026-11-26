import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Input } from '@/components/ui/Input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Switch } from '@/components/ui/Switch';
import type { RecoveryBotOption, RecoveryBotOptionsPage } from '@/domain/recovery/types';
import { recoveryService } from '@/services/recovery/recoveryService';
import { Bot, ChevronDown } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ErrorBox } from '../shared';

function useBotOptions(spaceId: string, visible: boolean) {
  const [search, setSearch] = useState('');
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [result, setResult] = useState<RecoveryBotOptionsPage>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const cursor = cursors[cursors.length - 1];
  useEffect(() => {
    if (!visible || !spaceId) return;
    const controller = new AbortController();
    setError('');
    setResult(undefined);
    const timer = window.setTimeout(
      () => {
        recoveryService.bots(spaceId, search.trim(), cursor, controller.signal).then(
          (value) => {
            if (!controller.signal.aborted) setResult(value);
          },
          (failure: unknown) => {
            if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Bot 加载失败');
          },
        );
      },
      search ? 250 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [spaceId, cursor, revision, search, visible]);
  return {
    result,
    error,
    search,
    changeSearch: (next: string) => {
      setResult(undefined);
      setSearch(next);
      setCursors([undefined]);
    },
    page: cursors.length,
    prev: () => setCursors((value) => (value.length > 1 ? value.slice(0, -1) : value)),
    next: () => {
      const next = result?.nextCursor;
      if (next) setCursors((value) => [...value, next]);
    },
    reload: () => setRevision((value) => value + 1),
  };
}

type BotDirectory = ReturnType<typeof useBotOptions>;

function OptionsFooter({ options, busy }: { options: BotDirectory; busy?: boolean }) {
  if (options.page === 1 && !options.result?.nextCursor) return null;
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-xs text-muted-foreground">
      <Button type="button" variant="outline" size="sm" disabled={busy || options.page < 2} onClick={options.prev}>
        上一页
      </Button>
      <span>第 {options.page} 页</span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy || !options.result?.nextCursor}
        onClick={options.next}
      >
        下一页
      </Button>
    </div>
  );
}

function EmptyOptions({ options }: { options: BotDirectory }) {
  if (options.error) return <ErrorBox message={options.error} retry={options.reload} />;
  if (!options.result)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        正在加载 Bot…
      </p>
    );
  if (!options.result.items.length) return <p className="text-sm text-muted-foreground">没有找到 Bot。</p>;
  return null;
}

export function RecoveryBotFilter({
  spaceId,
  label,
  onSelect,
}: {
  spaceId: string;
  label?: string;
  onSelect(bot?: RecoveryBotOption): void;
}) {
  const [open, setOpen] = useState(false);
  const options = useBotOptions(spaceId, open);
  const pick = (bot?: RecoveryBotOption) => {
    onSelect(bot);
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline" className="max-w-56" leftIcon={<Bot size={14} />}>
          <span className="truncate">{label || '全部 Bot'}</span>
          <ChevronDown size={14} className="shrink-0" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" aria-label="筛选 Bot" className="w-80 max-w-[calc(100vw-1.5rem)]">
        <Input
          aria-label="搜索 Bot"
          placeholder="搜索 Bot 名称或 ID"
          value={options.search}
          onChange={(e) => options.changeSearch(e.target.value)}
        />
        <div className="mt-2 max-h-64 overflow-y-auto">
          <Button type="button" variant="ghost" className="w-full justify-start" onClick={() => pick()}>
            全部 Bot
          </Button>
          {options.result?.items.map((bot) => (
            <Button
              type="button"
              variant="ghost"
              className="block h-auto w-full min-w-0 rounded-md border-0 px-3 py-2 text-left font-normal hover:bg-muted"
              key={bot.botRef}
              onClick={() => pick(bot)}
            >
              <span className="block truncate text-sm font-medium">{bot.botName}</span>
              <span className="block truncate text-xs text-muted-foreground">
                {bot.botId} · {bot.ownerId} · {bot.env}
              </span>
            </Button>
          ))}
        </div>
        <EmptyOptions options={options} />
        <OptionsFooter options={options} />
      </PopoverContent>
    </Popover>
  );
}

export function RecoverySettings({
  spaceId,
  onClose,
  onChanged,
}: {
  spaceId: string;
  onClose(): void;
  onChanged(): void;
}) {
  const options = useBotOptions(spaceId, true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function save(input: Parameters<typeof recoveryService.configure>[1]) {
    if (busy) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await recoveryService.configure(spaceId, input);
      setMessage(
        result.failed.length
          ? `已更新 ${result.updated} 个 Bot，${result.failed.length} 个未成功，请刷新后重试。`
          : `已更新 ${result.updated} 个 Bot。`,
      );
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '设置未更新，请刷新后重试。');
    } finally {
      options.reload();
      setBusy(false);
    }
  }

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DrawerContent size="lg" className="max-w-full" showClose={!busy} closeLabel="关闭自愈设置">
        <DrawerHeader>
          <DrawerTitle className="text-lg font-semibold">自愈设置</DrawerTitle>
          <DrawerDescription className="text-sm text-muted-foreground">
            诊断自愈会监测 Bot 的会话。发现异常时，系统会分析原因，并尝试让原任务继续执行。
          </DrawerDescription>
        </DrawerHeader>
        <p className="mb-4 rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
          展示当前空间的 Bot。批量设置覆盖此空间内所有可自行设置的生产 Bot，包含当前搜索和分页之外的 Bot。 桌面
          Bot 暂不支持诊断自愈。
        </p>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Input
            aria-label="搜索我的 Bot"
            placeholder="搜索 Bot 名称或 ID"
            className="min-w-40 flex-1"
            disabled={busy}
            value={options.search}
            onChange={(e) => options.changeSearch(e.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || !options.result}
            onClick={() => void save({ all: true, enabled: false })}
          >
            一键全关
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || !options.result}
            onClick={() => void save({ all: true, enabled: true })}
          >
            一键全开
          </Button>
        </div>
        <ErrorBox message={error} retry={options.reload} />
        {message && (
          <p role="status" className="mb-3 text-sm text-foreground">
            {message}
          </p>
        )}
        <EmptyOptions options={options} />
        <ul className="m-0 list-none divide-y divide-border p-0">
          {options.result?.items.map((bot) => {
            const config = bot.configuration;
            const enabled = Boolean(config?.diagnosisEnabled && config.recoveryEnabled);
            const unavailable =
              bot.configurationDisabledReason || (bot.canConfigure === false ? '当前 Bot 不可自行设置' : '');
            const engine = bot.activeEngine?.toLowerCase();
            const engineLabel = engine === 'openclaw' ? 'OpenClaw' : engine === 'teclaw' ? 'TE' : bot.activeEngine;
            const typeLabel =
              bot.botType === 'personal'
                ? '个人 Bot'
                : bot.botType === 'service'
                ? '服务 Bot'
                : bot.botType === 'desktop'
                ? '桌面 Bot'
                : bot.botType;
            return (
              <li className="flex items-center gap-3 py-3" key={bot.botRef}>
                <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="m-0 break-words text-sm font-medium">{bot.botName}</p>
                  <p className="m-0 break-words text-xs text-muted-foreground">
                    {[bot.botId, engineLabel, typeLabel, bot.env].filter(Boolean).join(' · ')}
                  </p>
                  {unavailable && <p className="m-0 mt-1 text-xs text-muted-foreground">{unavailable}</p>}
                </div>
                <span className="shrink-0 text-xs text-muted-foreground">{enabled ? '已开启' : '已关闭'}</span>
                <Switch
                  aria-label={`${bot.botName} 诊断自愈`}
                  checked={enabled}
                  disabled={busy || Boolean(unavailable)}
                  onCheckedChange={(next) =>
                    void save({ botRef: bot.botRef, version: config?.version ?? 0, enabled: next })
                  }
                />
              </li>
            );
          })}
        </ul>
        <OptionsFooter options={options} busy={busy} />
      </DrawerContent>
    </Drawer>
  );
}
