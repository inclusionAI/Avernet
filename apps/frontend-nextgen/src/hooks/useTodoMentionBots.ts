import type { TodoBotOption } from '@/domain/todos/models';
import { loadTodoMentionBots } from '@/services/todos/todoService';
import { useEffect, useRef, useState } from 'react';

export function useTodoMentionBots(spaceId: number | undefined) {
  const requestRef = useRef(0);
  const [bots, setBots] = useState<TodoBotOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!spaceId) {
      requestRef.current += 1;
      setBots([]);
      setLoading(false);
      setError(null);
      return;
    }

    const requestId = ++requestRef.current;
    setBots([]);
    setLoading(true);
    setError(null);
    void loadTodoMentionBots(spaceId)
      .then((items) => {
        if (requestId === requestRef.current) setBots(items);
      })
      .catch((cause) => {
        if (requestId !== requestRef.current) return;
        setBots([]);
        setError(cause instanceof Error ? cause.message : '空间 Bot 加载失败');
      })
      .finally(() => {
        if (requestId === requestRef.current) setLoading(false);
      });

    return () => {
      requestRef.current += 1;
    };
  }, [spaceId]);

  return { bots, loading, error };
}
