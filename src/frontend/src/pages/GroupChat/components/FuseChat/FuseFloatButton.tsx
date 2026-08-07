/**
 * FuseFloatButton - 智能问答悬浮按钮
 *
 * 固定在右下角（水平），垂直方向可拖动
 * 收缩时只显示 icon，hover 展开显示 icon + 文字
 * 当前 session 有未读回答时显示未读红点
 */

import { cn } from '@/utils/utils';
import { Brain } from 'lucide-react';
import { motion } from 'motion/react';
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useFuseStore } from '@/stores/groupchat/fuseStore';

interface FuseFloatButtonProps {
  onClick: () => void;
  /** 当前活跃会话 ID，用于按 session 判断未读 */
  sessionId?: string | null;
}

const COLLAPSED_WIDTH = 40;
const EXPANDED_WIDTH = 120;
const RIGHT_OFFSET = 24;

const FuseFloatButton: React.FC<FuseFloatButtonProps> = ({
  onClick,
  sessionId,
}) => {
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const hasUnread = useMemo(
    () => (sessionId ? !!unreadSessionIds[sessionId] : false),
    [unreadSessionIds, sessionId],
  );
  const [isHovered, setIsHovered] = useState(false);
  const [bottomOffset, setBottomOffset] = useState(200);

  const dragRef = useRef<{
    startMouseY: number;
    startBottom: number;
    moved: boolean;
  } | null>(null);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      dragRef.current = {
        startMouseY: e.clientY,
        startBottom: bottomOffset,
        moved: false,
      };
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [bottomOffset],
  );

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;

    const dy = drag.startMouseY - e.clientY; // 向上拖动时 dy > 0
    const newBottom = drag.startBottom + dy;

    if (!drag.moved && Math.abs(dy) > 4) {
      drag.moved = true;
    }

    if (drag.moved) {
      // 限制范围：20px 到 window.innerHeight - 60px
      setBottomOffset(
        Math.max(20, Math.min(newBottom, window.innerHeight - 60)),
      );
    }
  }, []);

  const handlePointerUp = useCallback(() => {
    const drag = dragRef.current;
    if (drag && !drag.moved) {
      onClick();
    }
    dragRef.current = null;
  }, [onClick]);

  return (
    <div
      className="fixed z-50"
      style={{
        right: RIGHT_OFFSET,
        bottom: bottomOffset,
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <motion.button
        type="button"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        initial={false}
        animate={{
          width: isHovered ? EXPANDED_WIDTH : COLLAPSED_WIDTH,
        }}
        transition={{
          type: 'spring',
          stiffness: 400,
          damping: 28,
          mass: 0.8,
        }}
        className={cn(
          'h-10 flex items-center overflow-hidden rounded-full',
          'bg-white hover:bg-slate-50',
          'text-slate-700 shadow-md hover:shadow-lg',
          'border border-slate-200/80',
          'focus:outline-none focus:ring-2 focus:ring-blue-200 focus:ring-offset-1',
          'cursor-grab active:cursor-grabbing',
          'touch-none select-none',
        )}
      >
        <span className="flex-shrink-0 flex items-center justify-center w-7 h-7 ml-1.5 rounded-full bg-blue-50">
          <Brain size={16} className="text-blue-500" />
        </span>

        <span
          className={cn(
            'flex-shrink-0 whitespace-nowrap text-sm font-medium pl-2 pr-3 text-slate-700',
            'transition-opacity duration-150',
            isHovered ? 'opacity-100' : 'opacity-0',
          )}
        >
          融合模式
        </span>

        {hasUnread && (
          <span
            className={cn(
              'absolute top-0.5 right-0.5 w-2.5 h-2.5 bg-red-500 rounded-full',
              'ring-2 ring-white pointer-events-none',
            )}
          />
        )}
      </motion.button>
    </div>
  );
};

export default FuseFloatButton;
