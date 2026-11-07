/**
 * useSpeakingLease —— BCS 群聊发言权（speaking lease）编排 Hook。
 *
 * 订阅 Provider 发言权快照并集中管理提交态与错误反馈：
 * - 连接建立（connected 事件）即 lease.current 补拍一次快照（服务端权威）；
 * - held/occupied 状态下的 10s 轮询与到期自动丢弃归 service 层，本 Hook 只做展示编排；
 * - Provider 替换 / 会话切换在渲染期即时隔离旧状态；旧 Provider 的迟到结果不写
 *   新状态、不产生新 toast（防「旧会话回流」污染新页面）；
 * - leaseNowMs 为 held 状态下的 1s 步进时钟，仅驱动「剩余 N 秒」倒计时展示。
 *
 * 协议细节（帧解析、超时、状态映射）归 services/workspace；本 Hook 不接触协议帧。
 */
import type {
  SpeakingLease,
  SpeakingLeaseStatus,
  SpeakingMode,
} from '@/domain/collaboration/speakingLease';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { SpeakingLeaseSnapshot } from '@/services/workspace/groupSpeakingLeaseService';
import type { ConnectionStatusEvent } from '@tc-chat/adapters';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** Hook 消费的 Provider 结构——GroupChatProvider 结构性满足，测试可注入可控 fake。 */
export interface SpeakingLeaseProvider {
  /** 订阅发言权快照；订阅即回放当前状态。 */
  subscribeToSpeakingLease(listener: (snapshot: SpeakingLeaseSnapshot) => void): () => void;
  /** 订阅底层 WebSocket 连接状态（不回放）。 */
  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void): () => void;
  /** 获取（preempt=false）/ 抢占（preempt=true）发言权；被占用返回 false，指令失败拒绝。 */
  acquireSpeakingLease(preempt?: boolean): Promise<boolean>;
  /** 释放当前持有的发言权；失败拒绝。 */
  releaseSpeakingLease(): Promise<void>;
  /** lease.current 补拍快照。 */
  refreshSpeakingLease(): Promise<void>;
}

