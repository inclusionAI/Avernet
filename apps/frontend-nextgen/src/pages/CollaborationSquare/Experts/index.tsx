import ExpertCard, { ExpertCardSkeleton } from '@/components/CollaborationSquare/ExpertCard';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useExpertCatalogFeed, type ExpertCatalogFeedQuery } from '@/hooks/useExpertCatalog';
import { useExpertCategories } from '@/hooks/useExpertCategories';
import { history, useLocation } from '@umijs/max';
import { GraduationCap, RefreshCw, Search, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { CompositionEvent, UIEvent } from 'react';
import { ExpertDetails } from './ExpertDetails';

const PAGE_SIZE = 20;
/** 触底预取距离：与协作广场 Shell（`SquarePageShell`）同一滚动加载节奏。 */
const LOAD_MORE_PRELOAD_DISTANCE = 420;
/** 专家卡栅格：16px 间距，与公开 Bot Tab 同列数（专家卡内容较厚，3 列节奏与 Bot 卡一致）。 */
const EXPERT_GRID = 'grid gap-4 md:grid-cols-2 xl:grid-cols-3';
/** 页头内下拉（dmore index.html Frame 2）：32px 高、8px 圆角描边框。 */
const SELECT_CLASS = 'h-8 shrink-0 rounded-lg border border-input bg-background px-3 text-[13px] text-foreground';

function readQuery(search: string): ExpertCatalogFeedQuery {
  const params = new URLSearchParams(search);
  return {
    keyword: (params.get('keyword') || '').slice(0, 200).trim(),
    category: params.get('category') || '',
    sort: params.get('sort') === 'newest' ? 'newest' : 'hot',
    page_size: PAGE_SIZE,
  };
}

/**
 * 页头搜索输入（dmore Frame 2 32px 描边框式）：与公开 Bot/协作群 Tab 的页头搜索同规格。
 * IME composition 事件透传给页面，由页面跳过输入法中间态。
 */
function ExpertSearchInput({
  value,
  onChange,
  onCompositionStart,
  onCompositionEnd,
}: {
  value: string;
  onChange: (value: string) => void;
  onCompositionStart: () => void;
  onCompositionEnd: (event: CompositionEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="relative flex h-8 w-full items-center rounded-lg border border-input bg-background pr-1 md:w-[248px] md:shrink-0 xl:w-[348px]">
      <Search aria-hidden className="ml-2.5 h-4.5 w-4.5 shrink-0 text-muted-foreground" />
      <Input
        aria-label="搜索专家"
        placeholder="搜索专家"
        maxLength={200}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        className="h-8 min-w-0 flex-1 border-0 bg-transparent pl-2 pr-8 text-sm shadow-none focus-visible:border-transparent focus-visible:ring-0 focus-visible:ring-offset-0"
      />
      {value && (
        <IconButton
          label="清除搜索"
          icon={<X aria-hidden className="h-4 w-4" />}
          onClick={() => onChange('')}
          className="absolute right-0 top-0"
        />
      )}
    </div>
  );
}

function ExpertLoadingState() {
  return (
    <>
      {/* 读屏通道：骨架网格本身无文字，用 sr-only status 告知加载中。 */}
      <p role="status" className="m-0 sr-only">
        正在加载专家…
      </p>
      <div className={EXPERT_GRID}>
        {[0, 1, 2, 3, 4, 5].map((item) => (
          <ExpertCardSkeleton key={item} />
        ))}
      </div>
    </>
  );
}

export default function ExpertMarketPage() {
  const location = useLocation();
  const query = readQuery(location.search);
  const expertId = new URLSearchParams(location.search).get('expert_id');
  const feed = useExpertCatalogFeed(query);
  const [keyword, setKeyword] = useState(query.keyword);
  const [composing, setComposing] = useState(false);
  const { categories, error: categoryError, reload: reloadCategories } = useExpertCategories();
  const canLoadMore = feed.hasMore && !feed.loading && !feed.loadingMore && !feed.error;

  // 滚动触底预取（与 SquarePageShell 同款）：距底部 420px 内即追加下一页。
  const handleScroll = (event: UIEvent<HTMLElement>) => {
    const target = event.currentTarget;
    const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;
    if (canLoadMore && distanceToBottom <= LOAD_MORE_PRELOAD_DISTANCE) feed.loadMore();
  };

  useEffect(() => setKeyword(query.keyword), [query.keyword]);
  useEffect(() => {
    if (composing || keyword.trim() === query.keyword) return;
    const timer = setTimeout(() => {
      const params = new URLSearchParams(location.search);
      params.set('keyword', keyword.trim());
      params.delete('page_num');
      history.replace(`${location.pathname}?${params}`);
    }, 300);
    return () => clearTimeout(timer);
  }, [keyword, composing, query.keyword, location.pathname, location.search]);


  // 页码已不进 URL：滚动加载位置是即时状态，筛选变化由 feed 自身回到第 1 页。
  const changeQuery = (patch: Record<string, string>) => {
    const params = new URLSearchParams(location.search);
    Object.entries(patch).forEach(([key, value]) => params.set(key, value));
    params.delete('page_num');
    history.push(`${location.pathname}?${params}`);
  };
  const detailLink = (id: string) => {
    const params = new URLSearchParams(location.search);
    params.set('expert_id', id);
    params.delete('page_num');
    return `${location.pathname}?${params}`;
  };
  const returnToCatalog = () => {
    const params = new URLSearchParams(location.search);
    params.delete('expert_id');
    params.delete('page_num');
    history.push(`${location.pathname}?${params}`);
  };

  // dmore Frame 2：搜索/分类/排序上移页头右侧工具区（与公开 Bot/协作群 Tab 同位），
  // 内容区不再出现工具栏；分类/排序宽度按 dmore 页头件高度（32px）收敛。
  const headerActions = (
    <>
      <ExpertSearchInput
        value={keyword}
        onChange={setKeyword}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={(event) => {
          setKeyword(event.currentTarget.value);
          setComposing(false);
        }}
      />
      <Select
        value={query.category ? `category:${query.category}` : 'all'}
        onValueChange={(value) => changeQuery({ category: value === 'all' ? '' : value.slice('category:'.length) })}
      >
        <SelectTrigger aria-label="专家分类" className={`${SELECT_CLASS} w-[140px]`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部分类</SelectItem>
          {query.category && !categories.includes(query.category) && <SelectItem value={`category:${query.category}`}>{query.category}</SelectItem>}
          {categories.map((category) => <SelectItem key={category} value={`category:${category}`}>{category}</SelectItem>)}
        </SelectContent>
      </Select>
      <Select value={query.sort} onValueChange={(value) => changeQuery({ sort: value })}>
        <SelectTrigger aria-label="专家排序" className={`${SELECT_CLASS} w-[104px]`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent><SelectItem value="hot">热门</SelectItem><SelectItem value="newest">最新</SelectItem></SelectContent>
      </Select>
    </>
  );

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 页头工具区仅目录态透传；详情态回到列表时保留原筛选参数。 */}
      <SquareNavigation active="expert" actions={expertId ? undefined : headerActions} />
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        {/* 与公开 Bot/协作群 Tab 相同的内容区节奏：px-5 / pt-4 / pb-6、区块 16px 间距。 */}
        <div className="flex w-full flex-col px-5 pb-6 pt-4">
          <section className="space-y-4" aria-label="专家">
            {expertId ? <ExpertDetails expertId={expertId} onBack={returnToCatalog} /> : <>
              {categoryError && (
                <div role="alert" className="m-0 flex items-center gap-2 text-sm text-destructive">
                  分类加载失败：{categoryError}
                  <Button variant="outline" size="sm" onClick={reloadCategories}>重试分类</Button>
                </div>
              )}
              {feed.loading && <ExpertLoadingState />}
              {!feed.loading && feed.error && (
                <Card role="alert">
                  <Empty
                    title="专家目录加载失败"
                    description={feed.error}
                    action={
                      <Button onClick={feed.reload} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                        重新加载
                      </Button>
                    }
                  />
                </Card>
              )}
              {!feed.loading && !feed.error && feed.items.length === 0 && (
                <Card>
                  <Empty
                    title="没有找到匹配的专家"
                    description="尝试更换关键词或分类。"
                    icon={<GraduationCap aria-hidden className="h-5 w-5" />}
                  />
                </Card>
              )}
              {!feed.loading && !feed.error && feed.items.length > 0 && (
                <div className={EXPERT_GRID}>
                  {feed.items.map((expert) => (
                    <ExpertCard key={expert.expert_id} expert={expert} detailHref={detailLink(expert.expert_id)} />
                  ))}
                </div>
              )}
              {!feed.loading && !feed.error && feed.loadingMore && (
                <div aria-live="polite" className="text-center text-xs text-muted-foreground">
                  正在加载更多...
                </div>
              )}
              {!feed.loading && !feed.error && feed.loadMoreError && (
                <Card role="alert" className="flex items-center justify-between gap-3 p-4">
                  <p className="m-0 text-sm text-muted-foreground">{feed.loadMoreError}</p>
                  <Button variant="secondary" size="sm" onClick={feed.loadMore}>
                    重试
                  </Button>
                </Card>
              )}
            </>}
          </section>
        </div>
      </main>
    </div>
  );
}
