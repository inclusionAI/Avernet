import type { BotEditorModel } from '@/domain/botEditor';
import { useEffect, useState } from 'react';

/** 拉取模型选择列表；卸载后到达的响应直接丢弃，避免对已卸载组件 setState。 */
export function useRoutineModels(onLoadModels: () => Promise<BotEditorModel[]>) {
  const [models, setModels] = useState<BotEditorModel[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    void onLoadModels()
      .then((items) => {
        if (active) setModels(items);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : '模型列表加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onLoadModels]);
  return { models, error, loading };
}