/** 交给群聊装配链（GroupChatPane）的发言权模型。 */
export interface GroupSpeakingLeaseModel {
  /** connect 应答快照的会话发言模式；仅用于面板条件渲染，消息提交由服务端逐条判定。 */
  speakingMode: SpeakingMode | undefined;
  /** 当前持有的租约（仅 holder 为本地 Human 时存在）。 */
  speakingLease: SpeakingLease | undefined;
  /** UI 四态：unheld / held / occupied / error。 */
  speakingLeaseStatus: SpeakingLeaseStatus;
  /** 是否有指令在途（按钮 loading）。 */
  isUpdatingSpeakingLease: boolean;
  /** held 状态下的当前时间（毫秒），驱动剩余秒倒计时。 */
  leaseNowMs: number;
  /** 获取 / 抢占发言权；失败已 toast 一次并返回 false。 */
  acquireSpeakingLease: (options?: { preempt?: boolean }) => Promise<boolean>;
  /** 释放发言权；失败已 toast 一次。 */
  releaseSpeakingLease: (lease: SpeakingLease) => Promise<void>;
  /** lease.current 补拍快照（发送成功 500ms 后调用以取回服务端续期后的租约）。 */
  refreshSpeakingLease: () => Promise<void>;
  /**
   * 包装群发送出口：发送后 500ms 补拍租约快照——服务端仅在成功接受 Human
   * 消息时续期，补拍取回新的到期时间（或观察到被抢占），不阻塞输入。
   */
  composeSender: (
    send: (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void,
  ) => (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void;
}

const INITIAL_SNAPSHOT: SpeakingLeaseSnapshot = {
  speakingMode: undefined,
  lease: undefined,
  status: 'unheld',
  requesting: false,
};

/**
 * 群聊发言权编排 Hook。Provider 变化（切换会话 / 身份重建）时即时隔离旧状态。
 */
export function useSpeakingLease(
  provider: SpeakingLeaseProvider | null,
): GroupSpeakingLeaseModel {
  const [snapshot, setSnapshot] = useState<SpeakingLeaseSnapshot>(INITIAL_SNAPSHOT);
  const [leaseNowMs, setLeaseNowMs] = useState(() => Date.now());
  // 渲染期即时隔离：Provider 变化的同一次渲染即重置（排队中的旧事件由 ref 身份守卫兜底）。
  const [subscribedProvider, setSubscribedProvider] = useState<SpeakingLeaseProvider | null>(
    provider,
  );
  if (subscribedProvider !== provider) {
    setSubscribedProvider(provider);
    setSnapshot(INITIAL_SNAPSHOT);
    setLeaseNowMs(Date.now());
  }

  // 最新 Provider 身份：迟到快照 / 迟到失败不写新状态、不产生新 toast。
  const providerRef = useRef<SpeakingLeaseProvider | null>(provider);
  providerRef.current = provider;

  useEffect(() => {
    if (!provider) return;
    const offLease = provider.subscribeToSpeakingLease((next) => {
      if (providerRef.current !== provider) return;
      setSnapshot(next);
    });
    const offStatus = provider.subscribeToConnectionStatus((event) => {
      if (providerRef.current !== provider) return;
      // 连接建立即补拍快照；断开不清展示（到期/轮询由服务端/下轮结果收敛）。
      if (event.status === 'connected') void providerRef.current?.refreshSpeakingLease();
    });
    return () => {
      offLease();
      offStatus();
    };
  }, [provider]);

  // held 状态下的 1s 步进时钟，驱动「剩余 N 秒」倒计时。
  useEffect(() => {
    if (snapshot.status !== 'held') return;
    setLeaseNowMs(Date.now());
    const timer = setInterval(() => setLeaseNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [snapshot.status]);

  const acquireSpeakingLease = useCallback(
    async (options: { preempt?: boolean } = {}): Promise<boolean> => {
      const activeProvider = providerRef.current;
      if (!activeProvider) return false;
      const preempt = options.preempt ?? false;
      try {
        const acquired = await activeProvider.acquireSpeakingLease(preempt);
        if (!acquired) {
          toast.error(
            preempt ? '抢占发言权失败，请稍后重试' : '发言权被其他成员占用',
          );
        }
        return acquired;
      } catch (error) {
        if (providerRef.current !== activeProvider) return false;
        console.error('[useSpeakingLease] acquire failed:', error);
        toast.error('获取发言权失败，请稍后重试');
        return false;
      }
    },
    [],
  );

  const releaseSpeakingLease = useCallback(async (lease: SpeakingLease): Promise<void> => {
    const activeProvider = providerRef.current;
    if (!activeProvider || !lease) return;
    try {
      await activeProvider.releaseSpeakingLease();
    } catch (error) {
      if (providerRef.current !== activeProvider) return;
      console.error('[useSpeakingLease] release failed:', error);
      toast.error('释放发言权失败，请稍后重试');
    }
  }, []);

  const refreshSpeakingLease = useCallback(async (): Promise<void> => {
    const activeProvider = providerRef.current;
    if (!activeProvider) return;
    await activeProvider.refreshSpeakingLease();
  }, []);

  // composeSender 闭包内按最新快照判定是否补拍，避免持有过期状态。
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const composeSender = useCallback<
    GroupSpeakingLeaseModel['composeSender']
  >((send) => (text, mentions, attachments) => {
    send(text, mentions, attachments);
    if (snapshotRef.current.status === 'held') {
      setTimeout(() => void refreshSpeakingLease(), 500);
    }
  }, [refreshSpeakingLease]);

  return {
    speakingMode: snapshot.speakingMode,
    speakingLease: snapshot.lease,
    speakingLeaseStatus: snapshot.status,
    isUpdatingSpeakingLease: snapshot.requesting,
    leaseNowMs,
    acquireSpeakingLease,
    releaseSpeakingLease,
    refreshSpeakingLease,
    composeSender,
  };
}