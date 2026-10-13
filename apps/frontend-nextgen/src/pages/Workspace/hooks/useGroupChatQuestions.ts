/**
 * useGroupChatQuestions —— BCS 群聊 ask_user 问答编排 Hook。
 *
 * 订阅 Provider 问答快照与真实连接状态，集中维护草稿 / 提交态 / 错误反馈：
 * - 草稿按「交互 key + questionId」两级字典保存；连接 reset 只清展示不清草稿，
 *   重放同 key 交互后自然恢复（重连恢复）；终态移除交互时释放对应草稿。
 * - Provider 替换 / 会话切换在渲染期即时隔离旧状态；旧 Provider 的迟到快照与
 *   迟到失败不更新新状态、不产生新 toast（防「旧会话回流」污染新页面）。
 * - resolve 返回 Promise<boolean>：失败 toast 一次并返回 false，供确认层保留
 *   弹窗与已填内容；成功由服务层终态快照收起问答（草稿随之释放）。
 *
 * 协议细节（帧解析、幂等、超时、终态）归 services/workspace；本 Hook 只做展示编排。
 */
import type {
  GroupQuestionInteraction,
  GroupQuestionResolution,
  QuestionDraft,
  QuestionDrafts,
} from '@/domain/collaboration/groupQuestions';
import type { GroupQuestionSnapshot } from '@/services/workspace/groupQuestionService';
import type { ConnectionStatusEvent } from '@tc-chat/adapters';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** Hook 消费的 Provider 结构——GroupChatProvider 结构性满足，测试可注入可控 fake。 */
export interface GroupQuestionsProvider {
  /** 订阅问答快照；订阅即回放当前待处理列表。 */
  subscribeToQuestions(listener: (snapshot: GroupQuestionSnapshot) => void): () => void;
  /** 提交答案 / 取消整组；失败拒绝结构化错误。 */
  resolveQuestion(key: string, resolution: GroupQuestionResolution): Promise<void>;
  /** 订阅底层 WebSocket 连接状态（不回放）。 */
  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void): () => void;
  /** 当前连接是否可用（真实连接状态，非 UI 展示状态）。 */
  readonly isConnected: boolean;
}

/** 交给群聊装配链（GroupChatPane）的问答模型。草稿与提交态均归本 Hook 所有。 */
export interface GroupChatQuestionsModel {
  /** 当前待处理 ask_user 交互（服务层快照，按到达顺序）。 */
  interactions: GroupQuestionInteraction[];
  /** 交互 key → questionId → 草稿。连接 reset 期间保留（暂存待重放恢复）。 */
  drafts: Record<string, QuestionDrafts>;
  /** 正在等待服务端响应的交互 key（其他交互不受影响）。 */
  submittingKeys: string[];
  /** 底层连接是否可用；断线时禁用提交。 */
  connected: boolean;
  /** 按「交互 key + questionId」更新草稿（作答修改应覆盖 skipped 标记）。 */
  setDraft: (key: string, questionId: string, draft: QuestionDraft) => void;
  /** 提交 / 取消整组；成功 true，失败 false（已 toast 一次）。 */
  resolve: (key: string, resolution: GroupQuestionResolution) => Promise<boolean>;
}

interface QuestionsState {
  interactions: GroupQuestionInteraction[];
  drafts: Record<string, QuestionDrafts>;
  submittingKeys: string[];
  connected: boolean;
}

const INITIAL_STATE: QuestionsState = {
  interactions: [],
  drafts: {},
  submittingKeys: [],
  connected: false,
};

/** 空原型字典：questionId / 交互 key 不落到原型链（'__proto__' 等合法 ID 安全）。 */
function nullProtoDrafts(): Record<string, QuestionDrafts> {
  return Object.create(null) as Record<string, QuestionDrafts>;
}

function emptyQuestionDrafts(): QuestionDrafts {
  return Object.create(null) as QuestionDrafts;
}

function removeFromSubmitting(state: QuestionsState, key: string): QuestionsState {
  if (!state.submittingKeys.includes(key)) return state;
  return { ...state, submittingKeys: state.submittingKeys.filter((k) => k !== key) };
}

/**
 * 群聊问答编排 Hook。Provider 变化（切换会话 / 身份重建）时即时隔离旧状态；
 * 重连 reset 期间草稿暂存不清空，仅当重放后重新可见。
 */
