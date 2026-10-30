/** @jest-environment node */
// taskPanelMapper 分支补齐单测:覆盖 executorColor(durationDisplay/isEmptyOutput/
// nonEmptyOutputSource/mapNodeStatus/layoutDag 环兜底/execution_graph 根节点兜底 等此前未触达分支。
import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { mapDashboard } from '@/assets/TaskPanel/taskPanelMapper';
import { describe, expect, it } from '@jest/globals';

/** 最小 dashboard 信封,产物/进度等字段留默认,用 overrides 覆盖。 */
const buildDashboard = (overrides?: Partial<TaskDashboardResponse>): TaskDashboardResponse =>
  ({
    task_id: 'task-branch',
    run_id: 1,
    status: 'RUNNING',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'user-1',
    owner_bot_id: 'bot-1',
    task_spec: { metadata: { title: '分支任务' }, goal: { objective: '目标', acceptances: [] } },
    create_time: '2026-10-30T10:00:00+08:00',
    finish_time: null,
    loop_round: 1,
    progress: {
      total: 1,
      pending: 1,
      planning: 0,
      running: 0,
      done: 0,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 0,
    },
    tasks: [],
    relations: [],
    ...overrides,
  }) as unknown as TaskDashboardResponse;

describe('taskPanelMapper executorColor 分支', () => {
  it('run_mode=bbs 命中红色,非根节点 run_mode 缺省命中 default 返回 null', () => {
    const task = mapDashboard(
      buildDashboard({
        progress: {
          total: 2,
          pending: 0,
          planning: 0,
          running: 2,
          done: 0,
          failed: 0,
          hung: 0,
          skipped: 0,
          percent: 0,
        },
        tasks: [
          {
            node_id: 'node-bbs',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            run_info: { run_mode: 'bbs', assignee: 'bbs-bot' },
            task_spec: { metadata: { title: 'bbs 节点' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-empty-mode',
            task_id: 'task-branch',
            sequence: 2,
            status: 'PENDING',
            // 非根节点无 run_mode/extend_props → effectiveRunMode=null → executorColor default null
            run_info: {},
            task_spec: { metadata: { title: '空模态' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes.find((n) => n.id === 'node-bbs')!.executorColor).toBe('#F53F3F');
    expect(task.nodes.find((n) => n.id === 'node-empty-mode')!.executorColor).toBeNull();
  });
});

describe('taskPanelMapper durationDisplay 分支', () => {
  it('end<start 返回 null;分钟段与小时段分别格式化', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            node_id: 'node-neg',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            run_info: { start_time: 100000, end_time: 50000 },
            task_spec: { metadata: { title: '负向' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-minutes',
            task_id: 'task-branch',
            sequence: 2,
            status: 'RUNNING',
            run_info: { start_time: 0, end_time: 120000 },
            task_spec: { metadata: { title: '两分钟' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-hours',
            task_id: 'task-branch',
            sequence: 3,
            status: 'RUNNING',
            run_info: { start_time: 0, end_time: 3_600_000 },
            task_spec: { metadata: { title: '一小时' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes.find((n) => n.id === 'node-neg')!.timeConsuming).toBeNull();
    expect(task.nodes.find((n) => n.id === 'node-minutes')!.timeConsuming).toBe('2分钟');
    expect(task.nodes.find((n) => n.id === 'node-hours')!.timeConsuming).toBe('1小时0分钟');
  });
});

describe('taskPanelMapper isEmptyOutput 非 string/array/object 分支', () => {
  it('relay 节点 output 为数字(number)时 isEmptyOutput 返回 false,数字产出被聚合保留', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-relay-number',
        execution_config: { orchestration_mode: 'relay' },
        // 不下发顶层 output,强制 rootOutputSource 走 collectNodeOutputs 聚合
        tasks: [
          {
            node_id: 'node-number',
            task_id: 'task-relay-number',
            sequence: 1,
            status: 'DONE',
            // output 为非空原始值(number):走 isEmptyOutput 的 `return false`(L80)
            run_info: { output: 123 },
            task_spec: { metadata: { title: '数字产出' }, goal: { objective: '', acceptances: [] } },
          },
        ] as unknown as TaskDashboardResponse['tasks'],
      }),
    );
    // 聚合 output 为 { node-number: 123 },整块渲染时包成 json 代码块并保留数字
    expect(task.rootOutputRender).toContain('"node-number": 123');
  });
});

describe('taskPanelMapper nonEmptyOutputSource 末端 outputSummary 兜底分支', () => {
  it('output 为 null 但 output_summary 非空时,末端 return outputSummary ?? null 命中', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-relay-summary',
        execution_config: { orchestration_mode: 'relay' },
        tasks: [
          {
            node_id: 'node-summary',
            task_id: 'task-relay-summary',
            sequence: 1,
            status: 'DONE',
            // output 缺省+output_summary 非空:isEmptyOutput 因 summary 为 false,
            // 跳过 object/string 分支后命中末端 return outputSummary ?? null(L135)
            run_info: { output: null, output_summary: '接力节点摘要兜底' },
            task_spec: { metadata: { title: '摘要兜底' }, goal: { objective: '', acceptances: [] } },
          },
        ] as unknown as TaskDashboardResponse['tasks'],
      }),
    );
    // summary 字符串作为维度内容透出到产物维度卡片,证明其经 L135 流入聚合产物
    expect(task.rootOutputDimensions).toEqual([{ key: 'node-summary', content: '接力节点摘要兜底' }]);
  });
});

describe('taskPanelMapper mapNodeStatus 失败/取消/挂起分支', () => {
  it('FAILED/CANCELLED/HUNG 分别映射为 failed/cancelled/hung', () => {
    // 不下发顶层 progress,强制 computeProgress 走「从节点状态统计」分支,
    // 同时 cancelled 不在 counts 键内(b116 else 分支)。
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-statuses',
        progress: undefined,
        tasks: [
          {
            node_id: 'node-failed',
            task_id: 'task-statuses',
            sequence: 1,
            status: 'FAILED',
            task_spec: { metadata: { title: '失败' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-cancelled',
            task_id: 'task-statuses',
            sequence: 2,
            status: 'CANCELLED',
            task_spec: { metadata: { title: '取消' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-hung',
            task_id: 'task-statuses',
            sequence: 3,
            status: 'HUNG',
            task_spec: { metadata: { title: '挂起' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes.find((n) => n.id === 'node-failed')!.status).toBe('failed');
    expect(task.nodes.find((n) => n.id === 'node-cancelled')!.status).toBe('cancelled');
    expect(task.nodes.find((n) => n.id === 'node-hung')!.status).toBe('hung');
    // 计数由节点状态汇总:done/failed/hung/skipped 计入完成率,cancelled 不计入
    expect(task.progress.total).toBe(3);
    expect(task.progress.failed).toBe(1);
    expect(task.progress.hung).toBe(1);
    expect(task.progress.done).toBe(0);
    expect(task.progress.percent).toBe(33);
  });
});

describe('taskPanelMapper layoutDag 环兜底分支', () => {
  it('所有节点入度>0(互相环)时,空队列兜底 push nodes[0] 仍能产出全部 DAG 节点', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-cycle',
        progress: {
          total: 2,
          pending: 0,
          planning: 0,
          running: 2,
          done: 0,
          failed: 0,
          hung: 0,
          skipped: 0,
          percent: 0,
        },
        tasks: [
          {
            node_id: 'node-a',
            task_id: 'task-cycle',
            sequence: 1,
            status: 'RUNNING',
            task_spec: { metadata: { title: 'A' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'node-b',
            task_id: 'task-cycle',
            sequence: 2,
            status: 'RUNNING',
            task_spec: { metadata: { title: 'B' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
        // A→B 且 B→A:两节点入度均为 1,初始队列为空,命中 fallback push nodes[0]
        relations: [
          { src_id: 'node-a', dst_id: 'node-b', type: 'DEPENDENCY' },
          { src_id: 'node-b', dst_id: 'node-a', type: 'DEPENDENCY' },
        ] as TaskDashboardResponse['relations'],
      }),
    );
    expect(task.dagNodes).toHaveLength(2);
    expect(task.dagNodes.map((n) => n.id).sort()).toEqual(['node-a', 'node-b']);
  });
});

describe('taskPanelMapper execution_graph 根节点兜底分支', () => {
  it('task_id 未出现在 execution_graph.tasks 时回落 graphTasks[0] 作为根节点', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'wf-missing',
        execution_config: { task_type: 'workflow' },
        source_type: 'bot',
        task_spec: { metadata: { title: 'workflow 任务' }, goal: { objective: '', acceptances: [] } },
        execution_graph: {
          tasks: [
            {
              node_id: 'wf-int-1',
              task_id: 'wf-missing',
              sequence: 1,
              status: 'RUNNING',
              run_info: {},
              task_spec: { metadata: { title: '内部节点一' }, goal: { objective: '', acceptances: [] } },
            },
            {
              node_id: 'wf-int-2',
              task_id: 'wf-missing',
              sequence: 2,
              status: 'PENDING',
              run_info: {},
              task_spec: { metadata: { title: '内部节点二' }, goal: { objective: '', acceptances: [] } },
            },
          ],
          relations: [],
          // 图级 source_type 让根节点 derive 出 run_mode=coop_group(证明 graphTasks[0] 被当根)
          extend_props: { source_type: 'coop_group', owner_bot_id: 'wf-graph-owner' },
        },
        tasks: [] as TaskDashboardResponse['tasks'],
      }),
    );
    // 未命中 task_id → graphTasks[0] 被当作根:继承图级 source_type/owner_bot_id
    const root = task.nodes.find((n) => n.id === 'wf-int-1')!;
    expect(root.runMode).toBe('coop_group');
    expect(root.assignee).toBe('wf-graph-owner');
    // 非根节点不继承图级 source_type,runMode 为 null
    expect(task.nodes.find((n) => n.id === 'wf-int-2')!.runMode).toBeNull();
  });

  it('task_id 命中 execution_graph.tasks 时(find 命中分支),命中的节点作为根节点', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'wf-root',
        execution_config: { task_type: 'workflow' },
        source_type: 'bot',
        task_spec: { metadata: { title: 'workflow 任务' }, goal: { objective: '', acceptances: [] } },
        execution_graph: {
          tasks: [
            {
              node_id: 'wf-other',
              task_id: 'wf-root',
              sequence: 1,
              status: 'RUNNING',
              run_info: {},
              task_spec: { metadata: { title: '非根' }, goal: { objective: '', acceptances: [] } },
            },
            {
              node_id: 'wf-root',
              task_id: 'wf-root',
              sequence: 2,
              status: 'RUNNING',
              run_info: {},
              task_spec: { metadata: { title: '根' }, goal: { objective: '', acceptances: [] } },
            },
          ],
          relations: [],
          extend_props: { source_type: 'coop_group' },
        },
        tasks: [] as TaskDashboardResponse['tasks'],
      }),
    );
    // find 命中第二个节点(wf-root),其作为根继承图级 source_type → run_mode=coop_group
    expect(task.nodes.find((n) => n.id === 'wf-root')!.runMode).toBe('coop_group');
    // 命中 graphTasks[1] 而非 graphTasks[0]:第一个节点非根,不继承 → runMode null
    expect(task.nodes.find((n) => n.id === 'wf-other')!.runMode).toBeNull();
  });
});

describe('taskPanelMapper msToDisplay 越界毫秒戳分支', () => {
  it('毫秒戳越界(>8.64e15)产生 Invalid Date → 返回数字字符串', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            node_id: 'node-overflow',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            // 超出 Date 合法区间 → d.getTime() 为 NaN → 走 String(ms) 分支
            run_info: { start_time: 1e16 },
            task_spec: { metadata: { title: '溢出' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes[0]!.startedAt).toBe('10000000000000000');
  });
});

describe('taskPanelMapper durationDisplay 无 end_time 分支', () => {
  it('只有 start_time、缺 end_time 时,以 Date.now() 计算 open-ended 时长(返回非空字符串)', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            node_id: 'node-open',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            // start_time 早于现在,end_time 缺省 → e = Date.now() → diff 正 → 命中 ?? Date.now() 右支
            run_info: { start_time: Date.now() - 5_000 },
            task_spec: { metadata: { title: '进行中' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes[0]!.timeConsuming).toMatch(/^\d+秒$/);
  });
});

describe('taskPanelMapper isEmptyOutput 数组分支', () => {
  it('relay 节点 output 为非空数组 → isEmptyOutput 走 Array.isArray 分支并保留', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-relay-array',
        execution_config: { orchestration_mode: 'relay' },
        tasks: [
          {
            node_id: 'node-array',
            task_id: 'task-relay-array',
            sequence: 1,
            status: 'DONE',
            run_info: { output: [1, 2] },
            task_spec: { metadata: { title: '数组产出' }, goal: { objective: '', acceptances: [] } },
          },
        ] as unknown as TaskDashboardResponse['tasks'],
      }),
    );
    // 非空数组产物进入聚合 → 拆成维度卡片(数组对象包成 json 代码块)
    expect(task.rootOutputDimensions).toHaveLength(1);
    expect(task.rootOutputDimensions?.[0].content).toContain('1');
  });
});

describe('taskPanelMapper resolveNodeExecutor 对象 assignee 分支', () => {
  it('assignee 为对象(bcs 群 binding)时,hasNonEmptyValue 走 object 分支且取 binding 作为 assignee', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-obj-assignee',
        tasks: [
          {
            node_id: 'node-obj',
            task_id: 'task-obj-assignee',
            sequence: 1,
            status: 'RUNNING',
            run_info: {
              run_mode: 'coop_group',
              assignee: { type: 'bot', binding: 'grp-binding-x' },
              assignee_name: '群Bot',
              extend_props: { group_id: 'bcs_grp_x', session_id: 'bcs_grp_x:r1' },
            },
            task_spec: { metadata: { title: '群节点' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes[0]!.executor).toBe('群Bot');
    // 对象 assignee 规范化:无 assignee_bot_id → 取 binding 作为 assignee
    expect(task.nodes[0]!.assignee).toBe('grp-binding-x');
  });
});

describe('taskPanelMapper mapArtifacts 非空产物列表', () => {
  it('dashboard.artifacts 含 url/summary 缺省混合时,逐项映射并保留 optional 缺省', () => {
    const task = mapDashboard(
      buildDashboard({
        artifacts: [
          {
            artifact_id: 'a-1',
            name: '报告',
            type: 'report',
            url: 'https://example/r.pdf',
            summary: '报告摘要',
            created_at: '2026-10-30T09:00:00+08:00',
          },
          {
            artifact_id: 'a-2',
            name: '纯文件',
            type: 'file',
            created_at: '2026-10-30T09:01:00+08:00',
          },
        ] as TaskDashboardResponse['artifacts'],
      }),
    );
    expect(task.artifacts).toHaveLength(2);
    expect(task.artifacts[0]!.id).toBe('a-1');
    expect(task.artifacts[0]!.url).toBe('https://example/r.pdf');
    expect(task.artifacts[0]!.summary).toBe('报告摘要');
    expect(task.artifacts[1]!.id).toBe('a-2');
    expect(task.artifacts[1]!.url).toBeNull();
    expect(task.artifacts[1]!.summary).toBeNull();
  });
});

describe('taskPanelMapper layoutDag 断开的多个环', () => {
  it('两个互不相连的环:节点全入度>0,兜底 push nodes[0] 后仅头环被 BFS,尾环走 level 兜底 L345', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-two-cycles',
        tasks: [
          {
            node_id: 'a1',
            task_id: 'task-two-cycles',
            sequence: 1,
            status: 'RUNNING',
            task_spec: { metadata: { title: 'A1' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'b1',
            task_id: 'task-two-cycles',
            sequence: 2,
            status: 'RUNNING',
            task_spec: { metadata: { title: 'B1' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'c1',
            task_id: 'task-two-cycles',
            sequence: 3,
            status: 'PENDING',
            task_spec: { metadata: { title: 'C1' }, goal: { objective: '', acceptances: [] } },
          },
          {
            node_id: 'd1',
            task_id: 'task-two-cycles',
            sequence: 4,
            status: 'PENDING',
            task_spec: { metadata: { title: 'D1' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
        // A1↔B1 形成环一;C1↔D1 形成环二;两组之间无边。
        relations: [
          { src_id: 'a1', dst_id: 'b1', type: 'DEPENDENCY' },
          { src_id: 'b1', dst_id: 'a1', type: 'DEPENDENCY' },
          { src_id: 'c1', dst_id: 'd1', type: 'DEPENDENCY' },
          { src_id: 'd1', dst_id: 'c1', type: 'DEPENDENCY' },
        ] as TaskDashboardResponse['relations'],
      }),
    );
    expect(task.dagNodes).toHaveLength(4);
    expect(task.dagNodes.map((n) => n.id).sort()).toEqual(['a1', 'b1', 'c1', 'd1']);
  });
});

describe('taskPanelMapper mapNodeTaskSpec acceptances/title 回退分支', () => {
  it('acceptances 混合 string/object/description,标题依次回退 context→metadata→objective→null', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-specs',
        tasks: [
          {
            // 字符串 acceptance + 对象 acceptance(acceptance 字段)+ 对象 acceptance(只有 description)+ 对象 acceptance(无字段)
            node_id: 'spec-mixed',
            task_id: 'task-specs',
            sequence: 1,
            status: 'DONE',
            task_spec: {
              context: { title: '混合标题' },
              goal: {
                objective: '目标',
                acceptances: [
                  '字符串验收',
                  { acceptance: '对象验收' },
                  { description: '描述验收' },
                  { id: 'blank' },
                ],
              },
            },
          },
          {
            // 无 context.title/metadata.title,仅 objective → 标题回退到 objective
            node_id: 'spec-obj-only',
            task_id: 'task-specs',
            sequence: 2,
            status: 'DONE',
            task_spec: { goal: { objective: '仅目标回退', acceptances: [] } },
          },
          {
            // 无 context/metadata/goal.objective → 标题 null;无 goal.acceptances → 默认 []
            node_id: 'spec-bare',
            task_id: 'task-specs',
            sequence: 3,
            status: 'DONE',
            task_spec: {},
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    const mixed = task.nodes.find((n) => n.id === 'spec-mixed')!;
    expect(mixed.taskSpec!.title).toBe('混合标题');
    expect(mixed.taskSpec!.acceptances).toEqual(['字符串验收', '对象验收', '描述验收']);
    const objOnly = task.nodes.find((n) => n.id === 'spec-obj-only')!;
    expect(objOnly.taskSpec!.title).toBe('仅目标回退');
    expect(objOnly.taskSpec!.target).toBe('仅目标回退');
    const bare = task.nodes.find((n) => n.id === 'spec-bare')!;
    expect(bare.taskSpec!.title).toBeNull();
    expect(bare.taskSpec!.target).toBeNull();
    expect(bare.taskSpec!.acceptances).toEqual([]);
  });
});

describe('taskPanelMapper mapActionLog 事件类型/时间/状态分支', () => {
  it('tool/无时间戳/无状态事件触发 tool_call、空时间戳、JSON payload 不同分支', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            node_id: 'node-actions',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            run_info: {
              action_log: [
                // dispatch + 带状态迁移 → type tool_call, 时间非空, 有状态文本
                { seq: 1, ts: 1_700_000_000_000, action: 'dispatch', status_from: 'PENDING', status_to: 'RUNNING' },
                // tool + 无 ts(null)→时间空字符串, 无 status_from/to → JSON stringify payload
                { seq: 2, ts: null as unknown as number, action: 'tool', payload: { k: 'v' } },
                // action 为空串 → title 回退 'system',type system
                { seq: 3, ts: 1_700_000_000_000, action: '', payload: {} },
                // status_from 缺省(status_to 存在)→ 文本走 '—' → status_to
                { seq: 4, ts: 1_700_000_000_000, action: 'dispatch', status_from: null, status_to: 'RUNNING' },
                // status_to 缺省(status_from 存在)→ 文本走 status_from → '—'
                { seq: 5, ts: 1_700_000_000_000, action: 'dispatch', status_from: 'PENDING', status_to: null },
              ],
            },
            task_spec: { metadata: { title: '动作' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    const steps = task.nodes[0]!.stepTraces;
    expect(steps).toHaveLength(5);
    expect(steps[0]!.type).toBe('tool_call');
    expect(steps[0]!.timestamp).not.toBe('');
    expect(steps[0]!.content).toBe('PENDING → RUNNING');
    expect(steps[1]!.type).toBe('tool_call');
    expect(steps[1]!.timestamp).toBe('');
    expect(steps[1]!.content).toBe(JSON.stringify({ k: 'v' }));
    expect(steps[2]!.title).toBe('system');
    expect(steps[2]!.type).toBe('system');
    expect(steps[3]!.content).toBe('— → RUNNING');
    expect(steps[4]!.content).toBe('PENDING → —');
  });
});

describe('taskPanelMapper computeProgress 空对象 progress 的 ?? 兜底分支', () => {
  it('progress 为空对象(truthy 但无字段)时,逐字段 ?? 0 兜底', () => {
    const task = mapDashboard(
      buildDashboard({
        // 空对象 → if (d.progress) 为真,进入字段访问分支,所有 ?? 0 命中右支
        progress: {} as TaskDashboardResponse['progress'],
      }),
    );
    expect(task.progress.total).toBe(0);
    expect(task.progress.pending).toBe(0);
    expect(task.progress.running).toBe(0);
    expect(task.progress.done).toBe(0);
    expect(task.progress.failed).toBe(0);
    expect(task.progress.hung).toBe(0);
    expect(task.progress.skipped).toBe(0);
    expect(task.progress.percent).toBe(0);
  });

  it('progress 缺省且无节点时,percent 走 total===0 → 0 兜底', () => {
    const task = mapDashboard(
      buildDashboard({
        progress: undefined,
        tasks: [] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.progress.total).toBe(0);
    expect(task.progress.percent).toBe(0);
  });
});

describe('taskPanelMapper 根 spec / 任务元数据兜底分支', () => {
  it('无 context/metadata 时,标题回退 goal.objective;acceptance 无 acceptance/description 取空串被过滤', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-rootspec',
        // 顶层 task_spec 无 metadata/context,goal 含 objective 与混合 acceptances
        task_spec: {
          goal: { objective: '根目标', acceptances: [{ id: 'x' }, { acceptance: 'A' }, { description: 'D' }] },
        } as TaskDashboardResponse['task_spec'],
        tasks: [
          {
            // 节点不提供 goal,让 spec.goal 取顶层(覆盖混合 acceptances)
            node_id: 'task-rootspec',
            task_id: 'task-rootspec',
            sequence: 1,
            status: 'DONE',
            task_spec: {},
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    // meta.title 回退 objective:{id:'x'}(无 acceptance/description)→ 空串被 filter 去除
    expect(task.name).toBe('根目标');
    expect(task.goal).toBe('根目标');
    expect(task.objective).toBe('根目标');
    expect(task.acceptances).toEqual(['A', 'D']);
  });

  it('task_id 缺省 + create_time 缺省 + loop_round 缺省时,回退 rootNode.task_id/start_time/0', () => {
    const startTs = 1_700_000_000_000;
    const task = mapDashboard(
      buildDashboard({
        task_id: undefined as unknown as string,
        create_time: undefined as unknown as string,
        loop_round: undefined as unknown as number,
        // 无顶层 context/metadata/objective → 标题回退到 d.task_id(缺省)→ rootNode.node_id
        task_spec: { goal: { acceptances: [] } } as TaskDashboardResponse['task_spec'],
        tasks: [
          {
            node_id: 'fall-root',
            task_id: 'fallback-task-id',
            sequence: 1,
            status: 'DONE',
            run_info: { start_time: startTs },
            task_spec: { goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.id).toBe('fallback-task-id');
    expect(task.loopRound).toBe(0);
    // create_time 缺省 → 走 graphExtProps → 走 rootRunInfo.start_time → ISO 字符串
    expect(task.createdAt).toBe(new Date(startTs).toISOString());
  });

  it('top-level 与节点均无 goal → spec.goal 走默认;title 回退 d.task_id;acceptances/goal 为空', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-barespec',
        // 顶层 task_spec 无 goal(触发 spec.goal ?? 默认)
        task_spec: {} as TaskDashboardResponse['task_spec'],
        tasks: [
          {
            node_id: 'task-barespec',
            task_id: 'task-barespec',
            sequence: 1,
            status: 'DONE',
            task_spec: {},
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    // context/metadata/objective 全缺 → title 回退 d.task_id
    expect(task.name).toBe('task-barespec');
    expect(task.goal).toBe('');
    expect(task.objective).toBe('');
    expect(task.acceptances).toEqual([]);
  });

  it('goal 存在但无 objective 与 acceptances 时,task.goal/objective 走空串兜底', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-empty-goal',
        // 顶层与节点 task_spec 的 goal 均无 objective/acceptances 字段
        task_spec: { goal: {} } as TaskDashboardResponse['task_spec'],
        tasks: [
          {
            node_id: 'task-empty-goal',
            task_id: 'task-empty-goal',
            sequence: 1,
            status: 'DONE',
            task_spec: { goal: {} },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.goal).toBe('');
    expect(task.objective).toBe('');
    expect(task.acceptances).toEqual([]);
  });

  it('task_id 缺省且根节点无 task_id 时,id 回退 rootNode.node_id;全空时为空串', () => {
    // 根节点有 node_id 但无 task_id → id 第二段 ?? 命中 rootNode.node_id
    const withIdNode = mapDashboard(
      buildDashboard({
        task_id: undefined as unknown as string,
        task_spec: {} as TaskDashboardResponse['task_spec'],
        tasks: [
          {
            node_id: 'only-node-id',
            // 不下发 task_id
            sequence: 1,
            status: 'DONE',
            task_spec: {},
          } as unknown as TaskDashboardResponse['tasks'][number],
        ],
      }),
    );
    expect(withIdNode.id).toBe('only-node-id');

    // 无根节点 → title 链最终回退 '',id 最终为 ''
    const empty = mapDashboard(
      buildDashboard({
        task_id: undefined as unknown as string,
        task_spec: {} as TaskDashboardResponse['task_spec'],
        tasks: [] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(empty.name).toBe('');
    expect(empty.id).toBe('');
  });
});

describe('taskPanelMapper template 三种取值分支', () => {
  it('execution_config.workflow_id 存在 → template = workflow_id', () => {
    const task = mapDashboard(
      buildDashboard({ execution_config: { workflow_id: 'wf-xyz' } }),
    );
    expect(task.template).toBe('wf-xyz');
  });
  it('execution_config 仅 yaml 非空 → template = "yaml"', () => {
    const task = mapDashboard(
      buildDashboard({ execution_config: { yaml: 'name: t' } }),
    );
    expect(task.template).toBe('yaml');
  });
  it('execution_config 为空对象 → template = null', () => {
    const task = mapDashboard(
      buildDashboard({ execution_config: {} }),
    );
    expect(task.template).toBeNull();
  });
});

describe('taskPanelMapper taskTypeLabel / sourceLabel 兜底分支', () => {
  it('task_type 与 source_type 均非已知枚举 → 透出原值兜底', () => {
    const task = mapDashboard(
      buildDashboard({
        execution_config: { task_type: 'weird' },
        source_type: 'weird' as TaskDashboardResponse['source_type'],
      }),
    );
    expect(task.taskType).toBe('weird');
    expect(task.taskTypeLabel).toBe('weird');
    expect(task.sourceLabel).toBe('weird');
  });
});

describe('taskPanelMapper acceptance_result / sequence 兜底分支', () => {
  it('acceptance_result 缺 done_items/gap_items 且节点缺 sequence → 回退空数组与 0', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            node_id: 'node-ac',
            task_id: 'task-branch',
            // sequence 缺省
            status: 'DONE',
            run_info: { acceptance_result: { verdict: 'DONE' } },
            task_spec: { metadata: { title: '验收' }, goal: { objective: '', acceptances: [] } },
          } as unknown as TaskDashboardResponse['tasks'][number],
        ],
      }),
    );
    expect(task.nodes[0]!.sequence).toBe(0);
    expect(task.nodes[0]!.acceptanceResult).toEqual({ verdict: 'DONE', doneItems: [], gapItems: [] });
  });
});

describe('taskPanelMapper graphTasks/relations 缺省分支', () => {
  it('tasks 与 relations 均缺省 → 节点/DAG 为空', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: undefined as unknown as TaskDashboardResponse['tasks'],
        relations: undefined as unknown as TaskDashboardResponse['relations'],
      }),
    );
    expect(task.nodes).toHaveLength(0);
    expect(task.dagNodes).toHaveLength(0);
    expect(task.dagEdges).toHaveLength(0);
  });
});

describe('taskPanelMapper 顶层 output 非空分支', () => {
  it('d.output 非空 → graphLevelOutput 取 d.output,产物维度按其分维度展开', () => {
    const task = mapDashboard(
      buildDashboard({
        task_type: 'dynamic',
        // 无根节点 output,强制走 graphLevelOutput(d.output)
        tasks: [
          {
            node_id: 'node-empty',
            task_id: 'task-branch',
            sequence: 1,
            status: 'DONE',
            run_info: {},
            task_spec: { metadata: { title: '空' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
        // 多一个非 result/data 维度,防止 unwrapHttpEnvelope 把 {result} 当信封剥掉
        output: { result: { output: '顶层产物结果' }, tag: 'keep' } as TaskDashboardResponse['output'],
      }),
    );
    expect(task.rootOutputDimensions?.map((d) => d.key)).toEqual(['result', 'tag']);
    expect(task.rootOutputDimensions?.find((d) => d.key === 'result')?.content).toBe('顶层产物结果');
  });
});

describe('taskPanelMapper resolveNodeExecutor 对象 assignee 末端分支', () => {
  it('run_mode 非 single_bot/coop_group 时,对象 assignee 走 binding 兜底链', () => {
    const task = mapDashboard(
      buildDashboard({
        tasks: [
          {
            // run_mode=bbs → 不命中 single_bot/coop_group 提前返回,落到 L209-215 绑定回退
            node_id: 'node-bbs-obj',
            task_id: 'task-branch',
            sequence: 1,
            status: 'RUNNING',
            run_info: {
              run_mode: 'bbs',
              assignee: { type: 'bot', binding: 'grp-binding-y' },
              extend_props: { bot_id: 'fallback-bot' },
            },
            task_spec: { metadata: { title: 'bbs 对象执行' }, goal: { objective: '', acceptances: [] } },
          },
          {
            // 对象 assignee 无 binding → 走 binding??null 的 null 分支,再回退到 extend_props.bot_id
            node_id: 'node-bbs-nobinding',
            task_id: 'task-branch',
            sequence: 2,
            status: 'RUNNING',
            run_info: {
              run_mode: 'bbs',
              assignee: { type: 'bot' },
              extend_props: { bot_id: 'fallback-bot-2' },
            },
            task_spec: { metadata: { title: '对象无 binding' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    // 有 binding → executor/assignee 取 binding
    expect(task.nodes.find((n) => n.id === 'node-bbs-obj')!.assignee).toBe('grp-binding-y');
    expect(task.nodes.find((n) => n.id === 'node-bbs-obj')!.executor).toBe('grp-binding-y');
    // 无 binding → 回退到 extend_props.bot_id
    expect(task.nodes.find((n) => n.id === 'node-bbs-nobinding')!.executor).toBe('fallback-bot-2');
  });
});

describe('taskPanelMapper 投放实施节点产物维度为空分支', () => {
  it('投放实施节点 DONE 但 output 不含 result 维度 → implDimensions 空,rootOutputRender 为 null', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-impl-empty',
        tasks: [
          {
            node_id: 'node-impl',
            task_id: 'task-impl-empty',
            sequence: 1,
            status: 'DONE',
            // output 顶层只有 handoff,无 result;onlyResult 过滤后维度为空
            run_info: { output: { handoff: { summary: '内部交接内容' } } },
            task_spec: { metadata: { title: '投放实施', instruction: '' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    // implDimensions 空 → rootOutputRender 走 ?? null 兜底
    expect(task.rootOutputRender).toBeNull();
  });
});

describe('taskPanelMapper DAG 边 ghost 节点过滤分支', () => {
  it('relation 指向不存在的节点时,被 layoutDag 与 dagEdges 双重过滤', () => {
    const task = mapDashboard(
      buildDashboard({
        task_id: 'task-ghost',
        tasks: [
          {
            node_id: 'node-real',
            task_id: 'task-ghost',
            sequence: 1,
            status: 'RUNNING',
            task_spec: { metadata: { title: '真实' }, goal: { objective: '', acceptances: [] } },
          },
        ] as TaskDashboardResponse['tasks'],
        relations: [
          { src_id: 'node-real', dst_id: 'ghost-node', type: 'DEPENDENCY' },
          { src_id: 'ghost-src', dst_id: 'node-real', type: 'DEPENDENCY' },
        ] as TaskDashboardResponse['relations'],
      }),
    );
    expect(task.dagNodes.map((n) => n.id)).toEqual(['node-real']);
    expect(task.dagEdges).toHaveLength(0);
  });
});
