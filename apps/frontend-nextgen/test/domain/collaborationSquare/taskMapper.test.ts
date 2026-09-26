import type { PublicTaskTransport } from '@/domain/collaborationSquare/taskMapper';
import {
  mapBbsTaskItemDto,
  mapBbsTaskStatus,
  mapPlazaStatusToBbsStatus,
  mapPublicTaskDto,
  sortPublicTasksByPublishedDesc,
} from '@/domain/collaborationSquare/taskMapper';
import type { PublicTask } from '@/domain/collaborationSquare/types';
import type { BbsTaskItem } from '@/services/backendApi/collaboration/bbsTaskController';

const validDto: PublicTaskTransport = {
  task_id: 'task-1',
  name: '梳理需求',
  goal: '输出路线图',
  acceptance_criteria: ['覆盖方向', '标注风险'],
  status: 'pending_claim',
  publisher_bot_name: '产品协作助手',
  published_at: '2026-08-19T09:00:00Z',
};

describe('mapPublicTaskDto', () => {
  it('把 snake_case transport 映射为 PublicTask（camelCase）', () => {
    expect(mapPublicTaskDto(validDto)).toEqual({
      id: 'task-1',
      name: '梳理需求',
      goal: '输出路线图',
      acceptanceCriteria: ['覆盖方向', '标注风险'],
      status: 'pending_claim',
      publisherBotName: '产品协作助手',
      publishedAt: '2026-08-19T09:00:00Z',
    });
  });

  it('映射 claimed 状态的认领者与认领时间', () => {
    const task = mapPublicTaskDto({
      ...validDto,
      status: 'claimed',
      claimed_bot_name: '运维协作助手',
      claimed_at: '2026-08-21T08:00:00Z',
    });
    expect(task).toEqual(
      expect.objectContaining({
        status: 'claimed',
        claimedBotName: '运维协作助手',
        claimedAt: '2026-08-21T08:00:00Z',
      }),
    );
  });

  it('映射 completed 状态的完成时间', () => {
    const task = mapPublicTaskDto({
      ...validDto,
      status: 'completed',
      claimed_bot_name: '运维协作助手',
      claimed_at: '2026-08-21T08:00:00Z',
      completed_at: '2026-08-25T17:00:00Z',
    });
    expect(task).toEqual(expect.objectContaining({ status: 'completed', completedAt: '2026-08-25T17:00:00Z' }));
  });

  it('未知 status 安全降级为 null（不入列）', () => {
    expect(mapPublicTaskDto({ ...validDto, status: 'drafting' })).toBeNull();
    expect(mapPublicTaskDto({ ...validDto, status: 'executing' })).toBeNull();
  });

  it('缺失 status 安全降级为 null', () => {
    expect(mapPublicTaskDto({ ...validDto, status: undefined })).toBeNull();
  });

  it('缺失或空白 task_id 安全降级为 null', () => {
    expect(mapPublicTaskDto({ ...validDto, task_id: '' })).toBeNull();
    expect(mapPublicTaskDto({ ...validDto, task_id: '   ' })).toBeNull();
    expect(mapPublicTaskDto({ ...validDto, task_id: undefined })).toBeNull();
  });

  it('缺失 acceptance_criteria 回退为空数组', () => {
    expect(mapPublicTaskDto({ ...validDto, acceptance_criteria: undefined })?.acceptanceCriteria).toEqual([]);
  });

  it('acceptance_criteria 过滤空串与 falsy 项（对齐 mapBotTransport capabilities 风格）', () => {
    expect(mapPublicTaskDto({ ...validDto, acceptance_criteria: ['有效项', '', '  '] })?.acceptanceCriteria).toEqual([
      '有效项',
      '  ',
    ]);
  });

  it('缺失 name/goal/publisher/published_at 走兜底文案且不抛错', () => {
    const task = mapPublicTaskDto({
      task_id: 'task-2',
      status: 'pending_claim',
      name: undefined,
      goal: undefined,
      publisher_bot_name: undefined,
      published_at: undefined,
    });
    expect(task).toEqual(
      expect.objectContaining({
        id: 'task-2',
        name: '未命名任务',
        goal: '',
        publisherBotName: '未公开',
        publishedAt: '',
        acceptanceCriteria: [],
        status: 'pending_claim',
      }),
    );
  });

  it('output 直传（去空白），缺失时不填', () => {
    const task = mapPublicTaskDto({ ...validDto, output: '  产出文本  ' });
    expect(task?.output).toBe('产出文本');
    expect(mapPublicTaskDto({ ...validDto, output: undefined })?.output).toBeUndefined();
  });

  it('丢弃内部字段：节点 / DAG / 日志 / 颜色 hex 不泄漏到领域对象', () => {
    const task = mapPublicTaskDto({
      ...validDto,
      node_id: 'node-7',
      dag: { nodes: ['n1', 'n2'] },
      run_log: 'internal-log',
      bbs_mode: true,
      publisher_color: '#165dff',
      claimed_color: '#abc',
      internal_task_id: 'real-task-90210',
    });
    expect(task).not.toBeNull();
    const serialized = JSON.stringify(task);
    expect(serialized).not.toContain('node_id');
    expect(serialized).not.toContain('dag');
    expect(serialized).not.toContain('run_log');
    expect(serialized).not.toContain('bbs_mode');
    expect(serialized).not.toContain('publisher_color');
    expect(serialized).not.toContain('claimed_color');
    expect(serialized).not.toContain('internal_task_id');
    expect(serialized).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });
});

