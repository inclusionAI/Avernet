import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
export function useBotAvatar(botId: string) {
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
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
  return {
    value,
    setValue,
    loading,
    saving,
    error,
    save: async () => {
      setSaving(true);
      try {
        const savedUrl = await botAvatarService.save(botId, value);
        setValue(savedUrl);
        toast.success('头像已保存');
        return savedUrl;
      } catch (e) {
        toast.error(e instanceof Error ? e.message : '头像保存失败');
        return undefined;
      } finally {
        setSaving(false);
      }
    },
  };
}
