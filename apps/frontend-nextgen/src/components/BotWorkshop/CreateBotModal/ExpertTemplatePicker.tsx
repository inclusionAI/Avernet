import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { ExpertSummary } from '@/domain/expertMarket';
import { useExpertCatalog } from '@/hooks/useExpertCatalog';
import { useEffect, useState } from 'react';

export function ExpertTemplatePicker({
  selected,
  onSelect,
}: {
  selected?: ExpertSummary;
  onSelect: (expert: ExpertSummary) => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const [queryKeyword, setQueryKeyword] = useState('');
  const [composing, setComposing] = useState(false);
  useEffect(() => {
    if (composing || keyword.trim() === queryKeyword) return;
    const timer = setTimeout(() => { setQueryKeyword(keyword.trim()); setPage(1); }, 300);
    return () => clearTimeout(timer);
  }, [keyword, queryKeyword, composing]);
  const catalog = useExpertCatalog({ keyword: queryKeyword, category: '', sort: 'hot', page_num: page, page_size: 20 });
  return (
    <section aria-label="选择专家模板" className="space-y-2 rounded-lg border border-input p-3">
      <Input
        aria-label="搜索专家模板"
        placeholder="搜索专家名称、描述或标签"
        maxLength={200}
        value={keyword}
        onChange={(event) => {
          setKeyword(event.target.value);
        }}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={(event) => { setKeyword(event.currentTarget.value); setComposing(false); }}
      />
      {catalog.loading ? (
        <p role="status">正在加载专家…</p>
      ) : catalog.error ? (
        <div role="alert">
          {catalog.error}
          <Button type="button" variant="outline" onClick={catalog.reload}>
            重试专家列表
          </Button>
        </div>
      ) : (
        <>
          {!catalog.page.items.length && <p>没有找到匹配的专家</p>}
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {catalog.page.items.map((expert) => (
              <Button
                key={expert.expert_id}
                type="button"
                variant={selected?.expert_id === expert.expert_id ? 'secondary' : 'ghost'}
                aria-pressed={selected?.expert_id === expert.expert_id}
                className="w-full justify-start"
                onClick={() => onSelect(expert)}
              >
                {expert.name}
              </Button>
            ))}
          </div>
          {catalog.page.total > 20 && (
            <div className="flex items-center gap-2">
              <Button type="button" variant="outline" disabled={page === 1} onClick={() => setPage(page - 1)}>
                上一页
              </Button>
              <span>第 {page} 页</span>
              <Button
                type="button"
                variant="outline"
                disabled={page * 20 >= catalog.page.total}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
