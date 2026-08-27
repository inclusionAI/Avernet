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
    const updatePosition = () => setPosition(getTooltipPosition(anchor));
    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
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
