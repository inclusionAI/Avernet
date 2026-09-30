/**
 * SearchInput 组件
 * 带搜索图标的输入框，Calm UI 风格
 */

import { cn } from '@/utils/utils';
import { Search } from 'lucide-react';
import React from 'react';

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** 右侧自定义图标 */
  rightIcon?: React.ReactNode;
  /** 输入框 keydown 事件 */
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

export const SearchInput: React.FC<SearchInputProps> = ({
  value,
  onChange,
  placeholder = '搜索...',
  className,
  rightIcon,
  onKeyDown,
}) => {
  return (
    <div className={cn('relative', className)}>
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className={cn(
          'w-full pl-8 pr-3 py-1.5 text-sm bg-white border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-lavender-400/40 focus:border-lavender-400 transition-all placeholder:text-slate-400',
          rightIcon && 'pr-8',
        )}
      />
      {rightIcon && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2">
          {rightIcon}
        </div>
      )}
    </div>
  );
};

export default SearchInput;
