import { BackendRequestError, setCodefuseToken } from '@/services/botWorkshop/agentCodingLegacyService';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

export function useCodefuseToken(botId?: string) {
  const [isSaving, setIsSaving] = useState(false);
  const saveToken = useCallback(
    async (token: string): Promise<{ ok: boolean; message?: string }> => {
      const value = token.trim();
      if (!botId) {
        toast.error('未获取到 Bot 信息，无法保存');
        return { ok: false, message: '未获取到 Bot 信息，无法保存' };
      }
      if (!value) {
        toast.error('请先填写 CodeFuse 授权码');
        return { ok: false, message: '请先填写 CodeFuse 授权码' };
      }
      setIsSaving(true);
      try {
        const response = await setCodefuseToken(botId, value);
        if (response.success === false) {
          const message = response.message || 'CodeFuse 授权失败';
          toast.error(message);
          return { ok: false, message };
        }
        toast.success('CodeFuse 授权成功');
        return { ok: true };
      } catch (error) {
        const message = error instanceof BackendRequestError ? error.message : 'CodeFuse 授权失败';
        toast.error(message);
        return { ok: false, message };
      } finally {
        setIsSaving(false);
      }
    },
    [botId],
  );
  return { isSaving, saveToken };
}
