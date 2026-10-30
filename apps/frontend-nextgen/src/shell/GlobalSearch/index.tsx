// 全局搜索弹窗（设计稿 artboard-016「Dialog」还原）：
// 全屏 rgba(0,0,0,0.5) 遮罩 + 居中 638×478 面板（圆角 10、1px #EEE 边、稿面双层微影），
// 64px 头部（16px 搜索图标 + 13px/-0.15px 输入 + 32px 圆角 10 关闭钮内 12px X，距右 18px），
// 64px 筛选带（全部/Bot/对话/协作群 等分 chips，垂直居中；选中态主色 12% 底 + 圆角 8），
// 结果区 64px 行紧密铺排、零行距（32px 图标槽 + 13px 标题 + 12px 灰描「·」分隔副行），
// 选中行 #F3F3F2 平底 + 圆角 10，右箭头仅随选中行显隐（opacity 切换防截断跳动）；
// 键盘 ↑/↓ 选中 + Enter 直达 + Esc 关闭；行点击经深链跳转并收起。
// 设计稿的「文件」筛选无后端搜索数据源支撑，暂不提供（避免空壳筛选项），有接口后按稿补齐。
import { Empty, Modal, ModalContent, ModalTitle } from '@/components/ui';
import { cn } from '@/utils/cn';
import { history } from '@umijs/max';
import { Bot as BotIcon, ChevronRight, MessageSquare, Search, Users, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import type { GlobalSearchKind, GlobalSearchResult } from './useGlobalSearchModel';
import { useGlobalSearchModel } from './useGlobalSearchModel';

interface GlobalSearchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const FILTER_OPTIONS: Array<{ value: GlobalSearchKind; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'bot', label: 'Bot' },
  { value: 'conversation', label: '对话' },
  { value: 'group', label: '协作群' },
];

function resultIcon(result: GlobalSearchResult): ReactNode {
  if (result.kind === 'bot' && result.avatarUrl)
    return <img src={result.avatarUrl} alt="" className="size-4 rounded-full object-cover" />;
  if (result.kind === 'bot') return <BotIcon className="size-4" aria-hidden />;
  if (result.kind === 'group') return <Users className="size-4" aria-hidden />;
  return <MessageSquare className="size-4" aria-hidden />;
}

/** 行深链：Bot/会话走对话页路由（section 由目录匹配），协作群走协作页 group 深链。 */
function openResult(result: GlobalSearchResult, onDone: () => void) {
  const params = new URLSearchParams();
  if (result.kind === 'group') {
    if (result.groupId) params.set('group', result.groupId);
    const q = params.toString();
    history.push(q ? `/workspace/collaboration?${q}` : '/workspace/collaboration');
  } else {
    if (result.botId) params.set('bot', result.botId);
    if (result.sessionId) params.set('session', result.sessionId);
    const q = params.toString();
    history.push(q ? `/workspace/chat?${q}` : '/workspace/chat');
  }
  onDone();
}

