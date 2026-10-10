// 管理 Bot 的发起归属 / 会话范围筛选(Popover)。
// 「会话范围」仅对 origin=mine 的管理 Bot 展示;好友 Bot 无归属概念,不渲染本组件。
// 仅回调解耦:归属/范围写入由父级经 Store 同步 setter 完成,组件不触达 Service。
import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { Check, ListFilter } from 'lucide-react';
import { useState } from 'react';

export interface ConversationOriginFilterProps {
  botId: string;
  origin: ConversationOrigin;
  /** 当前生效读取范围(others 强制 all)。 */
  scope: ConversationSessionScope;
  supportsFavorites?: boolean;
  /** 团队 Bot 他人会话后端尚未就绪；保留选项提示但不可操作。 */
  othersDisabled?: boolean;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
}

const ORIGIN_OPTIONS: Array<{ value: ConversationOrigin; label: string }> = [
  { value: 'mine', label: '我发起的' },
  { value: 'others', label: '他人发起的' },
];

const SCOPE_OPTIONS: Array<{ value: ConversationSessionScope; label: string }> = [
  { value: 'all', label: '全部会话' },
  { value: 'favorite', label: '仅看已收藏' },
];

function OptionButton(props: { role: 'radio'; label: string; checked: boolean; disabled?: boolean; onSelect(): void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      role={props.role}
      aria-checked={props.checked}
      disabled={props.disabled}
      onClick={(event) => {
        event.stopPropagation();
        props.onSelect();
      }}
      className={cn(
        'h-8 w-full justify-start gap-2 rounded-sm px-2 text-xs font-normal',
        props.checked && !props.disabled && 'bg-accent font-medium text-primary',
        props.disabled && 'text-muted-foreground',
      )}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
        {props.checked && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
      </span>
      <span className="flex-1 text-left">{props.label}</span>
    </Button>
  );
}

/**
 * 发起归属 + 会话范围筛选。内容常挂载(forceMount,关闭时 hidden),保证
 * 状态可测与无闪烁;选中即回调并关闭 Popover。
 */
export function ConversationOriginFilter({
  origin,
  scope,
  supportsFavorites = true,
  othersDisabled = false,
  onOriginChange,
  onScopeChange,
}: ConversationOriginFilterProps) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label={`${supportsFavorites ? '发起归属与会话范围' : '发起归属'}（${
            origin === 'mine' ? '我发起的' : '他人发起的'
          }）`}
          ariaLabel={supportsFavorites ? '发起归属与会话范围' : '发起归属'}
          size="sm"
          icon={
            <span className="relative flex items-center justify-center">
              <ListFilter className="h-3.5 w-3.5" aria-hidden="true" />
              {(origin === 'others' || (supportsFavorites && scope === 'favorite')) && (
                <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
              )}
            </span>
          }
          className={cn(
            // 底色 2026-10-10 用户反馈修正：dmore artboard-003 稿注释实测
            //「点击筛选项后，icon 改为蓝色高亮，并有圆点提示」——激活态只有蓝色 icon + 圆点，
            // 不画 bg-primary/10 底色方块（自绘底与所在行/操作区底色不一致）。
            // hover 与同排「新建会话」按钮统一走 muted 灰系，激活态 hover 保持蓝色不被覆盖。
            'h-6 w-6 rounded-md hover:bg-muted',
            origin === 'others' || (supportsFavorites && scope === 'favorite')
              ? 'text-primary hover:text-primary'
              : 'text-muted-foreground hover:text-foreground',
          )}
        />
      </PopoverTrigger>
      <PopoverContent
        forceMount
        align="end"
        // 关闭时仍然挂载(供状态测试与动画),仅以 hidden 收起;open 态恢复显示。
        className={cn('w-48 p-1', !open && 'hidden')}
        onClick={(event) => event.stopPropagation()}
      >
        <p className="m-0 px-2 pb-1 pt-1 text-xs font-medium text-muted-foreground">发起归属</p>
        <div role="radiogroup" aria-label="发起归属">
          {ORIGIN_OPTIONS.map((option) => (
            <OptionButton
              key={option.value}
              role="radio"
              label={option.value === 'others' && othersDisabled ? '他人发起的（开发中）' : option.label}
              disabled={option.value === 'others' && othersDisabled}
              checked={origin === option.value}
              onSelect={() => {
                onOriginChange(option.value);
                setOpen(false);
              }}
            />
          ))}
        </div>
        {origin === 'mine' && supportsFavorites && (
          <>
            <p className="m-0 px-2 pb-1 pt-2 text-xs font-medium text-muted-foreground">会话范围</p>
            <div role="radiogroup" aria-label="会话范围">
              {SCOPE_OPTIONS.map((option) => (
                <OptionButton
                  key={option.value}
                  role="radio"
                  label={option.label}
                  checked={scope === option.value}
                  onSelect={() => {
                    onScopeChange(option.value);
                    setOpen(false);
                  }}
                />
              ))}
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
