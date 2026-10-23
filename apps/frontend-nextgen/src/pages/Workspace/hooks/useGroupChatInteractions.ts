import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useGroupChatExecApprovals } from './useGroupChatExecApprovals';
import { useGroupChatQuestions } from './useGroupChatQuestions';
import { useSpeakingLease } from './useSpeakingLease';

/** Human interaction models share the same provider lifecycle. */
export function useGroupChatInteractions(provider: GroupChatProvider | null) {
  return {
    questions: useGroupChatQuestions(provider),
    execApprovals: useGroupChatExecApprovals(provider),
    speakingLease: useSpeakingLease(provider),
  };
}