describe('mapBbsTaskStatus', () => {
  it('PENDING 与 HUNG 映射为 pending_claim（待认领）', () => {
    expect(mapBbsTaskStatus('PENDING')).toBe('pending_claim');
    expect(mapBbsTaskStatus('HUNG')).toBe('pending_claim');
  });

  it('RUNNING 映射为 claimed（已认领）', () => {
    expect(mapBbsTaskStatus('RUNNING')).toBe('claimed');
  });

  it('DONE 映射为 reviewing（待验收）', () => {
    expect(mapBbsTaskStatus('DONE')).toBe('reviewing');
  });

  it('SUCCESS 映射为 completed（已完成）', () => {
    expect(mapBbsTaskStatus('SUCCESS')).toBe('completed');
  });

  it('对大小写与前后空白鲁棒', () => {
    expect(mapBbsTaskStatus('  pending  ')).toBe('pending_claim');
    expect(mapBbsTaskStatus('running')).toBe('claimed');
    expect(mapBbsTaskStatus('done')).toBe('reviewing');
    expect(mapBbsTaskStatus('success')).toBe('completed');
  });

  it('未知态返回 null（不入列）', () => {
    expect(mapBbsTaskStatus('CANCELLED')).toBeNull();
    expect(mapBbsTaskStatus('FAILED')).toBeNull();
    expect(mapBbsTaskStatus('PLANNING')).toBeNull();
    expect(mapBbsTaskStatus(undefined)).toBeNull();
    expect(mapBbsTaskStatus('')).toBeNull();
  });
});

describe('mapPlazaStatusToBbsStatus', () => {
  // 广场态→BBS 原始态用于服务端 status 过滤参数。pending_claim 实际含 PENDING+HUNG，
  // 但后端 status 当前为单值，此处先映射为 PENDING（HUNG 回收态暂不在该筛选下，待后端支持多值再补）。
  it('claimed → RUNNING', () => {
    expect(mapPlazaStatusToBbsStatus('claimed')).toBe('RUNNING');
  });

  it('completed → SUCCESS', () => {
    expect(mapPlazaStatusToBbsStatus('completed')).toBe('SUCCESS');
  });

  it('pending_claim → PENDING（单值限制下不含 HUNG）', () => {
    expect(mapPlazaStatusToBbsStatus('pending_claim')).toBe('PENDING');
  });

  it('reviewing → DONE', () => {
    expect(mapPlazaStatusToBbsStatus('reviewing')).toBe('DONE');
  });

  it('all → undefined（不过滤）', () => {
    expect(mapPlazaStatusToBbsStatus('all')).toBeUndefined();
  });
});

