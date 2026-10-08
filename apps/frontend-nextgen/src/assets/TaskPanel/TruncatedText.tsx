// @asset-migrated: teamclaw 自研资产
/** 可悬浮查看完整内容的截断文本。 */
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { truncateText } from './text';
import { C } from './tokens';

type TextTag = 'span' | 'div' | 'h1' | 'h2';

interface TooltipPosition {
  left: number;
  top: number;
  maxWidth: number;
}

function getTooltipPosition(anchor: HTMLElement): TooltipPosition {
  const rect = anchor.getBoundingClientRect();
  const viewportPadding = 12;
  const maxWidth = Math.min(360, window.innerWidth - viewportPadding * 2);
  const left = Math.min(
    Math.max(viewportPadding, rect.left),
    Math.max(viewportPadding, window.innerWidth - maxWidth - viewportPadding),
  );
  return { left, top: rect.bottom + 8, maxWidth };
}

/** 锚点是否已完全离开视口（被滚走/被遮挡后给一个兜底隐藏判定）。 */
function isAnchorOutOfView(anchor: HTMLElement): boolean {
  const rect = anchor.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return true; // display:none / 卸载残留
  return rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth;
}

export const TruncatedText: React.FC<{
  value: string;
  maxLength: number;
  as?: TextTag;
  style?: React.CSSProperties;
}> = ({ value, maxLength, as = 'span', style }) => {
  const displayValue = truncateText(value, maxLength);
  const isTruncated = displayValue !== value;
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [position, setPosition] = useState<TooltipPosition | null>(null);

  useEffect(() => {
    if (!anchor) return undefined;
    const onScroll = () => {
      // 锚点随滚动/布局离开视口 → 关闭 tooltip，避免叠在其它面板上「一直显示不消失」。
      if (isAnchorOutOfView(anchor)) {
        setAnchor(null);
        setPosition(null);
        return;
      }
      setPosition(getTooltipPosition(anchor));
    };
    onScroll();
    window.addEventListener('resize', onScroll);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('resize', onScroll);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [anchor]);

  const showTooltip = (event: React.MouseEvent<HTMLElement> | React.FocusEvent<HTMLElement>) => {
    if (!isTruncated) return;
    const nextAnchor = event.currentTarget;
    setAnchor(nextAnchor);
    setPosition(getTooltipPosition(nextAnchor));
  };
  const hideTooltip = () => {
    setAnchor(null);
    setPosition(null);
  };
  const Wrapper = as === 'span' ? 'span' : 'div';
  // 点击（mousedown）即「要触发动作」（节点卡片点击会下钻/打开侧栏且不卸载节点列表、也不产生 mouseleave），
  // 此刻应立即收起 tooltip，避免 portal 残留在 document.body 上盖住新面板。
  const Text = React.createElement(
    as,
    {
      style,
      tabIndex: isTruncated ? 0 : undefined,
      onFocus: showTooltip,
      onBlur: hideTooltip,
    },
    displayValue,
  );

  return (
    <Wrapper
      onMouseEnter={showTooltip}
      onMouseLeave={hideTooltip}
      onMouseDown={hideTooltip}
      style={{ display: as === 'span' ? 'inline-block' : 'block', minWidth: 0, maxWidth: '100%' }}
    >
      {Text}
      {isTruncated &&
        position &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            role="tooltip"
            style={{
              position: 'fixed',
              left: position.left,
              top: position.top,
              zIndex: 10000,
              maxWidth: position.maxWidth,
              padding: '7px 9px',
              border: `1px solid ${C.border}`,
              borderRadius: 6,
              background: C.textPrimary,
              color: '#fff',
              boxShadow: '0 6px 18px rgba(29, 33, 41, 0.18)',
              fontSize: 11,
              fontWeight: 400,
              lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              pointerEvents: 'none',
            }}
          >
            {value}
          </div>,
          document.body,
        )}
    </Wrapper>
  );
};
