import {
  addMarketFavorite,
  cancelMarketFavorite,
  listMarketFavorites,
  type MarketFavoriteItem,
} from '@/services/backend-api/MarketFavoriteController';
import { useUserStore } from '@/stores/userStore';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useMarketFavorites(spaceId: number | null) {
  const userId = useUserStore((state) => state.userId);
  const [items, setItems] = useState<MarketFavoriteItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!spaceId || !userId) return;
    setIsLoading(true);
    setError(null);
    try {
      const response = await listMarketFavorites(spaceId, userId);
      setItems(response.data.items);
    } catch (cause) {
      console.error('[MarketFavorites] 加载收藏失败', cause);
      setError('收藏加载失败，请稍后重试');
    } finally {
      setIsLoading(false);
    }
  }, [spaceId, userId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const add = useCallback(
    async (targetCode: string) => {
      if (!spaceId || !userId) return;
      setIsMutating(true);
      try {
        await addMarketFavorite(spaceId, userId, {
          target_type: 'SKILL',
          target_code: targetCode,
        });
        toast.success('已收藏');
        await reload();
      } catch (cause) {
        console.error('[MarketFavorites] 收藏失败', cause);
        toast.error('收藏失败，请检查权限或稍后重试');
      } finally {
        setIsMutating(false);
      }
    },
    [reload, spaceId, userId],
  );

  const cancel = useCallback(
    async (targetCode: string) => {
      if (!spaceId || !userId) return;
      setIsMutating(true);
      try {
        await cancelMarketFavorite(spaceId, userId, {
          target_type: 'SKILL',
          target_code: targetCode,
        });
        toast.success('已取消收藏');
        await reload();
      } catch (cause) {
        console.error('[MarketFavorites] 取消收藏失败', cause);
        toast.error('取消收藏失败，请稍后重试');
      } finally {
        setIsMutating(false);
      }
    },
    [reload, spaceId, userId],
  );

  return { items, isLoading, isMutating, error, reload, add, cancel };
}
