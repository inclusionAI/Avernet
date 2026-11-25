import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import type { LeaseCommandResult, SpeakingLease } from '../utils/speakingLease';
import type { ProviderInfo } from './useGroupChatProviders';

export type SpeakingLeaseStatus = 'unheld' | 'held' | 'occupied' | 'error';

export interface UseGroupSpeakingLeaseResult {
  speakingLease: SpeakingLease | undefined;
  speakingLeaseStatus: SpeakingLeaseStatus;
  isUpdatingSpeakingLease: boolean;
  leaseNowMs: number;
  acquireSpeakingLease: (options?: { preempt?: boolean }) => Promise<boolean>;
  releaseSpeakingLease: (lease: SpeakingLease) => Promise<void>;
  refreshSpeakingLease: () => Promise<void>;
}

interface UseGroupSpeakingLeaseConfig {
  providerInfo: ProviderInfo | undefined;
  groupId: string | number | undefined;
  humanActorId: string | undefined;
}

const LEASE_REFRESH_LEAD_MS = 5_000;
const LEASE_POLL_INTERVAL_MS = 10_000;

function leaseHeldByCurrentUser(
  result: LeaseCommandResult,
  humanActorId: string | undefined,
): boolean {
  if (!humanActorId || !result.lease) return false;
  return result.lease.holderActorId === humanActorId;
}

export function useGroupSpeakingLease({
  providerInfo,
  groupId,
  humanActorId,
}: UseGroupSpeakingLeaseConfig): UseGroupSpeakingLeaseResult {
  const normalizedGroupId = groupId ? String(groupId) : undefined;
  const [speakingLease, setSpeakingLease] = useState<SpeakingLease>();
  const [speakingLeaseStatus, setSpeakingLeaseStatus] =
    useState<SpeakingLeaseStatus>('unheld');
  const [isUpdatingSpeakingLease, setIsUpdatingSpeakingLease] = useState(false);
  const [leaseNowMs, setLeaseNowMs] = useState(() => Date.now());

  const clearLease = useCallback((status: SpeakingLeaseStatus) => {
    setSpeakingLease(undefined);
    setSpeakingLeaseStatus(status);
  }, []);

  const applyLeaseResult = useCallback(
    (result: LeaseCommandResult): boolean => {
      if (result.status === 'occupied') {
        clearLease('occupied');
        return false;
      }
      const lease = result.lease;
      if (lease && leaseHeldByCurrentUser(result, humanActorId)) {
        if (providerInfo) providerInfo.leaseRef.current = lease;
        setSpeakingLease(lease);
        setSpeakingLeaseStatus('held');
        setLeaseNowMs(Date.now());
        return true;
      }
      if (result.status === 'none' || result.status === 'released' || !lease) {
        if (providerInfo) providerInfo.leaseRef.current = undefined;
        clearLease('unheld');
        return false;
      }
      if (providerInfo) providerInfo.leaseRef.current = undefined;
      clearLease('occupied');
      return false;
    },
    [clearLease, humanActorId, providerInfo],
  );

  const refreshSpeakingLease = useCallback(async () => {
    if (!providerInfo?.leaseClient || !normalizedGroupId) return;
    try {
      const result = await providerInfo.leaseClient.current(normalizedGroupId);
      applyLeaseResult(result);
    } catch (error) {
      if (providerInfo) providerInfo.leaseRef.current = undefined;
      clearLease('error');
      console.error('[useGroupSpeakingLease] refresh failed:', error);
    }
  }, [applyLeaseResult, clearLease, normalizedGroupId, providerInfo]);

  useEffect(() => {
    if (!providerInfo?.isConnected || !normalizedGroupId) return;
    void refreshSpeakingLease();
  }, [
    normalizedGroupId,
    providerInfo,
    providerInfo?.isConnected,
    refreshSpeakingLease,
  ]);

  // Lease preemption currently has no WS event. Poll only while a lease exists
  // so an occupied/held UI eventually converges without a client heartbeat.
  useEffect(() => {
    if (
      !providerInfo?.isConnected ||
      !normalizedGroupId ||
      (speakingLeaseStatus !== 'held' && speakingLeaseStatus !== 'occupied')
    ) {
      return;
    }
    const timer = setInterval(() => {
      void refreshSpeakingLease();
    }, LEASE_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [
    normalizedGroupId,
    providerInfo,
    providerInfo?.isConnected,
    refreshSpeakingLease,
    speakingLeaseStatus,
  ]);

  const acquireSpeakingLease = useCallback(
    async (options: { preempt?: boolean } = {}) => {
      if (!providerInfo?.leaseClient || !normalizedGroupId) return false;
      setIsUpdatingSpeakingLease(true);
      try {
        const result = options.preempt
          ? await providerInfo.leaseClient.preempt(normalizedGroupId)
          : await providerInfo.leaseClient.acquire(normalizedGroupId);
        const acquired = applyLeaseResult(result);
        if (!acquired) {
          toast.error(
            options.preempt
              ? '抢占发言权失败，请稍后重试'
              : '发言权被其他成员占用',
          );
        }
        return acquired;
      } catch (error) {
        clearLease('error');
        console.error('[useGroupSpeakingLease] acquire failed:', error);
        toast.error('获取发言权失败，请稍后重试');
        return false;
      } finally {
        setIsUpdatingSpeakingLease(false);
      }
    },
    [applyLeaseResult, clearLease, normalizedGroupId, providerInfo],
  );

  const releaseSpeakingLease = useCallback(
    async (lease: SpeakingLease) => {
      if (!providerInfo?.leaseClient || !normalizedGroupId) return;
      setIsUpdatingSpeakingLease(true);
      try {
        await providerInfo.leaseClient.release(lease, normalizedGroupId);
      } catch (error) {
        console.error('[useGroupSpeakingLease] release failed:', error);
        toast.error('释放发言权失败，请稍后重试');
        return;
      } finally {
        setIsUpdatingSpeakingLease(false);
      }
      providerInfo.leaseRef.current = undefined;
      clearLease('unheld');
    },
    [clearLease, normalizedGroupId, providerInfo],
  );

  useEffect(() => {
    if (speakingLeaseStatus !== 'held') return;
    const timer = setInterval(() => setLeaseNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [speakingLeaseStatus]);

  useEffect(() => {
    if (speakingLeaseStatus !== 'held' || !speakingLease) return;
    const expiresAtMs = speakingLease.expiresAtMs - LEASE_REFRESH_LEAD_MS;
    const delay = Math.max(0, expiresAtMs - Date.now()) + 1;
    const timer = setTimeout(() => {
      if (providerInfo) providerInfo.leaseRef.current = undefined;
      clearLease('unheld');
    }, delay);
    return () => clearTimeout(timer);
  }, [clearLease, providerInfo, speakingLease, speakingLeaseStatus]);

  return {
    speakingLease,
    speakingLeaseStatus,
    isUpdatingSpeakingLease,
    leaseNowMs,
    acquireSpeakingLease,
    releaseSpeakingLease,
    refreshSpeakingLease,
  };
}
