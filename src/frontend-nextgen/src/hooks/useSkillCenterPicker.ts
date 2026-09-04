import type { BotEditorSkill } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useEffect, useRef, useState } from 'react';

/** 弹窗独立分页状态；不与能力市场页面共享搜索或选中态。 */
export function useSkillCenterPicker(enabled: boolean, keyword: string) {
  const [items, setItems] = useState<BotEditorSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  const page = useRef(0);
  const busy = useRef(false);

  useEffect(() => {
    const seq = ++sequence.current;
    page.current = 0;
    busy.current = true;
    setItems([]);
    setError('');
    setHasMore(false);
    setLoading(enabled);
    if (!enabled) return;
    const timer = setTimeout(async () => {
      try {
        const result = await botEditorService.searchSkillCenterSkills(keyword.trim(), 1);
        if (seq !== sequence.current) return;
        setItems(result.items);
        setHasMore(result.hasMore);
        page.current = 1;
      } catch (err) {
        if (seq === sequence.current) setError(err instanceof Error ? err.message : '加载失败');
      } finally {
        if (seq === sequence.current) {
          busy.current = false;
          setLoading(false);
        }
      }
    }, 300);
    return () => {
      clearTimeout(timer);
      sequence.current += 1;
    };
  }, [enabled, keyword, revision]);

  const loadMore = async () => {
    if (!enabled || busy.current || !hasMore) return;
    const seq = sequence.current;
    const next = page.current + 1;
    busy.current = true;
    setLoading(true);
    setError('');
    try {
      const result = await botEditorService.searchSkillCenterSkills(keyword.trim(), next);
      if (seq !== sequence.current) return;
      setItems((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [
          ...current,
          ...result.items.filter((item) => {
            if (seen.has(item.id)) return false;
            seen.add(item.id);
            return true;
          }),
        ];
      });
      setHasMore(result.hasMore);
      page.current = next;
    } catch (err) {
      if (seq === sequence.current) setError(err instanceof Error ? err.message : '加载更多失败');
    } finally {
      if (seq === sequence.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return {
    items,
    loading,
    error,
    hasMore,
    loadMore,
    retry: () => {
      if (page.current > 0) void loadMore();
      else setRevision((value) => value + 1);
    },
  };
}
