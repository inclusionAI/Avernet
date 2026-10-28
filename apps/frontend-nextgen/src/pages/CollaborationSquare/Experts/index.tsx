import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useExpertCatalog } from '@/hooks/useExpertCatalog';
import { useExpertCategories } from '@/hooks/useExpertCategories';
import type { ExpertQuery } from '@/domain/expertMarket';
import { history, Link, useLocation } from '@umijs/max';
import { useEffect, useState } from 'react';
import { ExpertDetails } from './ExpertDetails';

const PAGE_SIZE = 20;
const SELECT_CLASS = 'h-9 rounded-lg border border-input bg-background px-3 text-sm text-foreground';

function readQuery(search: string): ExpertQuery {
  const params = new URLSearchParams(search);
  const page = Number(params.get('page_num') || 1);
  return {
    keyword: (params.get('keyword') || '').slice(0, 200).trim(),
    category: params.get('category') || '',
    sort: params.get('sort') === 'newest' ? 'newest' : 'hot',
    page_num: Number.isSafeInteger(page) && page > 0 ? page : 1,
    page_size: PAGE_SIZE,
  };
}

export default function ExpertMarketPage() {
  const location = useLocation();
  const query = readQuery(location.search);
  const expertId = new URLSearchParams(location.search).get('expert_id');
  const catalog = useExpertCatalog(query);
  const [keyword, setKeyword] = useState(query.keyword);
  const [composing, setComposing] = useState(false);
  const { categories, error: categoryError, reload: reloadCategories } = useExpertCategories();

  useEffect(() => setKeyword(query.keyword), [query.keyword]);
  useEffect(() => {
    if (composing || keyword.trim() === query.keyword) return;
    const timer = setTimeout(() => {
      const params = new URLSearchParams(location.search);
      params.set('keyword', keyword.trim());
      params.set('page_num', '1');
      history.replace(`${location.pathname}?${params}`);
    }, 300);
    return () => clearTimeout(timer);
  }, [keyword, composing, query.keyword, location.pathname, location.search]);


  const changeQuery = (patch: Record<string, string>) => {
    const params = new URLSearchParams(location.search);
    Object.entries(patch).forEach(([key, value]) => params.set(key, value));
    history.push(`${location.pathname}?${params}`);
  };
  const detailLink = (id: string) => {
    const params = new URLSearchParams(location.search);
    params.set('expert_id', id);
    return `${location.pathname}?${params}`;
  };
  const returnToCatalog = () => {
    const params = new URLSearchParams(location.search);
    params.delete('expert_id');
    history.push(`${location.pathname}?${params}`);
  };

  return (
    <div className="flex h-full flex-col bg-background">
      <SquareNavigation active="expert" />
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <section className="mx-auto max-w-7xl space-y-5 px-4 py-5 sm:px-6 lg:px-8" aria-label="专家市场">
          {expertId ? <ExpertDetails expertId={expertId} onBack={returnToCatalog} /> : <>
            <header>
              <h1 className="text-xl font-semibold text-foreground">专家市场</h1>
              <p className="mt-1 text-sm text-muted-foreground">浏览专家模板，了解专业身份与技能。</p>
            </header>
            <div className="flex flex-wrap items-center gap-3">
              <Input
                className="h-9 max-w-md text-sm"
                aria-label="搜索专家"
                placeholder="搜索名称、描述或标签"
                maxLength={200}
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                onCompositionStart={() => setComposing(true)}
                onCompositionEnd={(event) => { setKeyword(event.currentTarget.value); setComposing(false); }}
              />
              <Select value={query.category ? `category:${query.category}` : 'all'}
                onValueChange={(value) => changeQuery({ category: value === 'all' ? '' : value.slice('category:'.length), page_num: '1' })}>
                <SelectTrigger aria-label="专家分类" className={SELECT_CLASS}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部分类</SelectItem>
                  {query.category && !categories.includes(query.category) && <SelectItem value={`category:${query.category}`}>{query.category}</SelectItem>}
                  {categories.map((category) => <SelectItem key={category} value={`category:${category}`}>{category}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={query.sort} onValueChange={(value) => changeQuery({ sort: value, page_num: '1' })}>
                <SelectTrigger aria-label="专家排序" className={SELECT_CLASS}><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="hot">热门</SelectItem><SelectItem value="newest">最新</SelectItem></SelectContent>
              </Select>
            </div>
            {categoryError && <div role="alert" className="text-sm text-destructive">分类加载失败：{categoryError}
              <Button className="ml-2" variant="outline" onClick={reloadCategories}>重试分类</Button>
            </div>}
            {catalog.loading ? <p role="status" className="py-12 text-center text-muted-foreground">正在加载专家…</p>
              : catalog.error ? <div role="alert" className="rounded-xl border border-destructive/30 p-6 text-center">
                <p className="mb-3 text-destructive">专家目录加载失败：{catalog.error}</p>
                <Button variant="outline" onClick={catalog.reload}>重试</Button>
              </div> : catalog.page.items.length === 0 ? <div className="rounded-xl border border-input p-12 text-center">
                <p className="font-medium text-foreground">没有找到匹配的专家</p>
                <p className="mt-2 text-sm text-muted-foreground">可以调整关键词或分类后重试。</p>
              </div> : <>
                <p className="text-sm text-muted-foreground">共 {catalog.page.total} 位专家</p>
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {catalog.page.items.map((expert) => <article key={expert.expert_id} className="flex flex-col rounded-xl border border-input bg-card p-5">
                    <span className="text-xs text-primary">{expert.category}</span>
                    <h2 className="mt-2 text-lg font-semibold text-foreground">{expert.name}</h2>
                    <p className="mt-2 flex-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{expert.description}</p>
                    <div className="mt-3 flex flex-wrap gap-1.5">{expert.tags.map((tag) => <span key={tag} className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">{tag}</span>)}</div>
                    <div className="mt-5 flex items-center justify-between gap-3">
                      <span className="text-xs text-muted-foreground">已创建 {expert.creation_count} 个 Bot</span>
                      <Button asChild variant="outline"><Link to={detailLink(expert.expert_id)}>查看 {expert.name}</Link></Button>
                    </div>
                  </article>)}
                </div>
              </>}
            {!catalog.loading && !catalog.error && (query.page_num > 1 || catalog.page.total > PAGE_SIZE) && <nav className="flex items-center justify-center gap-4" aria-label="专家分页">
              <Button variant="outline" disabled={query.page_num === 1} onClick={() => changeQuery({ page_num: String(query.page_num - 1) })}>上一页</Button>
              <span className="text-sm text-muted-foreground">第 {query.page_num} 页</span>
              <Button variant="outline" disabled={query.page_num * PAGE_SIZE >= catalog.page.total} onClick={() => changeQuery({ page_num: String(query.page_num + 1) })}>下一页</Button>
            </nav>}
          </>}
        </section>
      </main>
    </div>
  );
}
