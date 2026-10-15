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

describe('evaluation auto-confirmed execute request', () => {
  it('preserves every user acceptance string and maps it to request acceptance fields', () => {
    const acceptances = [
      '明确存储行业当前是否具备中短期投资价值',
      '明确最值得跟踪的细分赛道、公司类型和核心变量',
      '提供市场规模、竞争格局、技术演进、客户需求四大维度的系统分析',
      '至少形成5条核心投资判断',
    ];
    const request = buildExecuteRequest(
      {
        ...form,
        objective:
          '存储行业尽调：AI基础设施驱动下，企业级与数据中心存储行业的最新变化、竞争格局与进入机会',
        acceptances,
        background: 'AI基础设施驱动下，企业级与数据中心存储行业发生变化',
        deliverables: ['存储行业尽调报告'],
        hidden_flags: {
          evaluation_auto_execute: true,
          evaluation_auto_execute_id: 'evaluation-card-1',
          relay_mode_coverage_smoke: smokeState,
        },
      },
      context,
    );

    expect(request.task_spec.goal.acceptances).toEqual(
      acceptances.map((acceptance, index) => ({
        id: `ac${index + 1}`,
        acceptance,
      })),
    );
    expect(request.task_spec.goal.acceptances).not.toEqual([
      { id: 'ac1', acceptance: '' },
      { id: 'ac2', acceptance: '' },
    ]);
    expect(request.task_spec.context.title).toBe('存储行业尽调报告');
    expect(request.task_spec.context.background).toBe(
      'AI基础设施驱动下，企业级与数据中心存储行业发生变化',
    );
    expect(request.task_spec.context.extend_props).toEqual(
      expect.objectContaining({
        deliverables: ['存储行业尽调报告'],
        relay_mode_coverage_smoke: smokeState,
      }),
    );
    expect(request.task_spec.context.extend_props).not.toHaveProperty(
      'evaluation_auto_execute',
    );
    expect(request.task_spec.context.extend_props).not.toHaveProperty(
      'evaluation_auto_execute_id',
    );
  });
});