function GlobalSearchDialogBody({ onOpenChange }: Omit<GlobalSearchDialogProps, 'open'>) {
  const { query, setQuery, filter, setFilter, results, isLoading } = useGlobalSearchModel(true);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // 关键词/筛选变化后清掉越界的键盘选中位（-1 = 未触碰键盘/hover，Enter 落空取首行）。
  useEffect(() => {
    setActiveIndex((current) => {
      if (current < 0) return -1;
      return current >= results.length ? -1 : current;
    });
  }, [results.length]);

  const moveActive = useCallback(
    (delta: number) => {
      if (results.length === 0) return;
      setActiveIndex((current) => {
        const next = current < 0 && delta < 0 ? 0 : current + delta;
        return Math.max(0, Math.min(results.length - 1, next));
      });
    },
    [results.length],
  );

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === 'Enter') {
      const target = activeIndex >= 0 ? results[activeIndex] : results[0];
      if (!target) return;
      event.preventDefault();
      openResult(target, () => onOpenChange(false));
    }
  };

  // 键盘选中的行滚入可视区。
  useEffect(() => {
    // jsdom 无 scrollIntoView 实现，可选调用保持测试环境可跑。
    if (activeIndex >= 0) rowRefs.current[activeIndex]?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);

  const rows = useMemo(
    () =>
      results.map((result, index) => {
        // 选中态：键盘/交互动过分毫必亮；未触碰前排（-1）默认点亮首行（对齐设计稿首行高亮）。
        const active = activeIndex === index || (activeIndex < 0 && index === 0);
        return (
          <button
            key={result.key}
            ref={(node) => {
              rowRefs.current[index] = node;
            }}
            type="button"
            onClick={() => openResult(result, () => onOpenChange(false))}
            onMouseEnter={() => setActiveIndex(index)}
            className={cn(
              // 设计稿：行高整 64px 且零行距紧密铺排，自定义行内 10px 横向留白。
              'flex h-16 w-full items-center gap-2 rounded-[10px] px-2.5 text-left outline-none',
              'transition-colors focus-visible:ring-1 focus-visible:ring-ring',
              active && 'bg-muted',
            )}
          >
            {/* 图标槽（设计稿：行左侧 32px 槽位内 16px 图标，#71717A） */}
            <span className="grid size-8 shrink-0 place-items-center text-muted-foreground">
              {resultIcon(result)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] leading-5 tracking-[-0.15px] text-foreground">
                {result.title}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 truncate text-xs leading-4 text-content-soft">
                {result.metaParts.map((part, partIndex) => (
                  <span key={partIndex} className="flex items-center gap-1.5 truncate">
                    {partIndex > 0 && <span aria-hidden>·</span>}
                    <span className="truncate">{part}</span>
                  </span>
                ))}
              </span>
            </span>
            {/* 右箭头仅随选中行显隐（稿面语义：选中即「直达」），占位恒在防截断跳动 */}
            <ChevronRight
              className={cn(
                'size-4 shrink-0 text-muted-foreground transition-opacity',
                active ? 'opacity-100' : 'opacity-0',
              )}
              aria-hidden
            />
          </button>
        );
      }),
    [results, activeIndex, onOpenChange],
  );

  return (
    <Modal open onOpenChange={onOpenChange}>
      <ModalContent
        showClose={false}
        className={cn(
          'flex h-[478px] max-h-[calc(100vh-2rem)] w-[638px] max-w-[calc(100vw-2rem)] flex-col gap-0',
          'overflow-hidden rounded-[10px] border-border/80 p-0',
          // 稿面双层微影（artboard-016 Dialog 实测），替代 Modal 默认 shadow-lg 的通用值。
          'shadow-[0_2px_4px_-2px_rgba(0,0,0,0.03),0_4px_22px_0_rgba(0,0,0,0.06)]',
        )}
        onOpenAutoFocus={(event) => {
          // 焦点直接落搜索输入，去掉 Radix 默认对第一个可聚焦元素的等待。
          event.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <ModalTitle className="sr-only">搜索</ModalTitle>
        {/* 头部（设计稿：64px 高、底描边；图标距左 16，关闭钮距右 18） */}
        <div className="flex h-16 shrink-0 items-center gap-2 border-b border-divider pl-4 pr-[18px]">
          <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder="搜索对话名称或你的Agent名称"
            aria-label="搜索对话名称或你的Agent名称"
            className="h-10 min-w-0 flex-1 bg-transparent text-[13px] tracking-[-0.15px] text-foreground outline-none placeholder:text-content-hint"
          />
          <button
            type="button"
            aria-label="关闭搜索"
            onClick={() => onOpenChange(false)}
            className="grid size-8 shrink-0 place-items-center rounded-[10px] text-muted-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-1 focus-visible:ring-ring"
          >
            <X className="size-3" aria-hidden />
          </button>
        </div>
        {/* 筛选 chips（设计稿：同 64px 高带内垂直居中，段间 4px；选中主色 12% 底 + 圆角 8） */}
        <div className="flex h-16 shrink-0 items-center gap-1 px-4" role="group" aria-label="搜索范围筛选">
          {FILTER_OPTIONS.map((option) => {
            const active = filter === option.value;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(option.value)}
                className={cn(
                  // 未选中圆角 10、纯文字；选中态换圆角 8 + 主色 12% 底（设计稿两种态圆角刻意不同）。
                  'h-8 flex-1 text-xs font-medium leading-4 outline-none transition-colors',
                  active
                    ? 'rounded-lg bg-primary/[0.12] text-primary'
                    : 'rounded-[10px] text-foreground hover:bg-muted/60',
                )}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        {/* 结果区（设计稿：348px Suggestions，行高 64 零行距，上方 4px 段前距） */}
        <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-4 pb-1 pt-1">
          {results.length === 0 ? (
            isLoading ? (
              <div className="space-y-1" aria-live="polite" aria-label="搜索结果加载中">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="flex h-16 items-center gap-2 px-2.5">
                    <span className="size-8 shrink-0 animate-pulse rounded-lg bg-muted" />
                    <span className="flex-1 space-y-1.5">
                      <span className="block h-3.5 w-2/5 animate-pulse rounded-md bg-muted" />
                      <span className="block h-3 w-3/5 animate-pulse rounded-md bg-muted" />
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty title="未找到相关内容" description="换个关键词，或切换上方筛选范围试试" compact />
            )
          ) : (
            rows
          )}
        </div>
      </ModalContent>
    </Modal>
  );
}

/** 全局搜索弹窗。关闭态彻底卸载（模型请求链不随侧边栏常驻），重开即重拉一次保持结果新鲜。 */
export function GlobalSearchDialog({ open, onOpenChange }: GlobalSearchDialogProps) {
  if (!open) return null;
  return <GlobalSearchDialogBody onOpenChange={onOpenChange} />;
}