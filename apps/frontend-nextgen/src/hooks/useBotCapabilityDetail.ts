import {
  botCapabilityDetailService,
  type CapabilityDetail,
  type CapabilityDetailTarget,
} from '@/services/botWorkshop/botCapabilityDetailService';
import { useEffect, useState } from 'react';

export function useBotCapabilityDetail(botId: string | undefined, target?: CapabilityDetailTarget, ownerId?: string) {
  const [detail, setDetail] = useState<CapabilityDetail>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setDetail(undefined);
    setError(undefined);
    if (!target || !botId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void botCapabilityDetailService
      .load(botId, target, ownerId)
      .then((value) => {
        if (current) setDetail(value);
      })
      .catch((reason) => {
        if (current) setError(reason instanceof Error ? reason.message : '能力详情加载失败');
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [botId, target, ownerId, revision]);
  return { detail, loading, error, retry: () => setRevision((value) => value + 1) };
}
