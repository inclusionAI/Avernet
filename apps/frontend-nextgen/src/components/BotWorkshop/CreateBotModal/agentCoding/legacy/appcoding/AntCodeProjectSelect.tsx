import { Button } from '@/components/ui/Button';
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * AntCodeProjectSelect - AntCode 项目模糊搜索选择组件
 *
 * 用户输入时触发模糊搜索，选择后回填 URL
 */

import { Badge } from '@/components/ui/Badge';
import { searchAntCodeProjects, type AntCodeProject } from '@/services/botWorkshop/agentCodingLegacyService';
import { cn } from '@/utils/cn';
import { Loader2, Search, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface AntCodeProjectOptionProps {
  project: AntCodeProject;
  disabled?: boolean;
  onSelect: (project: AntCodeProject) => void;
}

export function sortAntCodeProjects(projects: AntCodeProject[]): AntCodeProject[] {
  return [...projects].sort((left, right) => {
    const leftRestricted = left.accessLevel === 0 ? 1 : 0;
    const rightRestricted = right.accessLevel === 0 ? 1 : 0;

    if (leftRestricted !== rightRestricted) {
      return leftRestricted - rightRestricted;
    }

    return left.name.localeCompare(right.name);
  });
}

export function AntCodeProjectOption({ project, disabled = false, onSelect }: AntCodeProjectOptionProps) {
  const isRestricted = project.accessLevel === 0;
  return (
    <Button
      type="button"
      variant="ghost"
      onClick={() => onSelect(project)}
      disabled={disabled || isRestricted}
      className={cn(
        'h-auto min-h-0 w-full flex-col items-stretch justify-start gap-0 rounded-none border-0 border-b border-slate-100 bg-transparent px-3 py-2 text-left font-normal shadow-none transition-colors last:border-b-0',
        disabled || isRestricted ? 'cursor-not-allowed bg-slate-50/80 opacity-70' : 'cursor-pointer hover:bg-slate-50',
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        <div className="min-w-0 flex-1 truncate text-sm font-medium leading-5 text-slate-700">{project.name}</div>
        {isRestricted ? (
          <Badge
            tone="neutral"
            className="pointer-events-none shrink-0 self-start whitespace-nowrap rounded-full border-amber-200/70 bg-amber-50/70 px-2 py-0 text-[10px] font-medium leading-4 text-amber-700 shadow-none"
          >
            无权限
          </Badge>
        ) : null}
      </div>
      {project.description ? <div className="mt-0.5 truncate text-xs text-slate-400">{project.description}</div> : null}
      <div className="mt-0.5 truncate font-mono text-xs text-slate-400">{project.web_url}</div>
    </Button>
  );
}

export interface AntCodeProjectSelectProps {
  /** 当前值（URL） */
  value: string;
  /** 值变化回调 */
  onChange: (url: string) => void;
  /** 禁用状态 */
  disabled?: boolean;
  /** 占位符 */
  placeholder?: string;
  /** 输入框样式类 */
  className?: string;
  /** 校验错误状态 */
  error?: boolean;
}

export function AntCodeProjectSelect({
  value,
  onChange,
  disabled = false,
  placeholder = '输入项目名称搜索...',
  className,
  error = false,
}: AntCodeProjectSelectProps) {
  // 搜索关键词
  const [query, setQuery] = useState('');
  // 搜索结果列表
  const [results, setResults] = useState<AntCodeProject[]>([]);
  // 加载状态
  const [isLoading, setIsLoading] = useState(false);
  // 下拉框显示状态
  const [isOpen, setIsOpen] = useState(false);
  // 定时器 ref，用于防抖
  const debounceRef = useRef<NodeJS.Timeout | null>(null);
  // 请求版本号 ref，用于忽略过时的响应
  const requestVersionRef = useRef(0);
  // 输入框 ref
  const inputRef = useRef<HTMLInputElement>(null);
  // 点击外部关闭下拉框
  const containerRef = useRef<HTMLDivElement>(null);

  // 同步 value 到 query：直接展示完整 URL，不再从 URL 中抽取项目名
  useEffect(() => {
    setQuery(value || '');
  }, [value]);

  // 点击外部关闭下拉框
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        // 如果输入框有内容但没有选择选项，点击外部时清空输入内容
        if (query.trim() && !value) {
          setQuery('');
          setResults([]);
        }
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [query, value]);

  // 搜索防抖
  const handleQueryChange = useCallback((newQuery: string) => {
    setQuery(newQuery);
    setIsOpen(true);

    // 清除之前的定时器
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    // 如果为空，清空结果
    if (!newQuery.trim()) {
      setResults([]);
      setIsLoading(false);
      return;
    }

    // 立即清空下拉列表并显示 loading 状态
    setResults([]);
    setIsLoading(true);

    // 设置新的定时器，300ms 后触发搜索
    debounceRef.current = setTimeout(async () => {
      // 增加请求版本号
      const currentVersion = ++requestVersionRef.current;

      try {
        const data = await searchAntCodeProjects(newQuery.trim());
        // 检查请求是否过时（忽略旧请求的响应）
        if (currentVersion !== requestVersionRef.current) {
          return;
        }
        setResults(sortAntCodeProjects(data));
      } catch (error) {
        console.error('[AntCodeProjectSelect] Search failed:', error);
        // 只有非过时请求才清空结果
        if (currentVersion === requestVersionRef.current) {
          setResults([]);
        }
        // 应用 Bot 埋点 A5：配置加载失败 - 代码仓库搜索
        // 详见 docs/架构与规范/埋点/应用Bot埋点方案.md
      } finally {
        // 只有非过时请求才更新 loading 状态
        if (currentVersion === requestVersionRef.current) {
          setIsLoading(false);
        }
      }
    }, 300);
  }, []);

  // 选择项目：回填完整 URL（输入框与外部 value 都直接展示 URL）
  const handleSelect = useCallback(
    (project: AntCodeProject) => {
      onChange(project.web_url);
      setQuery(project.web_url);
      setIsOpen(false);
      // 应用 Bot 埋点 A2：选择代码仓库
      // 详见 docs/架构与规范/埋点/应用Bot埋点方案.md
    },
    [onChange],
  );

  // 清除选择
  const handleClear = useCallback(() => {
    onChange('');
    setQuery('');
    setResults([]);
    setIsOpen(false);
    inputRef.current?.focus();
  }, [onChange]);

  // 处理输入框聚焦
  const handleFocus = useCallback(() => {
    if (query.trim() && results.length > 0) {
      setIsOpen(true);
    }
  }, [query, results.length]);

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          onFocus={handleFocus}
          placeholder={placeholder}
          disabled={disabled}
          className={cn(
            'w-full bg-background px-3 py-1.5 text-sm border rounded-lg',
            'focus:outline-none focus:ring-1 focus:border-transparent',
            'placeholder:text-slate-300',
            'disabled:bg-slate-50 disabled:text-slate-400',
            'pr-20', // 为右侧图标留出空间
            error ? 'border-red-300 focus:ring-red-400' : 'border-slate-200 focus:ring-slate-300',
          )}
        />
        {/* 右侧操作按钮区域 */}
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
          {value && !disabled && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleClear}
              className="p-0.5 text-slate-400 hover:text-red-500 rounded transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          )}
          {!value && <Search className="w-3.5 h-3.5 text-slate-400" />}
        </div>
      </div>

      {/* 下拉搜索结果 - loading 状态 */}
      {isOpen && isLoading && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-center">
          <div className="flex items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>搜索中...</span>
          </div>
        </div>
      )}

      {/* 下拉搜索结果 - 有数据 */}
      {isOpen && !isLoading && results.length > 0 && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
          {results.map((project, index) => (
            <AntCodeProjectOption key={index} project={project} disabled={disabled} onSelect={handleSelect} />
          ))}
        </div>
      )}

      {/* 无结果提示 */}
      {isOpen && query.trim() && !isLoading && results.length === 0 && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-center text-sm text-slate-400">
          未找到匹配的项目
        </div>
      )}
    </div>
  );
}

export default AntCodeProjectSelect;
