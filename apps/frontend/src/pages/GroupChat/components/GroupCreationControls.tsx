import React from 'react';
import * as Select from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import { Button, Spin } from '@/components';
import BotAvatar from '@/components/BotAvatar';
import { GROUP_CREATION_HOST, groupLink } from '@/services/backend-api/GroupCreationController';
import type { GroupBot } from '@/services/backend-api/GroupCreationController';
import { cn } from '@/utils/utils';
import type { useGroupCreation, useGroupBotPage } from '../hooks/useGroupCreation';

type Creation = ReturnType<typeof useGroupCreation>;

export function GroupBotPager({ page, label }: { page: ReturnType<typeof useGroupBotPage>; label: string }) {
  return <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
    {page.loading ? <Spin /> : <span>共 {page.page.total} 个{label}</span>}
    <Button size="sm" variant="ghost" disabled={page.loading || page.page.offset === 0} onClick={page.previous} aria-label={`${label}上一页`}>上一页</Button>
    <Button size="sm" variant="ghost" disabled={page.loading || page.page.offset + 20 >= page.page.total} onClick={page.next} aria-label={`${label}下一页`}>下一页</Button>
    {page.error ? <><span role="alert">{page.error}</span><Button size="sm" variant="ghost" onClick={page.retry}>重试</Button></> : null}
  </div>;
}

function OriginatorBotLabel({ bot, selected, duplicate, option = false }: {
  bot: GroupBot; selected: boolean; duplicate: boolean; option?: boolean;
}) {
  const reachable = bot.reachability === 'reachable';
  return <>
    <BotAvatar type="assistant" size="sm" name={bot.name} botId={bot.bot_id.split(':')[0]} />
    <span className="min-w-0 flex-1 text-left">
      <span className="flex flex-wrap items-center gap-2">
        <span className="truncate text-sm font-medium">{option ? <Select.ItemText>{bot.name}</Select.ItemText> : bot.name}</span>
        <span className={cn('shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
          reachable ? 'bg-green-50 text-green-600' : 'bg-muted text-muted-foreground')}>
          {reachable ? '在线' : '离线'}
        </span>
        {selected ? <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">发起方</span> : null}
      </span>
      {duplicate ? <span className="block truncate text-xs font-normal text-muted-foreground">{bot.bot_id}</span> : null}
    </span>
  </>;
}

export function GroupOriginatorPicker({ creation }: { creation: Creation }) {
  const selectId = React.useId();
  const { mine, originator } = creation;
  const options = originator && !mine.page.items.some(bot => bot.bot_id === originator.bot_id)
    ? [originator, ...mine.page.items] : mine.page.items;
  const hasDuplicateName = (bot: GroupBot) => options.some(other => other.bot_id !== bot.bot_id && other.name === bot.name);
  return <section className="mt-2 space-y-1" aria-label="发起方 Bot">
    <label htmlFor={selectId} className="block text-sm font-medium">发起方 Bot</label>
    <p className="text-xs text-muted-foreground">操作用户：{creation.user?.display_name || '尚未登录'}</p>
    <div className="flex items-center gap-2">
      <Select.Root value={originator?.bot_id || ''}
        disabled={mine.loading || !creation.user || !options.length}
        onValueChange={value => creation.setOriginator(options.find(bot => bot.bot_id === value))}>
        <Select.Trigger asChild>
          <Button id={selectId} variant="default"
            className="h-auto min-h-12 w-full justify-between gap-3 rounded-xl border border-input bg-background px-3 py-2 text-foreground hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-primary/20 data-[state=open]:border-primary/50">
            <span className="min-w-0 flex-1 text-left">
              <Select.Value placeholder={<span className="font-normal text-muted-foreground">{!mine.loading && !mine.error && !options.length ? '暂无自有 Bot' : '请选择发起方 Bot'}</span>}>
                {originator ? <span className="flex min-w-0 items-center gap-2.5">
                  <OriginatorBotLabel bot={originator} selected duplicate={hasDuplicateName(originator)} />
                </span> : undefined}
              </Select.Value>
            </span>
            <Select.Icon asChild><ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" /></Select.Icon>
          </Button>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content position="popper" align="start" sideOffset={6} collisionPadding={12}
            className="z-[200] max-h-[var(--radix-select-content-available-height)] w-[var(--radix-select-trigger-width)] overflow-hidden rounded-xl border border-input bg-popover text-popover-foreground shadow-lg">
            <Select.ScrollUpButton className="flex justify-center py-1 text-muted-foreground"><ChevronUp className="h-4 w-4" /></Select.ScrollUpButton>
            <Select.Viewport className="max-h-80 p-1.5">
              {options.map(bot => <Select.Item key={bot.bot_id} value={bot.bot_id} textValue={bot.name} title={bot.bot_id}
                className="relative flex cursor-pointer select-none items-center gap-2.5 rounded-lg py-2 pl-2 pr-9 outline-none data-[highlighted]:bg-accent data-[state=checked]:bg-primary/5">
                <OriginatorBotLabel bot={bot} selected={bot.bot_id === originator?.bot_id} duplicate={hasDuplicateName(bot)} option />
                <Select.ItemIndicator className="absolute right-3 flex items-center text-primary"><Check className="h-4 w-4" /></Select.ItemIndicator>
              </Select.Item>)}
            </Select.Viewport>
            <Select.ScrollDownButton className="flex justify-center py-1 text-muted-foreground"><ChevronDown className="h-4 w-4" /></Select.ScrollDownButton>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
      {mine.loading ? <Spin /> : null}
    </div>
    {mine.error || mine.page.total > mine.page.limit || mine.page.offset > 0
      ? <GroupBotPager page={mine} label="自有 Bot" /> : null}
  </section>;
}

export function GroupCreationStatus({ creation, startsWorkflow }: { creation: Creation; startsWorkflow: boolean }) {
  const { submission, user } = creation;
  return <div className="space-y-1 text-sm">
    {creation.error || submission.error ? <p role="alert" className="text-destructive">{creation.error || submission.error}</p> : null}
    {creation.error && !creation.frozen ? <Button size="sm" variant="ghost" onClick={creation.retry}>重新读取并确认登录</Button> : null}
    {submission.phase === 'unknown' ? <p role="alert">未能确认创建结果，请先检查群列表，避免重复建群。 <a className="text-primary underline" href={user ? groupLink(GROUP_CREATION_HOST, user) : '/bcn/chat/list'}>查看群列表</a></p> : null}
    {submission.phase === 'success' && user && user.user_id === submission.userId && submission.result ? <p role="status">
      已创建协作群。 <a className="text-primary underline" href={groupLink(GROUP_CREATION_HOST, user, submission.result)}>进入协作群</a>
    </p> : null}
    {submission.phase === 'success' && user?.user_id !== submission.userId ? <p role="alert">登录用户已变化，请切回原用户查看创建结果。</p> : null}
    {!creation.frozen ? <p className="text-xs text-muted-foreground">{startsWorkflow ? '确认后将建群并立即开始协作。' : '确认后将创建协作群。'}</p> : null}
  </div>;
}
