import {
  createGroup, groupCreationBody, UserApiError, validateGroupDefinition,
} from '@/services/backend-api/GroupCreationController';
import type { GroupCreationResult } from '@/services/backend-api/GroupCreationController';
import type { CreateGroupParams } from '../types';

export interface GroupSubmissionState {
  phase: 'editing' | 'preflight' | 'creating' | 'unknown' | 'success';
  error?: string;
  result?: GroupCreationResult;
}

// One instance belongs to one mounted modal, including while closed.
export class GroupCreationSubmission {
  state: GroupSubmissionState = { phase: 'editing' };
  private controller?: AbortController;

  constructor(private changed: (state: GroupSubmissionState) => void) {}

  private update(state: GroupSubmissionState) {
    this.state = state;
    this.changed(state);
  }

  dispose() { this.controller?.abort(); }

  async submit(input: { params: CreateGroupParams }): Promise<GroupCreationResult | undefined> {
    if (this.state.phase !== 'editing') return;
    // Snapshot the current form before any await; a Draft is no longer involved.
    const params: CreateGroupParams = structuredClone(input.params);
    this.update({ phase: 'preflight' });
    const controller = new AbortController();
    this.controller = controller;
    let posted = false;
    try {
      const checked = params.group_strategy === 'state_machine'
        ? await validateGroupDefinition(params.collaboration_definition_yaml || '', controller.signal) : undefined;
      const body = groupCreationBody(params, checked);
      if (controller.signal.aborted) return;
      this.update({ phase: 'creating' });
      posted = true;
      const result = await createGroup(body, controller.signal);
      if (controller.signal.aborted) return;
      this.update({ phase: 'success', result });
      return result;
    } catch (error) {
      if (controller.signal.aborted) return;
      const rejected = error instanceof UserApiError && !error.unknownOutcome;
      this.update({ phase: posted && !rejected ? 'unknown' : 'editing',
        error: error instanceof Error ? error.message : '创建失败' });
    }
  }
}