describe('mapBbsTaskItemDto', () => {
  const baseDto: BbsTaskItem = {
    task_id: 'bbs-1',
    title: '梳理需求',
    goal: '输出路线图',
    acceptances: [
      { id: 'a1', description: '覆盖方向' },
      { id: 'a2', description: '标注风险' },
    ],
    status: 'PENDING',
    publisher: 'bot-publisher-1',
    relay_create_time: '2026-09-01T09:00:00Z',
  };
  const publisherNameMap: Record<string, string> = { 'bot-publisher-1': '产品协作助手' };

  it('映射 BBS 字段到 PublicTask（camelCase），publisher 经 nameMap 反查', () => {
    expect(mapBbsTaskItemDto(baseDto, publisherNameMap)).toEqual({
      id: 'bbs-1',
      name: '梳理需求',
      goal: '输出路线图',
      acceptanceCriteria: ['覆盖方向', '标注风险'],
      status: 'pending_claim',
      publisher: 'bot-publisher-1',
      publisherBotName: '产品协作助手',
      publishedAt: '2026-09-01T09:00:00Z',
    });
  });

  it('publisher_name 映射为 publisherName（后端权威），publisher 原始 ID 直传；缺失/null 不填', () => {
    const withName = mapBbsTaskItemDto(
      { ...baseDto, status: 'PENDING', publisher_name: '  自动研发Bot  ' },
      publisherNameMap,
    );
    expect(withName?.publisher).toBe('bot-publisher-1');
    expect(withName?.publisherName).toBe('自动研发Bot');
    // publisher_name 缺失 → publisherName 不填，publisher 仍直传
    const noName = mapBbsTaskItemDto({ ...baseDto, status: 'PENDING' }, publisherNameMap);
    expect(noName?.publisher).toBe('bot-publisher-1');
    expect(noName?.publisherName).toBeUndefined();
    // publisher 为 null → publisher / publisherName 均不填
    const nullPub = mapBbsTaskItemDto({ ...baseDto, status: 'PENDING', publisher: null }, publisherNameMap);
    expect(nullPub?.publisher).toBeUndefined();
    expect(nullPub?.publisherName).toBeUndefined();
  });

  it('RUNNING 状态映射为 claimed，并填承接者与承接时间', () => {
    const task = mapBbsTaskItemDto(
      {
        ...baseDto,
        status: 'RUNNING',
        assignee_id: 'bot-assignee-1',
        assignee_name: '运维协作助手',
        relay_begin_time: '2026-09-01T10:00:00Z',
      },
      publisherNameMap,
    );
    expect(task).toEqual(
      expect.objectContaining({
        status: 'claimed',
        claimedBotName: '运维协作助手',
        claimedAt: '2026-09-01T10:00:00Z',
      }),
    );
  });

  it('assignee_name 缺失时兜底用 assignee_id 作为 claimedBotName', () => {
    const task = mapBbsTaskItemDto(
      { ...baseDto, status: 'RUNNING', assignee_id: 'bot-assignee-1', relay_begin_time: 't1' },
      publisherNameMap,
    );
    expect(task?.claimedBotName).toBe('bot-assignee-1');
  });

  it('assignee_name 缺失但 assigneeNameMap 命中时用反查名作 claimedBotName', () => {
    const task = mapBbsTaskItemDto(
      { ...baseDto, status: 'RUNNING', assignee_id: 'bot-assignee-1:2088', relay_begin_time: 't1' },
      publisherNameMap,
      { 'bot-assignee-1:2088': '运维协作助手' },
    );
    expect(task?.claimedBotName).toBe('运维协作助手');
  });

  it('assignee_name 命中时优先于 assigneeNameMap（后端名为权威，反查名仅兜底）', () => {
    const task = mapBbsTaskItemDto(
      {
        ...baseDto,
        status: 'RUNNING',
        assignee_id: 'bot-assignee-1:2088',
        assignee_name: '后端名',
        relay_begin_time: 't1',
      },
      publisherNameMap,
      { 'bot-assignee-1:2088': '反查名' },
    );
    expect(task?.claimedBotName).toBe('后端名');
  });

  it('extend_props.output：string 直用；对象含 string content/output 取之，否则对象 JSON；缺失/null 不填', () => {
    // 字符串型 output → 直接用（去空白）
    const str = mapBbsTaskItemDto(
      { ...baseDto, status: 'DONE', extend_props: { output: '  最终产出内容  ' } },
      publisherNameMap,
    );
    expect(str?.output).toBe('最终产出内容');
    // 对象且有 string content → 取 content（不取 extra）
    const withContent = mapBbsTaskItemDto(
      { ...baseDto, status: 'DONE', extend_props: { output: { content: '  报告内容  ', extra: { usage: null } } } },
      publisherNameMap,
    );
    expect(withContent?.output).toBe('报告内容');
    // 对象且含 string 型 output → 取 output（BBS 包装 {output} 形态）
    const withOutputKey = mapBbsTaskItemDto(
      { ...baseDto, status: 'DONE', extend_props: { output: { output: '  尽调报告已完成，已保存至工作区  ' } } },
      publisherNameMap,
    );
    expect(withOutputKey?.output).toBe('尽调报告已完成，已保存至工作区');
    // 对象但无 content 字段 → 用整个 output 的 JSON 文本
    const noContent = mapBbsTaskItemDto(
      { ...baseDto, status: 'DONE', extend_props: { output: { text: 'x', n: 1 } } },
      publisherNameMap,
    );
    expect(noContent?.output).toBe(JSON.stringify({ text: 'x', n: 1 }, null, 2));
    // content 非字符串（数字）→ 不取 content，用整个 output
    const nonStrContent = mapBbsTaskItemDto(
      { ...baseDto, status: 'DONE', extend_props: { output: { content: 123 } } },
      publisherNameMap,
    );
    expect(nonStrContent?.output).toBe(JSON.stringify({ content: 123 }, null, 2));
    // 缺失 extend_props / 无 output 键 / output 为 null → 不填
    expect(mapBbsTaskItemDto({ ...baseDto, status: 'PENDING' }, publisherNameMap)?.output).toBeUndefined();
    expect(
      mapBbsTaskItemDto({ ...baseDto, status: 'PENDING', extend_props: { foo: 1 } }, publisherNameMap)?.output,
    ).toBeUndefined();
    expect(
      mapBbsTaskItemDto({ ...baseDto, status: 'DONE', extend_props: { output: null } }, publisherNameMap)?.output,
    ).toBeUndefined();
  });

  it('DONE 状态映射为 reviewing，并填完成时间，保留承接者信息', () => {
    const task = mapBbsTaskItemDto(
      {
        ...baseDto,
        status: 'DONE',
        assignee_id: 'bot-assignee-1',
        assignee_name: '运维协作助手',
        relay_begin_time: '2026-09-01T10:00:00Z',
        relay_end_time: '2026-09-01T17:00:00Z',
      },
      publisherNameMap,
    );
    expect(task).toEqual(
      expect.objectContaining({
        status: 'reviewing',
        claimedBotName: '运维协作助手',
        claimedAt: '2026-09-01T10:00:00Z',
        completedAt: '2026-09-01T17:00:00Z',
      }),
    );
  });

  it('SUCCESS 状态映射为 completed，并填完成时间，保留承接者信息', () => {
    const task = mapBbsTaskItemDto(
      {
        ...baseDto,
        status: 'SUCCESS',
        assignee_id: 'bot-assignee-1',
        assignee_name: '运维协作助手',
        relay_begin_time: '2026-09-01T10:00:00Z',
        relay_end_time: '2026-09-01T17:00:00Z',
      },
      publisherNameMap,
    );
    expect(task).toEqual(
      expect.objectContaining({
        status: 'completed',
        claimedBotName: '运维协作助手',
        claimedAt: '2026-09-01T10:00:00Z',
        completedAt: '2026-09-01T17:00:00Z',
      }),
    );
  });

  it('非 completed 且非 reviewing 状态不填 completedAt', () => {
    const task = mapBbsTaskItemDto(
      { ...baseDto, status: 'RUNNING', assignee_id: 'bot-1', relay_end_time: 'should-not-appear' },
      publisherNameMap,
    );
    expect(task?.completedAt).toBeUndefined();
  });

  it('无承接者时不填 claimed* 字段（即便 relay_begin_time 存在）', () => {
    const task = mapBbsTaskItemDto({ ...baseDto, status: 'PENDING', relay_begin_time: 't1' }, publisherNameMap);
    expect(task?.claimedBotName).toBeUndefined();
    expect(task?.claimedAt).toBeUndefined();
  });

  it('publisher nameMap 未命中时兜底用 publisher ID', () => {
    const task = mapBbsTaskItemDto(baseDto, {});
    expect(task?.publisherBotName).toBe('bot-publisher-1');
  });

  it('publisher 为 null 时 publisherBotName 为 undefined', () => {
    const task = mapBbsTaskItemDto({ ...baseDto, publisher: null }, publisherNameMap);
    expect(task?.publisherBotName).toBeUndefined();
  });

  it('HUNG 状态映射为 pending_claim（与 PENDING 同档）', () => {
    expect(mapBbsTaskItemDto({ ...baseDto, status: 'HUNG' }, publisherNameMap)?.status).toBe('pending_claim');
  });

  it('未知 status 安全降级为 null（不入列）', () => {
    expect(mapBbsTaskItemDto({ ...baseDto, status: 'CANCELLED' }, publisherNameMap)).toBeNull();
    expect(mapBbsTaskItemDto({ ...baseDto, status: undefined }, publisherNameMap)).toBeNull();
  });

  it('缺失或空白 task_id 安全降级为 null', () => {
    expect(mapBbsTaskItemDto({ ...baseDto, task_id: '' }, publisherNameMap)).toBeNull();
    expect(mapBbsTaskItemDto({ ...baseDto, task_id: '   ' }, publisherNameMap)).toBeNull();
    expect(mapBbsTaskItemDto({ ...baseDto, task_id: undefined }, publisherNameMap)).toBeNull();
  });

  it('缺失 acceptances 回退为空数组', () => {
    expect(mapBbsTaskItemDto({ ...baseDto, acceptances: undefined }, publisherNameMap)?.acceptanceCriteria).toEqual([]);
  });

  it('acceptances 取 description 并过滤空/缺失项', () => {
    const task = mapBbsTaskItemDto(
      {
        ...baseDto,
        acceptances: [
          { id: 'a1', description: '有效项' },
          { id: 'a2', description: '' },
          { id: 'a3', description: '   ' },
          { id: 'a4' },
        ],
      },
      publisherNameMap,
    );
    expect(task?.acceptanceCriteria).toEqual(['有效项']);
  });

  it('缺失 title/goal/published_at 走兜底文案且不抛错', () => {
    const task = mapBbsTaskItemDto({ task_id: 'bbs-2', status: 'PENDING', publisher: 'bot-1' }, { 'bot-1': '发布者' });
    expect(task).toEqual(
      expect.objectContaining({
        id: 'bbs-2',
        name: '未命名任务',
        goal: '',
        publishedAt: '',
        acceptanceCriteria: [],
        status: 'pending_claim',
        publisherBotName: '发布者',
      }),
    );
  });
});

