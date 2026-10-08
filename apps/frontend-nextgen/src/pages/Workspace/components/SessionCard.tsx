import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { MessageSquare } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';

function parseDate(input: number | string | undefined): Date | null {
  if (input === undefined || input === '' || input === 0) return null;
  const date =
    typeof input === 'number' ? new Date(input) : new Date(input.includes('T') ? input : input.replace(/-/g, '/'));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 秒/毫秒时间戳或后端时间字符串 → MM/DD(对齐会话卡片日期样式);无法解析时返回空串。 */
export function formatMonthDay(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${month}/${day}`;
}

/** 秒/毫秒时间戳或后端时间字符串 → MM/dd HH:mm;无法解析时返回空串。 */
export function formatMonthDayTime(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hour}:${minute}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function startOfWeek(date: Date): number {
  const day = date.getDay();
  const mondayOffset = day === 0 ? 6 : day - 1;
  return startOfDay(new Date(date.getFullYear(), date.getMonth(), date.getDate() - mondayOffset));
}

/** 稳定相对时间：当天 HH:mm、昨天、同周星期、本年 MM/DD、跨年 YYYY/MM/DD。 */
export function formatSessionTime(input: number | string | undefined, now = new Date()): string {
  const date = parseDate(input);
  if (!date) return '';
  if (startOfDay(date) === startOfDay(now)) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  const yesterday = startOfDay(now) - 24 * 60 * 60 * 1000;
  if (startOfDay(date) === yesterday) return '昨天';
  if (startOfWeek(date) === startOfWeek(now)) {
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
  }
  if (date.getFullYear() === now.getFullYear()) return formatMonthDay(input);
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(
    2,
    '0',
  )}`;
}

export function formatSessionTimeTooltip(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

interface SessionCardProps {
  title: string;
  /** 副行文案(真实预览/成员数等);未传时显示「暂无会话预览」。 */
  subtitle?: string;
  /** 右侧日期(MM/DD),无则不渲染。 */
  dateText?: string;
  /** 日期的完整本地时间 Tooltip。 */
  dateTooltip?: string;
  selected: boolean;
  onSelect: () => void;
  /** 右侧操作区（如更多菜单），与日期位置互换、悬停浮现。 */
  trailing?: React.ReactNode;
  /** 常显操作（如收藏星标）——与日期同排常显，不参与悬停显隐。 */
  persistentAction?: React.ReactNode;
  /** 无副行内容时使用更紧凑的单行布局。 */
  compact?: boolean;
  /** 对话使用消息 Icon，协作群会话保留圆点。 */
  indicator?: 'dot' | 'message';
  /** 调用方可按列表场景补充尺寸；不改变卡片的交互语义。 */
  className?: string;
}

/** 二级会话列表项:无卡片容器、缩进排列、会话圆点 + 标题/副行 + 日期。 */
export const SessionCard = React.memo(function SessionCard({
  title,
  subtitle = '暂无会话预览',
  dateText,
  selected,
  onSelect,
  trailing,
  persistentAction,
  compact = false,
  dateTooltip,
  indicator = 'dot',
  className,
}: SessionCardProps) {
  // v1.4：标题截断检测（scrollWidth > clientWidth，ResizeObserver 跟随容器宽度变化，
  // 模式同 SystemMessageItem）——仅在真实截断时挂 Tooltip 补全完整标题。
  const titleRef = useRef<HTMLSpanElement>(null);
  const [titleOverflowing, setTitleOverflowing] = useState(false);
  useEffect(() => {
    const el = titleRef.current;
    if (!el) return;
    const check = () => setTitleOverflowing(el.scrollWidth > el.clientWidth + 1);
    check();
    // 测试环境无 ResizeObserver 时仅保留初始检测，跳过动态观察。
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [title]);

  const titleNode = (
    <span
      ref={titleRef}
      className={cn(
        'block truncate text-xs leading-5',
        selected ? 'font-medium text-primary' : 'font-normal text-foreground',
      )}
    >
      {title}
    </span>
  );

  return (
    // v1.4：会话行保持全宽行式。
    // 验收微调：树形导轨改为每行自带——干线（data-session-tree-rail）+ 拐角横线（elbow）
    // 均由行渲染；非末行干线贯穿整行，末行经 :last-child 变体止于拐角高度，
    // 消除末行下方的多余线段；选中态行首显示 2px 品牌条。
    <div
      className={cn(
        'group relative flex items-stretch text-sm transition-colors',
        // 末行干线止于拐角高度：树形导轨不再垂到列表底部（jsdom 不计算 CSS 行为，测试锁定类名契约）。
        'last:[&_[data-session-tree-rail]]:bottom-1/2',
        compact ? 'min-h-12' : 'min-h-15',
        selected ? 'bg-primary/10' : 'hover:bg-primary/5',
        className,
      )}
    >
      {/* 干线：对齐容器缩进 16px 处（行内 -left-2），非末行贯穿整行、末行止于拐角。 */}
      <span data-session-tree-rail aria-hidden="true" className="absolute -left-2 top-0 bottom-0 w-px bg-border" />
      {/* 拐角横线 8px：左端紧贴干线、右端无缝接到会话卡片行首——完整下钻路径。 */}
      <span data-session-tree-elbow aria-hidden="true" className="absolute -left-2 top-1/2 h-px w-2 bg-border" />
      {selected && (
        <span
          data-session-left-bar
          aria-hidden="true"
          className="absolute bottom-1 left-0 top-1 w-0.5 rounded-r-sm bg-primary"
        />
      )}
      <Button
        variant="ghost"
        aria-pressed={selected}
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'flex h-auto min-w-0 flex-1 justify-start gap-2 rounded-none px-2.5 text-left hover:bg-transparent focus-visible:z-10',
          compact ? 'items-center' : 'items-start',
          compact ? 'min-h-12 py-2' : 'min-h-15 py-2.5',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center',
            indicator === 'message' ? 'self-center' : !compact && 'mt-0.5',
          )}
        >
          {indicator === 'message' ? (
            <MessageSquare
              data-session-indicator
              className={cn(
                'h-3.5 w-3.5',
                selected ? 'text-primary' : 'text-muted-foreground group-hover:text-primary/70',
              )}
            />
          ) : (
            <span
              data-session-indicator
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                selected ? 'bg-primary' : 'bg-muted-foreground/50 group-hover:bg-primary/60',
              )}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          {/* Tooltip 结构常挂（span 不因溢出态切换而重建，保证 ref/ResizeObserver 稳定）；
              仅在真实截断时渲染内容，未截断悬停不弹窗。 */}
          <TooltipProvider delayDuration={300}>
            <Tooltip>
              <TooltipTrigger asChild>{titleNode}</TooltipTrigger>
              {titleOverflowing && <TooltipContent>{title}</TooltipContent>}
            </Tooltip>
          </TooltipProvider>
          {subtitle && (
            <span
              className={cn(
                'mt-0.5 block truncate text-xs leading-5',
                selected ? 'text-primary/80' : 'text-muted-foreground',
              )}
            >
              {subtitle}
            </span>
          )}
        </div>
      </Button>
      {/* v1.4：右侧结构 = 常显操作（收藏星标）+ 日期 + 悬停浮现的更多操作。
          日期占流常显；操作按钮绝对定位叠放右缘，悬停/键盘聚焦时原位浮现且日期淡出，
          消除非悬停时的透明占位空块；触屏设备（hover:none）操作按钮回到流内与日期并排常显。
          验收微调：日期改定宽右对齐槽位（min-w-9=36px，贴合最长常见格式「09/02/14:30」约 30px）——
          星标在日期左侧，槽位定宽后星标 x 位置不随「昨天/周X/MM/DD」等格式宽度漂移；
          槽宽从 48px 收紧到 36px，避免短日期（昨天/周X）在星标与日期间留出大空隙。 */}
      {(dateText || trailing || persistentAction) && (
        <div
          className={cn(
            'relative flex shrink-0 gap-2 pr-3 text-xs text-muted-foreground',
            compact ? 'items-center' : 'items-center pt-2',
          )}
        >
          {persistentAction && <span className="flex items-center">{persistentAction}</span>}
          {dateText && dateTooltip ? (
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="min-w-9 shrink-0 whitespace-nowrap text-right leading-5 transition-opacity [@media(hover:hover)]:group-hover:opacity-0 [@media(hover:hover)]:group-focus-within:opacity-0">
                    {dateText}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{dateTooltip}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : (
            dateText && (
              <span className="min-w-9 shrink-0 whitespace-nowrap text-right leading-5 transition-opacity [@media(hover:hover)]:group-hover:opacity-0 [@media(hover:hover)]:group-focus-within:opacity-0">
                {dateText}
              </span>
            )
          )}
          {trailing && (
            <span className="absolute right-3 flex items-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:relative [@media(hover:none)]:opacity-100">
              {trailing}
            </span>
          )}
        </div>
      )}
    </div>
  );
});
