/** @jest-environment jsdom */
import {
  claimEvaluationAutoExecute,
  clearEvaluationAutoExecuteClaimsForTest,
} from '@/services/tasks/taskEvaluationAutoExecute';

describe('claimEvaluationAutoExecute', () => {
  beforeEach(() => clearEvaluationAutoExecuteClaimsForTest());

  it('claims a unique evaluation card only once across remounts', () => {
    expect(claimEvaluationAutoExecute('eval-card-1')).toBe(true);
    expect(claimEvaluationAutoExecute('eval-card-1')).toBe(false);
    expect(claimEvaluationAutoExecute('eval-card-2')).toBe(true);
  });

  it('rejects an empty delivery id instead of risking duplicate task creation', () => {
    expect(claimEvaluationAutoExecute('')).toBe(false);
    expect(claimEvaluationAutoExecute('   ')).toBe(false);
  });
});
