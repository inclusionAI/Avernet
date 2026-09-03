import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { Check, Loader2, Search, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

type OrgPath = string[];

interface OrganizationScopeSearchProps {
  value: OrgPath[];
  onChange: (paths: OrgPath[]) => void;
  onSearch: (keyword: string, signal?: AbortSignal) => Promise<OrganizationSearchEntry[]>;
  /** 需要提交部门编码的调用方可同步保留已选搜索条目。 */
  selectedEntries?: OrganizationSearchEntry[];
  onEntriesChange?: (entries: OrganizationSearchEntry[]) => void;
}

const DEBOUNCE_MS = 1000;
function pathKey(path: OrgPath): string {
  return path.join('\u0000');
}

function visiblePath(path: OrgPath): string {
  return path.join(' / ');
}

export function OrganizationScopeSearch({
  value,
  onChange,
  onSearch,
  selectedEntries = [],
  onEntriesChange,
}: OrganizationScopeSearchProps) {
  const [keyword, setKeyword] = useState('');
  const [results, setResults] = useState<OrganizationSearchEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [waitingToSearch, setWaitingToSearch] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const abortRef = useRef<AbortController>();

  const selectedKeys = new Set(value.map(pathKey));

  const executeSearch = useCallback(
    async (kw: string) => {
      if (!kw.trim()) {
        setResults([]);
        setError(null);
        return;
      }
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setSearching(true);
      setError(null);
      try {
        const items = await onSearch(kw, controller.signal);
        if (!controller.signal.aborted) setResults(items);
      } catch (e) {
        if (!controller.signal.aborted) {
          setResults([]);
          setError(e instanceof Error ? e.message : '搜索失败，请重试');
        }
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    },
    [onSearch],
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, []);

  const handleInput = (value: string) => {
    setKeyword(value);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = undefined;
    }
    abortRef.current?.abort();
    abortRef.current = undefined;
    setSearching(false);
    setResults([]);
    setError(null);
    if (!value.trim()) {
      setWaitingToSearch(false);
      return;
    }
    setWaitingToSearch(true);
    debounceRef.current = setTimeout(() => {
      debounceRef.current = undefined;
      setWaitingToSearch(false);
      void executeSearch(value);
    }, DEBOUNCE_MS);
  };

  const handleClear = () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = undefined;
    }
    setKeyword('');
    setResults([]);
    setError(null);
    setWaitingToSearch(false);
    setSearching(false);
    abortRef.current?.abort();
    abortRef.current = undefined;
  };

  const handleAdd = (entry: OrganizationSearchEntry) => {
    if (selectedKeys.has(pathKey(entry.path))) return;
    onChange([...value, entry.path]);
    onEntriesChange?.([...selectedEntries, entry]);
    setKeyword('');
    setResults([]);
    setError(null);
    setWaitingToSearch(false);
  };

  const handleRemove = (path: OrgPath) => {
    onChange(value.filter((p) => pathKey(p) !== pathKey(path)));
    onEntriesChange?.(selectedEntries.filter((entry) => pathKey(entry.path) !== pathKey(path)));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && results.length === 1) {
      e.preventDefault();
      handleAdd(results[0]);
    }
  };

  const handleClearSelected = () => {
    onChange([]);
    onEntriesChange?.([]);
  };

  return (
    <div className="space-y-3">
      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5">
          <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
        </div>
        <Input
          value={keyword}
          onChange={(e) => handleInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="请输入集团、事业部、部门或团队名称（1~128 字符）"
          maxLength={128}
          aria-label="搜索组织团队范围"
          className="pl-9 pr-8"
        />
        {keyword && (
          <div className="absolute inset-y-0 right-0 flex items-center pr-1.5">
            <IconButton
              label="清空搜索"
              icon={<X className="h-4 w-4" aria-hidden />}
              size="sm"
              variant="ghost"
              onClick={handleClear}
            />
          </div>
        )}
      </div>

      {searching && (
        <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          <span>搜索中…</span>
        </div>
      )}

      {error && !searching && <p className="text-xs text-destructive">{error}</p>}

      {!waitingToSearch && !searching && !error && keyword.trim() && results.length === 0 && (
        <p className="text-xs text-muted-foreground">未找到匹配的组织团队</p>
      )}

      {!searching && !error && results.length > 0 && (
        <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border p-1">
          {results.map((entry) => {
            const key = pathKey(entry.path);
            const selected = selectedKeys.has(key);
            return (
              <Button
                key={key}
                variant="ghost"
                size="sm"
                disabled={selected}
                className="h-auto w-full justify-start whitespace-normal break-words px-3 py-2 text-left"
                onClick={() => handleAdd(entry)}
              >
                {selected && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
                <span className="text-sm font-medium text-foreground">{visiblePath(entry.path)}</span>
                {selected && <span className="text-xs text-muted-foreground">已添加</span>}
              </Button>
            );
          })}
        </div>
      )}

      <div className="border-t border-border pt-3">
        <div className="flex items-center justify-between gap-3">
          <p className="m-0 text-xs font-medium text-foreground">已选组织范围（{value.length}）</p>
          {value.length > 0 && (
            <Button variant="ghost" size="sm" onClick={handleClearSelected}>
              清空
            </Button>
          )}
        </div>
        {value.length === 0 ? (
          <p className="mb-0 mt-2 text-xs text-muted-foreground">
            可分别搜索集团、事业部、部门或团队，并连续添加多个范围。
          </p>
        ) : (
          <ul className="mb-0 mt-2 space-y-1 p-0">
            {value.map((path) => (
              <li key={pathKey(path)} className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span className="min-w-0 break-words">{visiblePath(path)}</span>
                <IconButton
                  label={`移除 ${visiblePath(path)}`}
                  icon={<X className="h-3.5 w-3.5" aria-hidden />}
                  size="sm"
                  className="h-6 w-6"
                  onClick={() => handleRemove(path)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
