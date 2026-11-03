// @asset-migrated: teamclaw 自研资产
import { mapDashboard } from '@/assets/TaskPanel/taskPanelMapper';
import { collectTaskArtifacts } from '@/assets/TaskPanel/v2/TaskArtifactsList';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { describe, expect, it } from '@jest/globals';

describe('Task artifact relay stage', () => {
  const relayTask = {
    task_id: 'task-stage',
    run_id: 1,
    status: 'RUNNING',
    needs_attention: true,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'user-1',
    owner_bot_id: 'bot-owner',
    task_spec: { metadata: { title: '接力任务' }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-10-20T10:00:00+08:00',
    finish_time: null,
    loop_round: 1,
    progress: { total: 2, pending: 0, planning: 0, running: 1, done: 1, failed: 0, hung: 0, skipped: 0, percent: 50 },
    relations: [],
    tasks: [
      {
        node_id: 'n-first',
        task_id: 'task-stage',
        sequence: 0,
        status: 'DONE',
        task_spec: { metadata: { title: '第一棒' }, goal: { objective: '', acceptances: [] } },
        artifacts: [
          {
            artifact_id: 'artifact-first',
            artifact_name: '第一棒产物',
            kind: 'text',
            content: { text: '第一棒结论' },
            creator: 'bot-first',
            created_at: 1729400000000,
          },
        ],
      },
      {
        node_id: 'n-second',
        task_id: 'task-stage',
        sequence: 1,
        status: 'RUNNING',
        task_spec: { metadata: { title: '第二棒' }, goal: { objective: '', acceptances: [] } },
      },
    ],
    artifacts: [
      {
        artifact_id: 'artifact-second',
        node_id: 'n-second',
        name: '第二棒产物',
        type: 'document',
        created_at: '2026-10-01 10:00:00',
      },
    ],
  } as TaskDashboardResponse;

  it('保留任务级产物的 node_id', () => {
    const task = mapDashboard(relayTask);
    expect(task.artifacts[0]?.nodeId).toBe('n-second');
  });

  it('产物聚合时按节点展示顺序标记棒次并标出最后一棒', () => {
    const task = mapDashboard(relayTask);
    const artifacts = collectTaskArtifacts(task);
    const summary = artifacts.map(
      (artifact: Pick<TaskArtifactView, 'id' | 'stage' | 'isFinalStage'>) =>
        [artifact.id, artifact.stage, artifact.isFinalStage].join(':'),
    );
    expect(summary).toEqual(['artifact-first:1:false', 'artifact-second:2:true']);
  });
});
