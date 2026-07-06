/**
 * Skeleton - 统一骨架屏组件
 *
 * 提供三种预设变体，也支持自定义组合：
 *
 * 文本行：
 * <Skeleton.Line />
 * <Skeleton.Line width="60%" />
 *
 * 列表行（头像 + 两行文字）：
 * <Skeleton.ListItem />
 * <Skeleton.ListItem count={3} />
 *
 * 卡片（图标 + 多行文字）：
 * <Skeleton.Card />
 *
 * 原子块（自定义）：
 * <Skeleton.Block className="w-10 h-10 rounded-xl" />
 */

import { cn } from '@/utils/utils';
import React from 'react';

// ─── 原子块 ──────────────────────────────────────────────────────────────────

interface BlockProps {
  className?: string;
}

const Block: React.FC<BlockProps> = ({ className }) => (
  <div className={cn('bg-slate-100 animate-pulse rounded', className)} />
);

// ─── 文本行 ──────────────────────────────────────────────────────────────────

interface LineProps {
  /** 宽度，支持任意 CSS 值或 Tailwind 宽度类 */
  width?: string;
  className?: string;
}

const Line: React.FC<LineProps> = ({ width = '100%', className }) => (
  <div
    className={cn('h-3 bg-slate-100 animate-pulse rounded-full', className)}
    style={width !== '100%' ? { width } : undefined}
  />
);

// ─── 列表行 ──────────────────────────────────────────────────────────────────

interface ListItemProps {
  /** 渲染数量 */
  count?: number;
  /** 是否显示右侧辅助文字（时间等） */
  showMeta?: boolean;
  className?: string;
}

const ListItem: React.FC<ListItemProps> = ({
  count = 1,
  showMeta = true,
  className,
}) => (
  <>
    {Array.from({ length: count }).map((_, i) => (
      <div
        key={i}
        className={cn('flex items-center gap-3 px-3 py-2.5', className)}
      >
        {/* 头像 */}
        <Block className="w-10 h-10 rounded-xl flex-shrink-0" />

        {/* 文字区 */}
        <div className="flex-1 min-w-0 flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <Line className="flex-1" width="55%" />
            {showMeta && <Line className="w-10 flex-shrink-0" />}
          </div>
          <Line width="80%" />
        </div>
      </div>
    ))}
  </>
);

// ─── 卡片 ────────────────────────────────────────────────────────────────────

interface CardProps {
  /** 文字行数 */
  lines?: number;
  className?: string;
}

const Card: React.FC<CardProps> = ({ lines = 3, className }) => (
  <div className={cn('p-4 flex flex-col gap-3', className)}>
    {/* 图标占位 */}
    <Block className="w-10 h-10 rounded-xl" />
    {/* 文字行 */}
    {Array.from({ length: lines }).map((_, i) => (
      <Line key={i} width={i === lines - 1 ? '60%' : '100%'} />
    ))}
  </div>
);

// ─── 聊天消息 ──────────────────────────────────────────────────────────────────

interface MessageProps {
  /** 消息条数，默认 4 */
  count?: number;
  className?: string;
}

const Message: React.FC<MessageProps> = ({ count = 4, className }) => (
  <div className={cn('flex flex-col gap-5 px-6 py-4', className)}>
    {Array.from({ length: count }).map((_, i) => {
      const isUser = i % 2 === 1;
      return (
        <div
          key={i}
          className={cn(
            'flex gap-2.5',
            isUser ? 'flex-row-reverse' : 'flex-row',
          )}
        >
          {/* 头像 */}
          <Block className="w-8 h-8 rounded-full flex-shrink-0 mt-0.5" />
          {/* 内容区 */}
          <div
            className={cn(
              'flex flex-col gap-1 max-w-[75%]',
              isUser ? 'items-end' : 'items-start',
            )}
          >
            {/* 名称 */}
            <Line width="32%" className="h-2.5" />
            {/* 气泡 */}
            <div
              className={cn(
                'flex flex-col gap-1.5 rounded-2xl px-3.5 py-2.5',
                isUser ? 'bg-blue-50/60' : 'bg-slate-50',
              )}
            >
              <Line width="100%" />
              <Line width={i === count - 1 ? '55%' : '80%'} />
            </div>
          </div>
        </div>
      );
    })}
  </div>
);

// ─── 导出 ─────────────────────────────────────────────────────────────────────

export const Skeleton = {
  Block,
  Line,
  ListItem,
  Card,
  Message,
};

export default Skeleton;
