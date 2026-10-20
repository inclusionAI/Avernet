import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { mapDashboard, mapTaskStatus } from '@/assets/TaskPanel/taskPanelMapper';
import { describe, expect, it } from '@jest/globals';

describe('taskPanelMapper status mapping', () => {
  it('兼容旧运行时状态和新的产品层状态', () => {
    expect(mapTaskStatus('HUNG')).toBe('REVIEWING');
    expect(mapTaskStatus('REVIEWING')).toBe('REVIEWING');
    expect(mapTaskStatus('DONE')).toBe('DONE');
    expect(mapTaskStatus('SUCCESS')).toBe('DONE');
    expect(mapTaskStatus('FAILED')).toBe('FAILED');
    expect(mapTaskStatus('CANCELLED')).toBe('CANCELLED');
    expect(mapTaskStatus('PENDING')).toBe('DEFINED');
    expect(mapTaskStatus('PLANNING')).toBe('EXECUTING');
    expect(mapTaskStatus('RUNNING')).toBe('EXECUTING');
    expect(mapTaskStatus('UNKNOWN')).toBe('EXECUTING');
  });
});

describe('taskPanelMapper node SUCCESS mapping', () => {
  it('节点 SUCCESS 映射为 done，而不是 pending', () => {
    const dashboard = {
      task_id: 'task-success-node',
      run_id: 1,
      status: 'SUCCESS',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: { metadata: { title: '任务' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: '2026-08-22T10:01:00+08:00',
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 0,
        done: 1,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-success',
          task_id: 'task-success-node',
          sequence: 1,
          status: 'SUCCESS',
          task_spec: { metadata: { title: '成功节点' }, goal: { objective: '目标', acceptances: [] } },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    expect(mapDashboard(dashboard).nodes[0].status).toBe('done');
  });
});

describe('taskPanelMapper task description', () => {
  it('任务描述使用 task_spec.context.background，不使用 metadata.instruction', () => {
    const dashboard = {
      task_id: 'task-description',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '任务标题', instruction: '不应展示的 instruction' },
        context: { background: '任务背景描述' },
        goal: { objective: '任务目标', acceptances: [] },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'node-description',
          task_id: 'task-description',
          sequence: 1,
          status: 'RUNNING',
          task_spec: {
            metadata: { title: '任务标题', instruction: '不应展示的 instruction' },
            context: { background: '任务背景描述' },
            goal: { objective: '任务目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    expect(mapDashboard(dashboard).description).toBe('任务背景描述');
  });
});

describe('taskPanelMapper Task Spec', () => {
  it('协作群节点优先展示自身 group_name，缺失时显示 BCS协作群(非根不继承根节点 group_name)', () => {
    const dashboard = {
      task_id: 'task-group',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'coop_group',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: { metadata: { title: '任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 2, pending: 2, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'node-group-named',
          task_id: 'task-group',
          sequence: 1,
          status: 'PENDING',
          run_info: {
            run_mode: 'coop_group',
            assignee: 'bcs_grp_123',
            assignee_name: '自动研发Bot',
            extend_props: { group_id: 'bcs_grp_123', group_name: '业务架构分析群' },
          },
          task_spec: {
            metadata: { title: '无人任务研发承接', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-group-fallback',
          task_id: 'task-group',
          sequence: 2,
          status: 'PENDING',
          run_info: { run_mode: 'coop_group', assignee: 'bcs_grp_456', extend_props: { group_id: 'bcs_grp_456' } },
          task_spec: {
            metadata: { title: '营销策略进一步讨论', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-risk-review',
          task_id: 'task-group',
          sequence: 3,
          status: 'PENDING',
          run_info: { run_mode: 'coop_group', assignee: 'bcs_grp_789', extend_props: { group_id: 'bcs_grp_789' } },
          task_spec: {
            metadata: { title: '风险评审', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.nodes[0].groupName).toBe('业务架构分析群');
    expect(task.nodes[0].name).toBe('无人任务研发承接');
    expect(task.nodes[0].executor).toBe('自动研发Bot');
    expect(task.nodes[1].groupName).toBe('BCS协作群');
    expect(task.nodes[1].name).toBe('营销策略进一步讨论');
    expect(task.nodes[2].name).toBe('风险评审');
    expect(task.nodes[1].executor).toBeNull();
  });

  it('兼容验收项 acceptance 字段并映射全部验收标准', () => {
    const dashboard = {
      task_id: 'task-1',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '任务标题', instruction: '执行指令' },
        goal: {
          objective: '任务目标',
          acceptances: [
            { id: 'ac-1', acceptance: '验收项一' },
            { id: 'ac-2', acceptance: '验收项二' },
          ],
        },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 1,
        done: 0,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 0,
      },
      tasks: [
        {
          node_id: 'node-1',
          task_id: 'task-1',
          sequence: 1,
          status: 'RUNNING',
          run_info: {
            run_mode: 'single_bot',
            assignee: '20260823_bot_id',
            extend_props: {
              assignee_name: '自动研发Bot',
            },
          },
          task_spec: {
            metadata: { title: '节点标题', instruction: '节点指令' },
            goal: {
              objective: '节点目标',
              acceptances: [
                { id: 'node-ac-1', acceptance: '节点验收项一' },
                { id: 'node-ac-2', acceptance: '节点验收项二' },
              ],
            },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);

    expect(task.nodes[0].executor).toBe('自动研发Bot');
    expect(task.nodes[0].taskSpec).toEqual({
      title: '节点标题',
      instruction: '节点指令',
      target: '节点目标',
      acceptances: ['节点验收项一', '节点验收项二'],
    });
  });
});

describe('taskPanelMapper root graph-level fallbacks', () => {
  it('根节点缺少 assignee/run_mode/session 时使用 graph extend_props 兜底', () => {
    const dashboard = {
      task_id: 'task-root-fallback',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-top-level',
      extend_props: {
        owner_bot_id: 'bot-from-graph',
        source_type: 'coop_group',
        main_session_id: 'bcs_grp_session:round-1',
      },
      task_spec: {
        metadata: { title: '根节点兜底任务', instruction: '执行' },
        context: { background: '', extend_props: {} },
        goal: { objective: '目标', acceptances: [] },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 1, pending: 1, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'task-root-fallback',
          task_id: 'task-root-fallback',
          sequence: 1,
          status: 'PENDING',
          run_info: { extend_props: {} },
          task_spec: {
            metadata: { title: '根节点兜底任务', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const root = mapDashboard(dashboard).nodes[0];
    expect(root.assignee).toBe('bot-from-graph');
    expect(root.runMode).toBe('coop_group');
    expect(root.sessionId).toBe('bcs_grp_session:round-1');
  });
});

describe('taskPanelMapper root output dimensions (产物输出处理)', () => {
  it('根节点 output 为结构化对象时按顶层维度拆成多张卡片，优先读取 summary', () => {
    const dashboard = {
      task_id: 'task-okr-output',
      run_id: 1,
      status: 'DONE',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '18 周年店庆 OKR', instruction: '执行' },
        goal: { objective: '目标', acceptances: [] },
      },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 0,
        done: 1,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-root',
          task_id: 'task-okr-output',
          sequence: 1,
          status: 'DONE',
          run_info: {
            output: {
              实施计划: { summary: '客流与护理双增长实施计划摘要' },
              阶段性结果: { summary: '新客到店 1500、券核销 1000' },
            },
          },
          task_spec: {
            metadata: { title: '任务接收', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.rootOutputDimensions).toHaveLength(2);
    expect(task.rootOutputDimensions?.map((d) => d.key)).toEqual(['实施计划', '阶段性结果']);
    expect(task.rootOutputDimensions?.[0].content).toBe('客流与护理双增长实施计划摘要');
    expect(task.rootOutputDimensions?.[1].content).toBe('新客到店 1500、券核销 1000');
  });

  it('维度内容优先取 $.<key>.output，兼容 $.<key> 直字符串与 JSON 信封展开', () => {
    const dashboard = {
      task_id: 'task-output-field',
      run_id: 1,
      status: 'DONE',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '存储行业分析', instruction: '执行' },
        goal: { objective: '目标', acceptances: [] },
      },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 0,
        done: 1,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-root',
          task_id: 'task-output-field',
          sequence: 1,
          status: 'DONE',
          run_info: {
            output: {
              sub_tech_customers: { output: '## 技术演进与客户需求分析\n\n双维度均完整通过验收。' },
              sub_investment_judgment: { output: '存储行业投资价值判断报告已完成。' },
              'bbs-e5cb1eaf': { output: '{"result":"尽调报告已生成并保存"}' },
              plain_dimension: '直接字符串维度的产出内容',
            },
          },
          task_spec: {
            metadata: { title: '任务接收', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.rootOutputDimensions?.map((d) => d.key)).toEqual([
      'sub_tech_customers',
      'sub_investment_judgment',
      'bbs-e5cb1eaf',
      'plain_dimension',
    ]);
    // $.<key>.output 为 markdown/文本 → 原样保留
    expect(task.rootOutputDimensions?.[0].content).toBe('## 技术演进与客户需求分析\n\n双维度均完整通过验收。');
    expect(task.rootOutputDimensions?.[1].content).toBe('存储行业投资价值判断报告已完成。');
    // $.<key>.output 为 JSON 信封字符串 → 展开取 result 具体值
    expect(task.rootOutputDimensions?.[2].content).toBe('尽调报告已生成并保存');
    // $.<key> 本身为字符串 → 直接作为内容
    expect(task.rootOutputDimensions?.[3].content).toBe('直接字符串维度的产出内容');
  });

  it('分布式接力产物始终聚合所有节点，即使根节点已有部分产出', () => {
    const dashboard = {
      task_id: 'task-relay-output',
      run_id: 1,
      status: 'DONE',
      loop_round: 2,
      execution_config: { orchestration_mode: 'relay' },
      tasks: [
        {
          node_id: 'node-root',
          task_id: 'task-relay-output',
          sequence: 1,
          status: 'DONE',
          run_info: { output: { result: '根棒环境核验产出' } },
          task_spec: {
            context: { title: '存储行业尽调', background: '' },
            goal: { objective: '产出尽调报告', acceptances: [] },
          },
        },
        {
          node_id: 'relay-research',
          task_id: 'task-relay-output',
          sequence: 2,
          status: 'DONE',
          run_info: { output: { result: '# 尽调报告\n\n完整正文' } },
          task_spec: {
            context: { title: '行业研究' },
            goal: { objective: '完成尽调报告', acceptances: [] },
          },
        },
        {
          node_id: 'relay-verify',
          task_id: 'task-relay-output',
          sequence: 3,
          status: 'SUCCESS',
          run_info: { output: { result: '近期数据验证补充报告' } },
          task_spec: {
            context: { title: '数据验证' },
            goal: { objective: '补齐近期数据', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.rootOutputDimensions?.map((item) => item.key)).toEqual(['node-root', 'relay-research', 'relay-verify']);
    expect(task.rootOutputDimensions?.[0].content).toBe('根棒环境核验产出');
    expect(task.rootOutputDimensions?.[1].content).toBe('# 尽调报告\n\n完整正文');
    expect(task.rootOutputDimensions?.[2].content).toBe('近期数据验证补充报告');
  });

  it('根节点 output 为非结构化(字符串/null)时不产生维度卡片，回退 markdown 渲染', () => {
    const dashboard = {
      task_id: 'task-plain',
      run_id: 1,
      status: 'DONE',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: { metadata: { title: '普通任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 0,
        done: 1,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-root',
          task_id: 'task-plain',
          sequence: 1,
          status: 'DONE',
          run_info: { output: '一段纯文本产出' },
          task_spec: {
            metadata: { title: '任务接收', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.rootOutputDimensions).toBeUndefined();
    expect(task.rootOutputRender).toContain('一段纯文本产出');
  });

  it('存在已完成的「投放实施」节点时，产物 Tab 绑定该节点 output(维度来源与渲染源均取自该节点)', () => {
    const dashboard = {
      task_id: 'task-okr-multi',
      run_id: 1,
      status: 'DONE',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '18 周年店庆 OKR', instruction: '执行' },
        goal: { objective: '目标', acceptances: [] },
      },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 2,
        pending: 0,
        planning: 0,
        running: 0,
        done: 2,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-root',
          task_id: 'task-okr-multi',
          sequence: 1,
          status: 'DONE',
          run_info: { output: { 根节点概览: { summary: '根节点产出概览' } } },
          task_spec: {
            metadata: { title: '任务接收', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-impl',
          task_id: 'task-okr-multi',
          sequence: 2,
          status: 'DONE',
          run_info: {
            output: {
              result: {
                summary:
                  '投放实施执行报告:体验券分批投放 50 张、传单 3 公里覆盖、展位布置、社群定时推送;护理套餐放量与扩产能待审批;低价引流款挂起;到店核销去重工具缺口转 BBS 研发接力。',
                random: '432251',
              },
              handoff: { summary: '不应作为最终产物输出的 handoff 重复内容', random: '432251' },
            },
          },
          task_spec: {
            metadata: { title: '投放实施', instruction: '执行投放' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [{ src_id: 'node-root', dst_id: 'node-impl', type: 'DEPENDENCY' }],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    // 产物定制:投放实施节点 output 仅取 `result` 维度,过滤 handoff 等非 result 顶层字段
    expect(task.rootOutputDimensions?.map((d) => d.key)).toEqual(['result']);
    expect(task.rootOutputDimensions?.[0].content).toBe(
      '投放实施执行报告:体验券分批投放 50 张、传单 3 公里覆盖、展位布置、社群定时推送;护理套餐放量与扩产能待审批;低价引流款挂起;到店核销去重工具缺口转 BBS 研发接力。',
    );
    expect(task.rootOutputDimensions?.some((d) => d.key === 'handoff')).toBe(false);
    // 渲染源也收敛到 `result` 维度内容,不含根节点概览、也不带出 handoff
    expect(task.rootOutputRender).toContain('投放实施执行报告');
    expect(task.rootOutputRender).not.toContain('根节点产出概览');
    expect(task.rootOutputRender).not.toContain('handoff');
  });
});

describe('taskPanelMapper MISS(派发未命中)节点', () => {
  it('MISS 节点不继承根节点 session、不回退任务归属 bot 当执行人', () => {
    const dashboard = {
      task_id: 'task-miss',
      run_id: 1,
      status: 'EXECUTING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'single_bot',
      owner_user_id: 'user-1',
      owner_bot_id: '20260826_20rphqo0',
      task_spec: { metadata: { title: '主任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 2, pending: 1, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      extend_props: { owner_bot_id: '20260826_20rphqo0', owner_user_id: 'user-1' },
      tasks: [
        {
          node_id: 'task-miss',
          task_id: 'task-miss',
          sequence: 1,
          status: 'EXECUTING',
          run_info: {
            run_mode: 'single_bot',
            assignee: '20260826_20rphqo0',
            assignee_name: '金庸',
            extend_props: { session_id: 'agent:main:session:abc-123:user:146836', assignee_owner_id: '146836' },
          },
          task_spec: {
            metadata: { title: '主任务', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'comp_market_share',
          task_id: 'task-miss',
          sequence: 2,
          status: 'REVIEWING',
          run_info: {
            run_mode: null,
            assignee: null,
            extend_props: { miss_events: ['rule_single_candidate_random_miss'], hung_reason: 'claim_on 筛选未命中' },
          },
          task_spec: {
            metadata: { title: '市场份额分析', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [{ src_id: 'task-miss', dst_id: 'comp_market_share', type: 'DEPENDENCY' }],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    const missNode = task.nodes.find((n) => n.id === 'comp_market_share');
    expect(missNode).toBeDefined();
    // 透出 miss 事件与挂起原因
    expect(missNode!.missEvents).toEqual(['rule_single_candidate_random_miss']);
    expect(missNode!.hungReason).toBe('claim_on 筛选未命中');
    // 未真正分配 bot:executor/assignee 为空,runMode 为空
    expect(missNode!.executor).toBeNull();
    expect(missNode!.assignee).toBeNull();
    expect(missNode!.runMode).toBeNull();
    // 非根节点不继承根节点 session:无自身 session 时为 null → 不可下钻。
    expect(missNode!.sessionId).toBeNull();
    expect(missNode!.groupId).toBeNull();
    // 根节点 session 不受影响
    const rootNode = task.nodes.find((n) => n.id === 'task-miss');
    expect(rootNode!.sessionId).toBe('agent:main:session:abc-123:user:146836');
  });

  it('miss_events 非空但 assignee 已派发(首派未命中后回退成功)时按正常已派发节点处理', () => {
    const groupId = 'bcs_grp_f4d2eceff8e54b4381e3d06fa9c6f6d0';
    const dashboard = {
      task_id: 'task-miss2',
      run_id: 1,
      status: 'EXECUTING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'single_bot',
      owner_user_id: 'user-1',
      owner_bot_id: '20260826_20rphqo0',
      task_spec: { metadata: { title: '主任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 2, pending: 0, planning: 0, running: 2, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      extend_props: { owner_bot_id: '20260826_20rphqo0', owner_user_id: 'user-1' },
      tasks: [
        {
          node_id: 'task-miss2',
          task_id: 'task-miss2',
          sequence: 1,
          status: 'EXECUTING',
          run_info: { run_mode: 'single_bot', assignee: '20260826_20rphqo0', assignee_name: '金庸' },
          task_spec: {
            metadata: { title: '主任务', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'sub_entry_opportunity',
          task_id: 'task-miss2',
          sequence: 2,
          status: 'EXECUTING',
          run_info: {
            run_mode: 'coop_group',
            assignee: groupId,
            extend_props: {
              miss_events: ['rule_single_candidate_random_miss'],
              assignee_owner_id: '146836',
              assignee_name: '技术栈概览Bot',
              actual_run_mode: 'coop_group',
              group_id: groupId,
              session_id: `${groupId}:a612806b`,
            },
          },
          task_spec: {
            metadata: { title: '存储行业进入机会分析', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [{ src_id: 'task-miss2', dst_id: 'sub_entry_opportunity', type: 'DEPENDENCY' }],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    const node = task.nodes.find((n) => n.id === 'sub_entry_opportunity');
    expect(node).toBeDefined();
    // miss_events 仍透出(元数据),但节点已派发:assignee/runMode 存在
    expect(node!.missEvents).toEqual(['rule_single_candidate_random_miss']);
    expect(node!.assignee).toBe(groupId);
    expect(node!.runMode).toBe('coop_group');
    // 保留自身 session/group(不因 miss_events 被清空)
    expect(node!.sessionId).toBe(`${groupId}:a612806b`);
    expect(node!.groupId).toBe(groupId);
    expect(node!.groupName).toBe('BCS协作群');
  });
});

describe('taskPanelMapper actual_run_mode 覆盖(run_mode 权限绕过)', () => {
  it('actual_run_mode 存在时覆盖 run_mode:真实模式驱动展示,下钻通道仍按物理群 session 保留', () => {
    const groupId = 'bcs_grp_actual_override';
    const dashboard = {
      task_id: 'task-actual',
      run_id: 1,
      status: 'EXECUTING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'coop_group',
      owner_user_id: 'user-1',
      owner_bot_id: '20260826_20rphqo0',
      task_spec: { metadata: { title: '主任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 2, pending: 0, planning: 0, running: 2, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      extend_props: { owner_bot_id: '20260826_20rphqo0', owner_user_id: 'user-1' },
      tasks: [
        {
          node_id: 'task-actual',
          task_id: 'task-actual',
          sequence: 1,
          status: 'EXECUTING',
          run_info: { run_mode: 'coop_group', assignee: '20260826_20rphqo0', assignee_name: '金庸' },
          task_spec: {
            metadata: { title: '主任务', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'single_bot_via_group',
          task_id: 'task-actual',
          sequence: 2,
          status: 'EXECUTING',
          run_info: {
            // 权限绕过:run_mode 被统一改成 coop_group,真实模式落在 extend_props.actual_run_mode
            run_mode: 'coop_group',
            assignee: '20260826_20rphqo0',
            assignee_name: '技术栈概览Bot',
            extend_props: {
              actual_run_mode: 'single_bot',
              group_id: groupId,
              session_id: `${groupId}:a612806b`,
              assignee_bot_id: '20260826_20rphqo0',
            },
          },
          task_spec: {
            metadata: { title: '技术栈概览', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [{ src_id: 'task-actual', dst_id: 'single_bot_via_group', type: 'DEPENDENCY' }],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    const node = task.nodes.find((n) => n.id === 'single_bot_via_group');
    expect(node).toBeDefined();
    // actual_run_mode 覆盖 run_mode:真实执行模式为单 bot
    expect(node!.runMode).toBe('single_bot');
    // 真实单 bot → 执行者展示 assignee_name(bot 名)
    expect(node!.executor).toBe('技术栈概览Bot');
    expect(node!.assigneeName).toBe('技术栈概览Bot');
    // 下钻通道仍按物理群 session 保留(单 bot 绕过群执行,群元数据不被清空)
    expect(node!.groupId).toBe(groupId);
    expect(node!.sessionId).toBe(`${groupId}:a612806b`);
  });

  it('actual_run_mode 为非法/未知值时降级回 run_mode,不污染下游单/群判别', () => {
    const groupId = 'bcs_grp_invalid_actual';
    const dashboard = {
      task_id: 'task-invalid-actual',
      run_id: 1,
      status: 'EXECUTING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'coop_group',
      owner_user_id: 'user-1',
      owner_bot_id: '20260826_20rphqo0',
      task_spec: { metadata: { title: '主任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-09-01T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      extend_props: { owner_bot_id: '20260826_20rphqo0', owner_user_id: 'user-1' },
      tasks: [
        {
          node_id: 'task-invalid-actual',
          task_id: 'task-invalid-actual',
          sequence: 1,
          status: 'EXECUTING',
          run_info: {
            run_mode: 'coop_group',
            assignee: groupId,
            extend_props: { actual_run_mode: 'unknown_mode', group_id: groupId, session_id: `${groupId}:round1` },
          },
          task_spec: { metadata: { title: '节点', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
        },
      ],
      relations: [],
    } as unknown as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    const node = task.nodes[0];
    // 非法 actual_run_mode 降级 → 沿用 run_mode=coop_group
    expect(node.runMode).toBe('coop_group');
    expect(node.groupId).toBe(groupId);
  });
});

describe('taskPanelMapper canonical TaskSpec', () => {
  it('节点标题优先读取 context.title，且不依赖 legacy metadata', () => {
    const dashboard = {
      task_id: 'task-context-title',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        context: { title: '根任务标题', background: '', extend_props: {} },
        goal: { objective: '根目标', acceptances: [] },
      },
      create_time: '2026-09-21T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'task-context-title',
          task_id: 'task-context-title',
          sequence: 0,
          status: 'DONE',
          task_spec: {
            context: { title: '根任务标题', background: '', extend_props: {} },
            goal: { objective: '根目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-1',
          task_id: 'task-context-title',
          sequence: 1,
          status: 'RUNNING',
          task_spec: {
            context: { title: '下一棒标题', background: '', extend_props: {} },
            goal: { objective: '下一棒目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.name).toBe('根任务标题');
    expect(task.nodes[1]!.name).toBe('下一棒标题');
    expect(task.nodes[1]!.taskSpec!.title).toBe('下一棒标题');
    expect(task.nodes[1]!.taskSpec!.target).toBe('下一棒目标');
  });
});

// ── Task 模块 V2 升级:派生接线(statusDerivation/artifactDescriptor/session 字段透传) ──

describe('taskPanelMapper V2 derivation wiring', () => {
  const buildDashboard = (overrides?: Partial<TaskDashboardResponse>): TaskDashboardResponse =>
    ({
      task_id: 'task-v2',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: true,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-owner',
      task_spec: { metadata: { title: 'V2 任务' }, goal: { objective: '目标', acceptances: [] } },
      main_session: { session_id: 'agent:main:session:s-main', name: '主单聊', source_type: 'bot', owner_bot_id: 'bot-owner', source_group_id: null },
      derived_session_ids: [],
      create_time: '2026-10-20T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 3, pending: 1, planning: 0, running: 1, done: 1, failed: 0, hung: 0, skipped: 0, percent: 33 },
      tasks: [],
      relations: [],
      ...overrides,
    }) as TaskDashboardResponse;

  it('节点 present/rework 派生:执行中+多 attempt+验收非 DONE', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [
        {
          node_id: 'n-planner', task_id: 'task-v2', sequence: 1, status: 'DONE',
          task_spec: { metadata: { title: '规划' }, goal: { objective: '', acceptances: [] } },
          relations: [],
        },
        {
          node_id: 'n-worker', task_id: 'task-v2', sequence: 2, status: 'RUNNING',
          task_spec: { metadata: { title: '执行' }, goal: { objective: '', acceptances: [] } },
          run_info: {
            run_mode: 'coop_group',
            action_log: [
              { seq: 1, ts: 1700000000000, action: 'dispatch', attempt: 1 },
              { seq: 2, ts: 1700000001000, action: 'rework', attempt: 2, status_to: 'REWORK_RERUN', payload: { description: 'Owner Bot 裁决 revise: 返工' } },
            ],
            acceptance_result: { verdict: 'FAILED', done_items: [], gap_items: ['缺定价对比'] },
          },
        },
      ] as TaskDashboardResponse['tasks'],
      relations: [
        { src_id: 'n-planner', dst_id: 'n-worker', type: 'DEPENDENCY' },
      ] as TaskDashboardResponse['relations'],
    }));
    const worker = task.nodes.find((n) => n.id === 'n-worker')!;
    expect(worker.present).toBe('rework');
    expect(worker.attemptCount).toBe(2);
    expect(worker.needsHitl).toBe(true);
    // planner DONE 且下游 worker 已在执行(消费其输出)→ merged;无下游的 DONE 节点 → done 见下例
    expect(task.nodes.find((n) => n.id === 'n-planner')!.present).toBe('merged');
  });

  it('merged 派生:DONE 节点存在已完成的直接下游', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [
        { node_id: 'n-a', task_id: 'task-v2', sequence: 1, status: 'DONE', task_spec: { metadata: { title: 'A' }, goal: { objective: '', acceptances: [] } } },
        { node_id: 'n-b', task_id: 'task-v2', sequence: 2, status: 'DONE', task_spec: { metadata: { title: 'B' }, goal: { objective: '', acceptances: [] } } },
      ] as TaskDashboardResponse['tasks'],
      relations: [{ src_id: 'n-a', dst_id: 'n-b', type: 'DEPENDENCY' }] as TaskDashboardResponse['relations'],
    }));
    expect(task.nodes.find((n) => n.id === 'n-a')!.present).toBe('merged');
    expect(task.nodes.find((n) => n.id === 'n-b')!.present).toBe('done');
    expect(task.dagNodes.find((n) => n.id === 'n-a')!.present).toBe('merged');
  });

  it('drillable:协作群节点 true,单聊节点 false', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [
        { node_id: 'n-solo', task_id: 'task-v2', sequence: 1, status: 'RUNNING', task_spec: { metadata: { title: '单聊' }, goal: { objective: '', acceptances: [] } } },
        {
          node_id: 'n-group', task_id: 'task-v2', sequence: 2, status: 'RUNNING',
          task_spec: { metadata: { title: '群节点' }, goal: { objective: '', acceptances: [] } },
          run_info: { run_mode: 'coop_group' },
        },
      ] as TaskDashboardResponse['tasks'],
      relations: [{ src_id: 'n-solo', dst_id: 'n-group', type: 'DEPENDENCY' }] as TaskDashboardResponse['relations'],
    }));
    expect(task.nodes.find((n) => n.id === 'n-solo')!.drillable).toBe(false);
    expect(task.nodes.find((n) => n.id === 'n-group')!.drillable).toBe(true);
    expect(task.dagNodes.find((n) => n.id === 'n-group')!.drillable).toBe(true);
  });

  it('execDag 由 action_log 派生注入节点', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [{
        node_id: 'n-exec', task_id: 'task-v2', sequence: 1, status: 'RUNNING',
        task_spec: { metadata: { title: 'E' }, goal: { objective: '', acceptances: [] } },
        run_info: {
          action_log: [
            { seq: 1, ts: 1700000000000, action: 'accept_context', attempt: 1 },
            { seq: 2, ts: 1700000001000, action: 'self_check', attempt: 1 },
          ],
        },
      }] as TaskDashboardResponse['tasks'],
    }));
    const exec = task.nodes[0]!.execDag!;
    expect(exec.nodes.map((n) => n.id)).toEqual(['ev-1', 'ev-2']);
    expect(exec.edges).toEqual([{ from: 'ev-1', to: 'ev-2', semantics: 'none' }]);
  });

  it('dagEdges 语义/reason:fan-out → split_work;返工节点入边 → rework', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [
        { node_id: 'n1', task_id: 'task-v2', sequence: 1, status: 'DONE', task_spec: { metadata: { title: '一' }, goal: { objective: '', acceptances: [] } } },
        { node_id: 'n2', task_id: 'task-v2', sequence: 2, status: 'DONE', task_spec: { metadata: { title: '二' }, goal: { objective: '', acceptances: [] } } },
        {
          node_id: 'n3', task_id: 'task-v2', sequence: 3, status: 'RUNNING',
          task_spec: { metadata: { title: '三' }, goal: { objective: '', acceptances: [] } },
          run_info: { action_log: [{ seq: 5, ts: 1700000003000, action: 'rework', attempt: 2, payload: { description: '第 2 次返工' } }], acceptance_result: { verdict: 'FAILED', done_items: [], gap_items: [] } },
        },
      ] as TaskDashboardResponse['tasks'],
      relations: [
        { src_id: 'n1', dst_id: 'n2', type: 'DEPENDENCY' },
        { src_id: 'n1', dst_id: 'n3', type: 'DEPENDENCY' },
      ] as TaskDashboardResponse['relations'],
    }));
    const byKey = new Map(task.dagEdges.map((e) => [`${e.from}->${e.to}`, e]));
    expect(byKey.get('n1->n2')?.semantics).toBe('split_work');
    expect(byKey.get('n1->n3')?.semantics).toBe('rework');
    expect(byKey.get('n1->n3')?.reason).toBe('第 2 次返工');
  });

  it('工具节点 dagNodes.short 生成(名超 8 字截断加省略号)', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [{
        node_id: 'n-long', task_id: 'task-v2', sequence: 1, status: 'RUNNING',
        task_spec: { context: { title: '这是一个名字特别长的节点标题要截断' }, goal: { objective: '', acceptances: [] } },
      }] as TaskDashboardResponse['tasks'],
    }));
    const short = task.dagNodes.find((n) => n.id === 'n-long')?.short!;
    expect(short.endsWith('…')).toBe(true);
    expect(short.length).toBeLessThanOrEqual(9);
  });

  it('TaskView 透传 mainSessionId/sourceGroupId/derivedSessionIds + 节点原始时间戳', () => {
    let task = mapDashboard(buildDashboard());
    expect(task.mainSessionId).toBe('agent:main:session:s-main');
    expect(task.sourceGroupId).toBeNull();
    expect(task.derivedSessionIds).toEqual([]);

    task = mapDashboard(buildDashboard({
      main_session: { session_id: 's-main', name: '主', source_type: 'coop_group', owner_bot_id: 'bot-owner', source_group_id: 'g-902' },
      derived_session_ids: ['bcs_grp_x:round:1', 'bcs_grp_y:round:1'],
      tasks: [{
        node_id: 'n1', task_id: 'task-v2', sequence: 1, status: 'RUNNING',
        task_spec: { metadata: { title: 'T' }, goal: { objective: '', acceptances: [] } },
        run_info: { start_time: 1700000000000, end_time: 1700000005000 },
      }] as TaskDashboardResponse['tasks'],
    }));
    expect(task.sourceGroupId).toBe('g-902');
    expect(task.derivedSessionIds).toEqual(['bcs_grp_x:round:1', 'bcs_grp_y:round:1']);
    expect(task.nodes[0]!.startedAtMs).toBe(1700000000000);
    expect(task.nodes[0]!.endAtMs).toBe(1700000005000);
  });

  it('节点产物 Descriptor(富化)与 legacy 合并去重', () => {
    const task = mapDashboard(buildDashboard({
      tasks: [{
        node_id: 'n-art', task_id: 'task-v2', sequence: 1, status: 'DONE',
        task_spec: { metadata: { title: 'A' }, goal: { objective: '', acceptances: [] } },
        artifacts: [
          {
            artifact_id: 'd-1', artifact_name: '调研链接', kind: 'url_link',
            content: { url: 'https://yuque.example/x', type: '文档' },
            attempt: 1, creator: 'group-902', created_at: 1729400000000,
          },
        ],
        run_info: {
          artifacts: [
            { artifact_id: 'd-1', name: '旧口径同名', type: 'link', url: 'https://old', created_at: '2026-10-01' },
            { artifact_id: 'legacy-9', name: '旧口径独有', type: 'file', created_at: '2026-10-01' },
          ],
        },
      }] as TaskDashboardResponse['tasks'],
    }));
    const artifacts = task.nodes[0]!.artifacts;
    expect(artifacts).toHaveLength(2);
    expect(artifacts.find((a) => a.id === 'd-1')?.kind).toBe('url_link'); // Descriptor 口径胜出
    expect(artifacts.find((a) => a.id === 'legacy-9')?.name).toBe('旧口径独有');
  });
});
