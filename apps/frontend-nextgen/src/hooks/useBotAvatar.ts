import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { useEffect, useState } from 'react';
/** 编辑态头像：拉取当前值供编辑器展示；保存由宿主（基础信息弹窗）统一落库，hook 不再直接保存。 */
export function useBotAvatar(botId: string) {
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    void botAvatarService
      .get(botId)
      .then((v) => {
        if (active) setValue(v);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : '头像加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [botId]);
  return { value, setValue, loading, error };
}
