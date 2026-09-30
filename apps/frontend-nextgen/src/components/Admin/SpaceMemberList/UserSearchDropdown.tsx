// 员工搜索下拉：按花名/工号/邮箱搜索 Antbu 员工目录，选中回调 SearchedUser。
//
// 对齐 open-claw UserSearchDropdown：防抖 300ms、最少 2 字触发、点击外部关闭、
// 已添加成员禁用并标「已添加」、选中后清空并关闭。
// Open Core / 不支持员工目录环境（useUserSearch.supported=false）→ 降级为手填工号 Input，
// 回车提交合成 {userId, displayName: userId}，保证添加成员弹窗始终可用。
// 仅消费 useUserSearch hook 与 @/components/ui 白名单，不直接调 service/api（import-boundaries 门禁）。
import type { SearchedUser } from '@/capabilities';
import { Button, CaptionText, Input, ValueText } from '@/components/ui';
import { Card } from '@/components/ui/Card';
import { useUserSearch } from '@/hooks/useUserSearch';
import { cn } from '@/utils/cn';
import { Loader2, Search, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface UserSearchDropdownProps {
  /** 已添加（禁用选择）的 userId 集合，下拉中显示「已添加」且不可点击 */
  disabledUserIds?: Set<string> | string[];
  /** 选中员工回调（搜索态传完整 SearchedUser；手填降级态合成 {userId, displayName: userId}） */
  onSelect: (user: SearchedUser) => void;
  /** 输入框 placeholder（仅搜索态；降级态固定「请输入用户 ID」） */
  placeholder?: string;
  /** 整体禁用 */
  disabled?: boolean;
  className?: string;
}

interface DropdownItem extends SearchedUser {
  disabled?: boolean;
}

export function UserSearchDropdown({
  disabledUserIds,
  onSelect,
  placeholder = '搜索花名、工号或邮箱',
  disabled = false,
  className,
}: UserSearchDropdownProps) {
  const [keyword, setKeyword] = useState('');
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { results, loading, supported } = useUserSearch(keyword);

  const disabledSet = useMemo(() => {
    if (!disabledUserIds) return new Set<string>();
    return disabledUserIds instanceof Set ? disabledUserIds : new Set(disabledUserIds);
  }, [disabledUserIds]);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const items: DropdownItem[] = useMemo(
    () => results.map((u) => ({ ...u, disabled: disabledSet.has(u.userId) })),
    [results, disabledSet],
  );

  const handleSelect = useCallback(
    (user: DropdownItem) => {
      if (user.disabled) return;
      onSelect(user);
      setKeyword('');
      setOpen(false);
    },
    [onSelect],
  );

  // 不支持员工目录 → 降级手填工号：Enter 提交合成 SearchedUser
  const handleManualEnter = useCallback(() => {
    const id = keyword.trim();
    if (!id) return;
    onSelect({ userId: id, displayName: id });
    setKeyword('');
  }, [keyword, onSelect]);

  const showClear = supported && keyword.length > 0;

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <div className="relative">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          placeholder={supported ? placeholder : '请输入用户 ID'}
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            if (supported) setOpen(true);
          }}
          onFocus={() => {
            if (supported && keyword.trim().length >= 2) setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              if (supported) {
                const first = items.find((i) => !i.disabled);
                if (first) handleSelect(first);
              } else {
                handleManualEnter();
              }
            } else if (e.key === 'Escape') {
              setOpen(false);
            }
          }}
          disabled={disabled}
          className={cn('pl-8', showClear && 'pr-7')}
          aria-label={supported ? '搜索员工' : '用户 ID'}
        />
        {showClear && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="清空"
            className="absolute right-0.5 top-1/2 h-6 w-6 -translate-y-1/2 text-muted-foreground"
            onClick={() => {
              setKeyword('');
              setOpen(false);
            }}
          >
            <X size={14} />
          </Button>
        )}
      </div>

      {supported && open && (
        <Card className="absolute left-0 right-0 top-full z-50 mt-1 max-h-60 overflow-hidden p-0 shadow-lg">
          {loading ? (
            <CaptionText className="flex items-center justify-center gap-2 px-3 py-4">
              <Loader2 size={16} className="animate-spin text-primary" />
              搜索中...
            </CaptionText>
          ) : items.length === 0 ? (
            <CaptionText className="px-3 py-4 text-center">
              {keyword.trim().length < 2 ? '输入至少 2 个字符搜索' : '未找到匹配的用户'}
            </CaptionText>
          ) : (
            <ul className="m-0 list-none p-0">
              {items.map((u) => {
                const isDisabled = !!u.disabled;
                const initial = (u.nickName || u.realName || u.userId || '?').charAt(0).toUpperCase();
                // 花名非空展示花名，花名为空展示真名，二者皆空退化为 userId
                const primaryText = u.nickName
                  ? `${u.nickName}(${u.userId})`
                  : u.realName
                  ? `${u.realName}(${u.userId})`
                  : u.userId;
                return (
                  <li key={u.userId}>
                    <Button
                      variant="ghost"
                      disabled={isDisabled}
                      className={cn(
                        'h-auto w-full justify-start rounded-none px-3 py-2 font-normal',
                        isDisabled ? 'cursor-not-allowed opacity-60' : 'hover:bg-muted/60',
                      )}
                      onClick={() => handleSelect(u)}
                    >
                      <span
                        className={cn(
                          'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-medium text-primary-foreground',
                          isDisabled ? 'bg-muted-foreground/40' : 'bg-primary',
                        )}
                      >
                        {initial}
                      </span>
                      <span className="min-w-0 flex-1">
                        <ValueText as="span" className="block truncate">
                          {primaryText}
                        </ValueText>
                        <CaptionText as="span" className="block truncate">
                          {u.email || u.userId}
                        </CaptionText>
                      </span>
                      {isDisabled && (
                        <CaptionText as="span" className="shrink-0">
                          已添加
                        </CaptionText>
                      )}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

export default UserSearchDropdown;
