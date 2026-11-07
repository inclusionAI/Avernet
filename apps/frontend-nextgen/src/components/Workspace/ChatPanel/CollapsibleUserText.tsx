/**
 * 超长用户消息折叠 —— 包装组件与纯函数。
 *
 * 见 OpenSpec change `add-long-user-message-collapse`
 * (specs/long-user-message-collapse) 与 design.md D2–D5。
 *
 * 边界:折叠能力仅经 `ChatMessageList` 的 user 纯文本分支发声(单聊专用组件,
 * 协作群走独立 GroupChatMessageList 不引入本模块);assistant 消息与含附件的
 * user 消息不经本组件,零影响。
 *
 * 经 SDK `<Bubble renderText={...}>` escape hatch 注入:设置后 Bubble 对 text block
 * 不再调内部 markdown 渲染 → 本组件内部用 `MarkdownRenderer +
 * markdownPresets.full + [aixUiPlugin, fileRefPlugin]` 复刻 ChatMessageList 现有
 * markdown 配置(与 resolveConfig 的展开结果一致),视觉不漂移(design D2)。
 *
 * 复制全文不经本组件:气泡下方已有 `MessageCopyAction` 复制入口(2026-11-03
 * 用户拍板删除折叠行内的「复制全文」按钮,避免双复制入口)。
 */
import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import {
  aixUiPlugin,
  fileRefPlugin,
  MarkdownRenderer,
} from '@tc-chat/ui/es/MarkdownRender';
import { markdownPresets } from '@tc-chat/ui/es/MessageRenderer';
import React, { useLayoutEffect, useRef, useState } from 'react';

/** 折叠态最大展示高度(px,约 12–15 行正文)。视觉校准若需微调只改此常量(design D5)。 */
export const COLLAPSE_MAX_HEIGHT_PX = 340;

/** 行高兜底值:getComputedStyle 取不到 lineHeight 时以 22px 折算行数。 */
const FALLBACK_LINE_HEIGHT_PX = 22;

/** 本组件内部复刻 ChatMessageList 的 user text 渲染配置:`{preset:'full', extensions:[aixUiPlugin, fileRefPlugin]}`。 */
const USER_TEXT_EXTENSIONS = [...markdownPresets.full, aixUiPlugin, fileRefPlugin];

/**
 * 量高结论:是否超过折叠阈值 + 内容总行数。纯函数,单测直测。
 * 行数取四舍五入(渐隐遮罩下的最后一行本就不可全览,精确截断无意义)。
 */
export function computeCollapseMetrics(
  contentHeight: number,
  lineHeight: number,
  maxHeight: number = COLLAPSE_MAX_HEIGHT_PX,
): { overThreshold: boolean; lineCount: number } {
  const safeLineHeight =
    Number.isFinite(lineHeight) && lineHeight > 0
      ? lineHeight
      : FALLBACK_LINE_HEIGHT_PX;
  return {
    overThreshold: contentHeight > maxHeight,
    lineCount: Math.max(1, Math.round(contentHeight / safeLineHeight)),
  };
}

export interface CollapsibleUserTextProps {
  /** 消息纯文本正文。 */
  content: string;
  /** 本轮回复进行中(isRequesting 且为最新一条 user 消息):完整渲染,折叠 UI 不出现。 */
  forceExpanded?: boolean;
}

/**
 * 折叠壳组件。经 renderText 返回给 SDK Bubble,本地 expanded state 随 Bubble 实例存活。
 * 不满足折叠条件的消息经量高后自动退化为纯渲染(无遮罩、无操作行,零视觉增量)。
 */
function CollapsibleUserTextImpl({ content, forceExpanded }: CollapsibleUserTextProps) {
  const textRef = useRef<HTMLDivElement>(null);
  const [metrics, setMetrics] = useState<{ overThreshold: boolean; lineCount: number }>({
    overThreshold: false,
    lineCount: 0,
  });
  const [expanded, setExpanded] = useState(false);

  // 量高在 useLayoutEffect 内做:先量再决定 clamp,避免先闪完整高度再回跳。
  // 不监听 resize —— 接受窗口宽度变化下的陈旧判定(展开态永不受损)。
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    // 外层 clamp 不作用于本节点(scrollHeight 量的是整段正文,折叠态同样可测全量)。
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 0;
    setMetrics(computeCollapseMetrics(el.scrollHeight, lineHeight));
  }, [content]);

  // 本轮回复进行中(forceExpanded):完整渲染、不出任何折叠 UI —— 见 spec 延迟折叠。
  // 回复结束 forceExpanded 翻 false,expanded 仍为 false → 消息自然收起,无额外状态机。
  const collapsed = metrics.overThreshold && !expanded && !forceExpanded;
  const showActions = metrics.overThreshold && !forceExpanded;

  return (
    <>
      <div
        className="relative"
        style={collapsed ? { maxHeight: COLLAPSE_MAX_HEIGHT_PX, overflow: 'hidden' } : undefined}
      >
        <div ref={textRef}>
          <MarkdownRenderer content={content} extensions={USER_TEXT_EXTENSIONS} />
        </div>
        {collapsed ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 h-12"
            // 渐隐落点 = user 气泡底色(ChatMessageList 的 bubbleColor 'hsl(var(--muted))'),
            // 遮罩终点取实色保证与气泡底融合,不透出下层文字(design D5)。
            style={{ background: 'linear-gradient(to bottom, transparent, hsl(var(--muted)))' }}
          />
        ) : null}
      </div>
      {showActions ? (
        <div className="flex items-center justify-end gap-0.5 pt-1">
          <Button
            variant="ghost"
            className={cn('h-auto px-1.5 py-0.5 text-xs font-normal text-muted-foreground')}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? '收起' : `展开全部 · 共 ${metrics.lineCount} 行`}
          </Button>
        </div>
      ) : null}
    </>
  );
}

export const CollapsibleUserText: React.FC<CollapsibleUserTextProps> = React.memo(
  CollapsibleUserTextImpl,
);

export default CollapsibleUserText;
