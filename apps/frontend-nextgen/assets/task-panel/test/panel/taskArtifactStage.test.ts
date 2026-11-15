// @asset-migrated: teamclaw 自研资产
import { mapDashboard } from '../../src/panel/taskPanelMapper';
import { collectTaskArtifacts } from '../../src/panel/v2/TaskArtifactsList';
import type { TaskArtifactView, TaskView } from '../../src/panel/types';
import type { TaskDashboardResponse } from '../../src/panel/contract';
import { describe, expect, it } from 'vitest';

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
            content: { text: '第一棒结论', is_final_artifact: 'true' },
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

  it('产物列表按棒次分组，棒内按创建时间和产物 ID 正序展示', () => {
    const makeArtifact = (
      id: string,
      nodeId: string,
      updatedAt: string,
      sourceOrder: number,
    ): TaskArtifactView => ({
      id,
      name: id,
      type: 'document',
      nodeId,
      updatedAt,
      sourceOrder,
    });
    const makeNode = (id: string, sequence: number, artifacts: TaskArtifactView[]) => ({
      id,
      name: id,
      sequence,
      status: 'pending',
      artifacts,
      hasSubTask: false,
      stepTraces: [],
    });

    const task = {
      id: 'task-artifact-order',
      name: '产物排序任务',
      description: '',
      goal: '',
      objective: '',
      acceptances: [],
      status: 'EXECUTING',
      taskType: 'dynamic',
      taskTypeLabel: '',
      sourceLabel: '',
      ownerBotName: '',
      ownerBotId: '',
      ownerUserId: '',
      createdAt: '',
      finishedAt: null,
      loopRound: 1,
      needsAttention: false,
      progress: { total: 2, pending: 2, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      artifacts: [],
      nodes: [
        makeNode('node-first', 0, [
          makeArtifact('newer-first', 'node-first', '2026-11-01T10:00:00Z', 0),
          makeArtifact('same-time-10', 'node-first', '2026-11-01T09:00:00Z', 1),
          makeArtifact('same-time-2', 'node-first', '2026-11-01T09:00:00Z', 2),
        ]),
        makeNode('node-second', 1, [
          makeArtifact('later-second', 'node-second', '2026-11-01T11:00:00Z', 0),
        ]),
      ],
      dagNodes: [],
      dagEdges: [],
    } as TaskView;

    expect(collectTaskArtifacts(task).map((artifact) => [artifact.id, artifact.stage])).toEqual([
      ['same-time-2', 1],
      ['same-time-10', 1],
      ['newer-first', 1],
      ['later-second', 2],
    ]);
  });

  it('产物聚合时按节点展示顺序标记棒次,并以 is_final_artifact 标记最终产物', () => {
    const task = mapDashboard(relayTask);
    const artifacts = collectTaskArtifacts(task);
    const summary = artifacts.map(
      (artifact: Pick<TaskArtifactView, 'id' | 'stage' | 'isFinalArtifact'>) =>
        [artifact.id, artifact.stage, artifact.isFinalArtifact].join(':'),
    );
    expect(summary).toEqual(['artifact-first:1:true', 'artifact-second:2:false']);
  });
});
