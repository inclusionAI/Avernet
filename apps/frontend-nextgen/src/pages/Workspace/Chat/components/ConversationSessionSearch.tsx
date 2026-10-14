// sessionKey 搜索入口(参照 dmore 截图 1.png/2.png):Bot 行操作区的放大镜按钮 + Popover 搜索。
// 仅受控契约:value = 当前已生效条件(空串 = 未生效),onSearch = 提交(空串 = 清除);
// 弹层内只是本地草稿:键入不发请求,关闭即丢弃,重开回到当前已生效条件。
// 版式:弹层为纵向表单(截图 1.png)——通栏输入在上、右对齐文字态「搜索」按钮在下;
// 生效态按钮加宽为浅主色胶囊,数字 1 标签与图标内联并排(截图 2.png),不再覆盖图标角。
import { Button, IconButton, Input, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export interface ConversationSessionSearchProps {
  /** 当前已生效的 sessionKey 条件;空串表示未生效。 */
  value: string;
  /** 提交草稿(组件内已 trim 首尾空白、保留内部内容);空串表示清除条件。 */
  onSearch(value: string): void;
}

/** 行内搜索按钮 + 弹层。提交后关闭弹层;展开对应 Bot 由装配层决定,组件不触达 Store。
 *  内容常挂载(forceMount,关闭时 hidden,与 ConversationOriginFilter 同约定):
 *  草稿在“打开瞬间”重置为当前已生效条件,关闭(含 Escape/点外)不提交、直接丢弃。 */
export function ConversationSessionSearch({ value, onSearch }: ConversationSessionSearchProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  // 非空条件生效中(截图 2.png):按钮加宽为浅主色胶囊 + 数字 1 标签与图标内联。
  const active = value !== '';
  const handleOpenChange = (next: boolean) => {
    // 打开瞬间回到已生效条件;关闭丢弃未提交草稿。
    if (next) setDraft(value);
    setOpen(next);
  };
  useEffect(() => {
    // 弹层常挂载,聚焦由 open 变化驱动(键盘/读屏无需依赖 Radix 默认的挂载聚焦)。
    if (open) inputRef.current?.focus();
  }, [open]);
  const submit = () => {
    onSearch(draft.trim());
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <IconButton
          // 提示文案随生效态描述条件(读屏名称保持稳定,由 ariaLabel 承载)。
          label={active ? '搜索会话（已生效）' : '搜索会话'}
          ariaLabel="搜索会话"
          size="sm"
          icon={
            <span className="flex items-center gap-1">
              <Search className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {active && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'flex h-3 min-w-3 items-center justify-center rounded-full',
                    'bg-primary px-[3px] text-[9px] font-semibold leading-none text-primary-foreground',
                  )}
                >
                  1
                </span>
              )}
            </span>
          }
          className={cn(
            'h-6 rounded-md',
            // 未生效 24px 与同排筛选/加号等宽;生效态加宽(≈40px)容纳内联标签,悬停保持浅主色胶囊。
            active
              ? 'w-10 bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary'
              : 'w-6 text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        />
      </PopoverTrigger>
      <PopoverContent
        forceMount
        align="end"
        // 弹层版式对齐 dmore 截图 1.png:语义白底圆角气泡+充足内边距(默认 p-4),关闭时以 hidden 收起。
        className={cn(!open && 'hidden')}
        onClick={(event) => event.stopPropagation()}
        onOpenAutoFocus={(event) => {
          // 常挂载内容会在挂载瞬间触发默认聚焦;聚焦改由上方 open effect 驱动,这里显式拦截。
          event.preventDefault();
        }}
      >
        <form
          className="flex flex-col items-stretch gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Input
            ref={inputRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter 与「搜索」按钮行为一致;阻止默认提交避免双发。
              if (event.key !== 'Enter') return;
              event.preventDefault();
              submit();
            }}
            placeholder="sessionKey"
            aria-label="sessionKey"
            autoComplete="off"
          />
          <Button type="submit" variant="link" className="h-7 self-end px-2 text-sm no-underline">
            搜索
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
