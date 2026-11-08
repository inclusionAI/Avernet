import type { GroupQuestionInteraction, GroupQuestionResolution } from '@/domain/collaboration/groupQuestions';
import { parseGroupQuestionEvent } from './groupQuestionFrames';
import { GroupInteractionService, type GroupInteractionSnapshot, type GroupQuestionServiceConfig } from './groupInteractionService';

export { GroupQuestionResolveError } from './groupInteractionService';
export type { GroupQuestionServiceConfig } from './groupInteractionService';
export type GroupQuestionSnapshot = GroupInteractionSnapshot<GroupQuestionInteraction>;

/** Ask-user facade over the shared interaction lifecycle. */
export class GroupQuestionService extends GroupInteractionService<GroupQuestionInteraction, GroupQuestionResolution> {
  constructor(config: GroupQuestionServiceConfig) {
    super(config, {
      parse(frame, identity) {
        const event = parseGroupQuestionEvent(frame, identity);
        if (!event) return null;
        if (event.phase !== 'requested') return event;
        const { phase, ...interaction } = event;
        return { phase, key: event.key, interaction };
      },
      fingerprint(interaction, resolution) {
        if (resolution.action === 'cancel') return JSON.stringify(['cancel']);
        return JSON.stringify(['submit', interaction.questions.map((question) => [
          question.questionId, resolution.answers[question.questionId]?.values ?? null,
        ])]);
      },
    });
  }
}