describe('sortPublicTasksByPublishedDesc', () => {
  const task = (id: string, publishedAt: string): PublicTask => ({
    id,
    name: id,
    goal: '',
    acceptanceCriteria: [],
    status: 'pending_claim',
    publishedAt,
  });

  it('按 publishedAt 倒序排列（最新发布在前）', () => {
    const items = [
      task('old', '2026-08-01T08:00:00Z'),
      task('new', '2026-08-31T08:00:00Z'),
      task('mid', '2026-08-15T08:00:00Z'),
    ];
    expect(sortPublicTasksByPublishedDesc(items).map((t) => t.id)).toEqual(['new', 'mid', 'old']);
  });

  it('publishedAt 空/非法的项排到末尾，保持原相对顺序', () => {
    const items = [
      task('valid1', '2026-08-02T08:00:00Z'),
      task('invalid', 'not-a-date'),
      task('empty', ''),
      task('valid2', '2026-08-01T08:00:00Z'),
    ];
    expect(sortPublicTasksByPublishedDesc(items).map((t) => t.id)).toEqual(['valid1', 'valid2', 'invalid', 'empty']);
  });

  it('相同 publishedAt 保持原顺序（稳定）', () => {
    const items = [
      task('a', '2026-08-10T08:00:00Z'),
      task('b', '2026-08-10T08:00:00Z'),
      task('c', '2026-08-10T08:00:00Z'),
    ];
    expect(sortPublicTasksByPublishedDesc(items).map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });

  it('返回新数组，不修改原数组', () => {
    const items = [task('a', '2026-08-02T08:00:00Z'), task('b', '2026-08-01T08:00:00Z')];
    const sorted = sortPublicTasksByPublishedDesc(items);
    expect(sorted).not.toBe(items);
    expect(items.map((t) => t.id)).toEqual(['a', 'b']);
  });

  it('空数组返回空数组', () => {
    expect(sortPublicTasksByPublishedDesc([])).toEqual([]);
  });

  it('全部 publishedAt 无效时保持原顺序（均排末尾，稳定）', () => {
    const items = [task('a', ''), task('b', 'not-a-date'), task('c', '')];
    expect(sortPublicTasksByPublishedDesc(items).map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });
});
