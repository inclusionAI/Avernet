import type { RelayModeCoverageSmokeState } from '@/services/tasks/taskMapper';
import { buildExecuteRequest } from '@/services/tasks/taskMapper';

const form = {
  title: '存储行业尽调报告',
  instruction: '目标：产出存储行业尽调报告',
  objective: '产出存储行业尽调报告',
  acceptances: ['形成5条核心投资判断'],
  taskType: 'dynamic' as const,
  background: 'AI 基础设施驱动的存储行业变化',
  deliverables: ['尽调报告'],
  constraints: ['全球市场'],
  resources: [],
};

const context = {
  sourceType: 'bot' as const,
  ownerUserId: 'u1',
  ownerBotId: 'b1',
  mainSessionId: 's1',
  parentTaskId: null,
};

const smokeState: RelayModeCoverageSmokeState = {
  version: 3,
  current_phase: 'plan_single_bot',
  planned_modes: [],
};

describe('buildExecuteRequest', () => {
  it('keeps a normal task free of relay smoke extend_props', () => {
    const request = buildExecuteRequest(form, context);
    expect(request.task_spec.context.extend_props).not.toHaveProperty(
      'relay_mode_coverage_smoke',
    );
  });

  it('forwards the confirmed hidden smoke state to TaskSpec extend_props unchanged', () => {
    const request = buildExecuteRequest(
      { ...form, hidden_flags: { relay_mode_coverage_smoke: smokeState } },
      context,
    );
    expect(request.task_spec.context.extend_props.relay_mode_coverage_smoke).toEqual(
      smokeState,
    );
  });
});
