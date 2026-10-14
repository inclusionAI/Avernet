// 管理 Bot 的发起归属 / 会话范围筛选(Popover);好友 Bot 经 supportsOrigin=false 复用,
// 只保留「会话:全部/已收藏」分组(无「发起人」概念,spec 2026-10-14 session-key-search)。
// 仅回调解耦:归属/范围写入由父级经 Store 同步 setter 完成,组件不触达 Service。
// 气泡版式对齐 dmore artboard-003「click筛选icon」(Frame 1912056215,171×154):白底圆角 8、
// 多层投影,分组小题「发起人」「会话」(12px,颜色取设计 #525252/70% 合成 ≈ #868686 →
// text-content-soft),双组灰轨分段控件(轨道 #F5F5F5 → bg-muted,选中白胶囊 h26/r6 +
// 0 1px 3px 阴影 + 文字 #09090B),弹层内边距 13/17、组间距 16、胶囊随内容自适应宽。
import { IconButton, Popover, PopoverContent, PopoverTrigger, Segmented } from '@/components/ui';
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { ListFilter } from 'lucide-react';
import { useState } from 'react';

export interface ConversationOriginFilterProps {
  botId: string;
  origin: ConversationOrigin;
  /** 当前生效读取范围(others 强制 all)。 */
  scope: ConversationSessionScope;
  /** 是否展示「发起人」分组;好友 Bot 无归属概念,仅保留「会话」范围(dmore 截图 3.png)。 */
  supportsOrigin?: boolean;
  supportsFavorites?: boolean;
  /** 团队 Bot 他人会话后端尚未就绪；保留选项提示但不可操作。 */
  othersDisabled?: boolean;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
}

// 选项胶囊:13px 常规字重、letter-spacing -0.15px、内边距 9px、高 26px(稿面 DOM 实测);
// flex-none 使胶囊随内容自适应宽度,复刻稿面 57/70 与 44/57 的胶囊宽度比。
const OPTION_BASE = 'flex-none h-[26px] rounded-md px-[9px] text-[13px] font-normal tracking-[-0.15px]';
// 选中态文字 #09090B → text-foreground;阴影取稿面 0 1px 3px rgba(0,0,0,0.1)。
const OPTION_ACTIVE = 'text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.1)]';
// 未选中文字 #71717A = --muted-foreground;zinc 语义 token 同源,hover 提亮沿用 SquareSearchBar 惯例。
const OPTION_INACTIVE = 'text-muted-foreground hover:text-foreground';

/**
 * 发起归属 + 会话范围筛选。内容常挂载(forceMount,关闭时 hidden),保证
 * 状态可测与无闪烁;选中即回调并关闭 Popover。
 */
export function ConversationOriginFilter({
  origin,
  scope,
  supportsOrigin = true,
  supportsFavorites = true,
  othersDisabled = false,
  onOriginChange,
  onScopeChange,
}: ConversationOriginFilterProps) {
  const [open, setOpen] = useState(false);
  // 激活态:管理/团队看归属(others)或范围(favorite);好友只看范围。
  const filterActive = (supportsOrigin && origin === 'others') || (supportsFavorites && scope === 'favorite');
  const triggerName = supportsOrigin ? (supportsFavorites ? '发起归属与会话范围' : '发起归属') : '会话范围';
  const triggerState = supportsOrigin
    ? origin === 'mine'
      ? '我发起的'
      : '他人发起的'
    : scope === 'favorite'
    ? '已收藏'
    : '全部';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label={`${triggerName}（${triggerState}）`}
          ariaLabel={triggerName}
          size="sm"
          icon={
            <span className="relative flex items-center justify-center">
              <ListFilter className="h-3.5 w-3.5" aria-hidden="true" />
              {filterActive && (
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
            filterActive ? 'text-primary hover:text-primary' : 'text-muted-foreground hover:text-foreground',
          )}
        />
      </PopoverTrigger>
      <PopoverContent
        forceMount
        align="end"
        className={cn(
          'flex w-[171px] flex-col gap-4 rounded-lg px-[13px] py-[17px]',
          // 稿面多层投影实测(dmore artboard-003):0 9 28 8 / 0 6 16 / 0 3 6 -4px。
          'shadow-[0_9px_28px_8px_rgba(0,10,26,0.04),0_6px_16px_0px_rgba(0,10,26,0.07),0_3px_6px_-4px_rgba(155,166,185,0.11)]',
          // 关闭时仍然挂载(供状态测试与动画),仅以 hidden 收起;open 态恢复显示。
          !open && 'hidden',
        )}
        onClick={(event) => event.stopPropagation()}
      >
        {supportsOrigin && (
          <section>
            <p className="m-0 text-xs font-normal leading-4 text-content-soft">发起人</p>
            <Segmented
              className="mt-1 w-fit p-[3px]"
              aria-label="发起人"
              value={origin}
              options={[
                { value: 'mine', label: '我发起' },
                ...(othersDisabled
                  ? [{ value: 'others' as const, label: '他人发起', disabledReason: '他人发起的会话开发中' }]
                  : [{ value: 'others' as const, label: '他人发起' }]),
              ]}
              onChange={(next) => {
                onOriginChange(next);
                setOpen(false);
              }}
              activeOptionClassName={cn(OPTION_BASE, OPTION_ACTIVE)}
              inactiveOptionClassName={cn(OPTION_BASE, OPTION_INACTIVE)}
            />
          </section>
        )}
        {origin === 'mine' && supportsFavorites && (
          <section>
            <p className="m-0 text-xs font-normal leading-4 text-content-soft">会话</p>
            <Segmented
              className="mt-1 w-fit p-[3px]"
              aria-label="会话"
              value={scope}
              options={[
                { value: 'all', label: '全部' },
                { value: 'favorite', label: '已收藏' },
              ]}
              onChange={(next) => {
                onScopeChange(next);
                setOpen(false);
              }}
              activeOptionClassName={cn(OPTION_BASE, OPTION_ACTIVE)}
              inactiveOptionClassName={cn(OPTION_BASE, OPTION_INACTIVE)}
            />
          </section>
        )}
      </PopoverContent>
    </Popover>
  );
}
