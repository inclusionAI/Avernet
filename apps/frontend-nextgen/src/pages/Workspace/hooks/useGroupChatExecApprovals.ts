import type {
  GroupExecApproval,
  GroupExecDecision,
  GroupExecResolution,
} from '@/domain/collaboration/groupExecApprovals';
import type { GroupExecApprovalSnapshot } from '@/services/workspace/groupExecApprovalService';
import type { ConnectionStatusEvent } from '@tc-chat/adapters';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface GroupExecApprovalsProvider {
  subscribeToExecApprovals(listener: (snapshot: GroupExecApprovalSnapshot) => void): () => void;
  resolveExecApproval(key: string, resolution: GroupExecResolution): Promise<void>;
  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void): () => void;
  readonly isConnected: boolean;
}
export interface GroupChatExecApprovalsModel {
  interactions: GroupExecApproval[];
  connected: boolean;
  submittingKeys: string[];
  resolve(key: string, decision: GroupExecDecision): Promise<boolean>;
}
interface ApprovalState {
  interactions: GroupExecApproval[];
  connected: boolean;
  submittingKeys: string[];
}
const emptyState = (): ApprovalState => ({ interactions: [], connected: false, submittingKeys: [] });

export function useGroupChatExecApprovals(provider: GroupExecApprovalsProvider | null): GroupChatExecApprovalsModel {
  const [state, setState] = useState(emptyState);
  const [owner, setOwner] = useState(provider);
  if (owner !== provider) {
    setOwner(provider);
    setState(emptyState());
  }
  const current = useRef(provider);
  current.current = provider;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!provider) return;
    const offApprovals = provider.subscribeToExecApprovals((snapshot) => {
      if (current.current !== provider) return;
      const keys = new Set(snapshot.interactions.map((item) => item.key));
      setState((value) => ({
        ...value,
        interactions: snapshot.interactions,
        submittingKeys:
          snapshot.reason === 'update' ? value.submittingKeys : value.submittingKeys.filter((key) => keys.has(key)),
      }));
    });
    const offStatus = provider.subscribeToConnectionStatus((event) => {
      if (current.current === provider) {
        setState((value) => ({ ...value, connected: event.status === 'connected' }));
      }
    });
    setState((value) => ({ ...value, connected: provider.isConnected }));
    return () => {
      offApprovals();
      offStatus();
    };
  }, [provider]);

  const resolve = useCallback(async (key: string, decision: GroupExecDecision): Promise<boolean> => {
    const active = current.current;
    if (!active) return false;
    setState((value) => ({ ...value, submittingKeys: [...new Set([...value.submittingKeys, key])] }));
    try {
      await active.resolveExecApproval(key, { decision });
      return true;
    } catch (error) {
      if (mounted.current && current.current === active) {
        toast.error(error instanceof Error ? error.message : '审批提交失败，请重试');
      }
      return false;
    } finally {
      if (mounted.current && current.current === active) {
        setState((value) => ({ ...value, submittingKeys: value.submittingKeys.filter((item) => item !== key) }));
      }
    }
  }, []);
  return { ...state, resolve };
}
