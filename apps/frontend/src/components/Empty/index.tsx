/**
 * Empty - 统一空状态组件
 *
 * 用法：
 * <Empty title="暂无数据" />
 * <Empty icon={<Bot />} title="暂无 Bot" description="点击下方按钮创建" action={<Button>新建</Button>} />
 * <Empty size="sm" title="暂无结果" />
 */

import { cn } from '@/utils/utils';
import { Inbox } from 'lucide-react';
import React from 'react';

interface EmptyProps {
  /** 自定义图标，不传则使用默认 Inbox */
  icon?: React.ReactNode;
  title?: string;
  description?: string;
  /** 底部操作区（按钮等） */
  action?: React.ReactNode;
  /** sm: 侧边栏/紧凑场景；md: 默认；lg: 全页空状态 */
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE = {
  sm: {
    wrap: 'py-6 gap-2',
    iconBox: 'w-8 h-8 rounded-lg',
    iconSize: 'w-4 h-4',
    title: 'text-sm text-slate-500',
    desc: 'text-xs text-slate-400',
  },
  md: {
    wrap: 'py-10 gap-3',
    iconBox: 'w-12 h-12 rounded-xl',
    iconSize: 'w-5 h-5',
    title: 'text-sm font-medium text-slate-600',
    desc: 'text-xs text-slate-400',
  },
  lg: {
    wrap: 'py-16 gap-4',
    iconBox: 'w-16 h-16 rounded-2xl',
    iconSize: 'w-7 h-7',
    title: 'text-base font-medium text-slate-700',
    desc: 'text-sm text-slate-400',
  },
};

export const Empty: React.FC<EmptyProps> = ({
  icon,
  title = '暂无数据',
  description,
  action,
  size = 'md',
  className,
}) => {
  const s = SIZE[size];

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        s.wrap,
        className,
      )}
    >
      <div
        className={cn(
          'flex items-center justify-center bg-slate-100 flex-shrink-0',
          s.iconBox,
        )}
      >
        {icon ? (
          <span className={cn('text-slate-400 flex items-center justify-center', s.iconSize)}>{icon}</span>
        ) : (
          <Inbox className={cn('text-slate-400', s.iconSize)} />
        )}
      </div>

      <p className={cn('mb-0', s.title)}>{title}</p>

      {description && (
        <p className={cn('mb-0 max-w-xs', s.desc)}>{description}</p>
      )}

      {action && <div className="mt-1">{action}</div>}
    </div>
  );
};

export default Empty;
