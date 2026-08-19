import { Button, Empty } from '@/components';
import { useMarketFavorites } from '@/hooks/useMarketFavorites';
import { Heart } from 'lucide-react';
import { useMemo, useState } from 'react';

function readSpaceId(): number | null {
  const value = new URLSearchParams(window.location.search).get('space_id');
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export default function MarketFavoritesPage() {
  const spaceId = useMemo(readSpaceId, []);
  const [targetCode, setTargetCode] = useState('');
  const { items, isLoading, isMutating, error, reload, add, cancel } =
    useMarketFavorites(spaceId);

  if (!spaceId) {
    return (
      <Empty
        title="请选择空间"
        description="从空间工作台进入收藏页后即可查看和管理收藏。"
        size="lg"
      />
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-8">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">我的收藏</h1>
          <p className="mt-1 text-sm text-slate-500">空间 #{spaceId} 中可访问的 Skill 收藏</p>
        </div>
        <Button variant="default" soft loading={isLoading} onClick={() => void reload()}>
          刷新
        </Button>
      </header>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const code = targetCode.trim();
          if (!code) return;
          void add(code).then(() => setTargetCode(''));
        }}
      >
        <input
          aria-label="Skill 标识"
          className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-violet-400"
          maxLength={128}
          onChange={(event) => setTargetCode(event.target.value)}
          placeholder="输入已发布 Skill 的稳定标识"
          value={targetCode}
        />
        <Button disabled={!targetCode.trim()} loading={isMutating} type="submit">
          收藏
        </Button>
      </form>

      {error ? (
        <Empty
          title={error}
          description="不会更改当前收藏状态。"
          action={<Button onClick={() => void reload()}>重试</Button>}
        />
      ) : items.length === 0 && !isLoading ? (
        <Empty title="暂无收藏" description="在市场或 Skill 详情中收藏后会显示在这里。" />
      ) : (
        <section className="space-y-3" aria-label="收藏列表">
          {items.map((item) => (
            <article
              className="flex items-center justify-between rounded-xl border border-slate-200/60 bg-white px-4 py-3"
              key={item.favorite_id}
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-900">{item.target_code}</p>
                <p className="mt-1 text-xs text-slate-500">Skill · 收藏于 {new Date(item.favorite_at).toLocaleString()}</p>
              </div>
              <Button
                aria-label={`取消收藏 ${item.target_code}`}
                ghost
                leftIcon={<Heart fill="currentColor" size={15} />}
                loading={isMutating}
                onClick={() => void cancel(item.target_code)}
                variant="secondary"
              >
                已收藏
              </Button>
            </article>
          ))}
        </section>
      )}
    </main>
  );
}