export function useGroupChatQuestions(
  provider: GroupQuestionsProvider | null,
): GroupChatQuestionsModel {
  const [state, setState] = useState<QuestionsState>(INITIAL_STATE);
  // 渲染期即时隔离：Provider 变化的同一次渲染即重置（不等待 effect 清理，
  // 排队中的旧事件由下方 ref 身份守卫兜底）。
  const [subscribedProvider, setSubscribedProvider] = useState<GroupQuestionsProvider | null>(
    provider,
  );
  const knownKeysRef = useRef<Set<string>>(new Set());
  if (subscribedProvider !== provider) {
    setSubscribedProvider(provider);
    setState(INITIAL_STATE);
    knownKeysRef.current = new Set();
  }

  // 最新 Provider 身份 + 挂载标记：迟到结算（旧 Provider / 卸载后）不写状态、不 toast。
  const providerRef = useRef<GroupQuestionsProvider | null>(provider);
  providerRef.current = provider;
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const applySnapshot = useCallback((snapshot: GroupQuestionSnapshot) => {
    const previousKeys = knownKeysRef.current;
    const nextKeys = new Set(snapshot.interactions.map((interaction) => interaction.key));
    knownKeysRef.current = nextKeys;

    setState((current) => {
      if (snapshot.reason === 'update' || snapshot.reason === 'reset') {
        // reset 只清展示（重放由服务端 Pending 重建）；草稿暂存保留待恢复。
        return { ...current, interactions: snapshot.interactions };
      }
      // terminal：被移除交互的草稿与提交标记一并释放，防止终态复活或草稿泄漏。
      const removedKeys = Array.from(previousKeys).filter((key) => !nextKeys.has(key));
      if (removedKeys.length === 0) return { ...current, interactions: snapshot.interactions };
      // 只清受本快照影响（previously known 且已被终态移除）的交互草稿；本周期内
      // 尚未重放的暂存草稿保留，待其重放时恢复（协议允许重放/终态交错）。
      const drafts = nullProtoDrafts();
      let draftsChanged = false;
      for (const [key, questionDrafts] of Object.entries(current.drafts)) {
        if (removedKeys.includes(key)) draftsChanged = true;
        else drafts[key] = questionDrafts;
      }
      const submittingKeys = current.submittingKeys.filter(
        (key) => !removedKeys.includes(key),
      );
      return {
        ...current,
        interactions: snapshot.interactions,
        drafts: draftsChanged ? drafts : current.drafts,
        submittingKeys,
      };
    });
  }, []);

  useEffect(() => {
    if (!provider) return;
    // 旧 Provider 在 effect 清理前被捕获的迟到快照 / 状态变化，不得写入新状态。
    const offQuestions = provider.subscribeToQuestions((snapshot) => {
      if (providerRef.current !== provider) return;
      applySnapshot(snapshot);
    });
    const offStatus = provider.subscribeToConnectionStatus((event) => {
      if (providerRef.current !== provider) return;
      setState((current) => ({ ...current, connected: event.status === 'connected' }));
    });
    // 连接状态订阅不回放，以当前真实连接状态初始化。
    setState((current) => ({ ...current, connected: provider.isConnected }));
    return () => {
      offQuestions();
      offStatus();
    };
  }, [provider, applySnapshot]);

  const setDraft = useCallback((key: string, questionId: string, draft: QuestionDraft) => {
    setState((current) => {
      const drafts = nullProtoDrafts();
      for (const [existingKey, existing] of Object.entries(current.drafts)) {
        drafts[existingKey] = existing;
      }
      const perInteraction = emptyQuestionDrafts();
      const prevPerInteraction = current.drafts[key];
      if (prevPerInteraction) {
        for (const [existingId, existingDraft] of Object.entries(prevPerInteraction)) {
          perInteraction[existingId] = existingDraft;
        }
      }
      perInteraction[questionId] = draft;
      drafts[key] = perInteraction;
      return { ...current, drafts };
    });
  }, []);

  const resolve = useCallback(
    (key: string, resolution: GroupQuestionResolution): Promise<boolean> => {
      const activeProvider = providerRef.current;
      if (!activeProvider) return Promise.resolve(false);
      setState((state_) => ({
        ...state_,
        submittingKeys: state_.submittingKeys.includes(key)
          ? state_.submittingKeys
          : [...state_.submittingKeys, key],
      }));
      return activeProvider.resolveQuestion(key, resolution).then(
        () => {
          if (mountedRef.current && providerRef.current === activeProvider) {
            setState((state_) => removeFromSubmitting(state_, key));
          }
          return true;
        },
        (error: unknown) => {
          // 卸载或已切换 Provider：迟到失败不写新状态、不产生新 toast。
          if (!mountedRef.current || providerRef.current !== activeProvider) return false;
          setState((state_) => removeFromSubmitting(state_, key));
          // 失败集中提示一次（保留输入），由调用方决定重试或取消。
          const message =
            error instanceof Error && error.message ? error.message : '问答提交失败，请重试';
          toast.error(message);
          return false;
        },
      );
    },
    [],
  );

  return {
    interactions: state.interactions,
    drafts: state.drafts,
    submittingKeys: state.submittingKeys,
    connected: state.connected,
    setDraft,
    resolve,
  };
}