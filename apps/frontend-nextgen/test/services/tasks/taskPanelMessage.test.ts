/** @jest-environment jsdom */
/**
 * buildTaskPanelAixUI — 任务副屏声明式消息契约测试。
 *
 * 这里只锁住 TeamClaw 侧喂给副屏 SDK 的「数据层契约」：
 *  - 每个 task 独立 tab.id（`task-${taskId}`），不同任务互不覆盖（持久化前提）；
 *  - tab.title 携带完整标题（不在此截断），由副屏 SDK 统一做「≤10 字 + hover 全文」展示；
 *  - 剔除会破坏单引号 HTML 属性 / JSON 的危险字符，但保留完整长度；
 *  - 空标题兜底为「任务」。
 */
import type { TaskRecord } from '@/domain/tasks/models';
import {
  buildRelayRootExecutionMessage,
  buildTaskLaunchMessage,
  buildTaskPanelAixUI,
} from '@/services/tasks/taskPanelMessage';

describe('buildTaskPanelAixUI', () => {
  const parse = (raw: string) => {
    // 抠 tab='...' 与 params='...' 单引号属性
    const tabRaw = raw.match(/tab='(.*?)' params='/)![1];
    const paramsRaw = raw.match(/params='(.*?)'><\/AixUI>$/)![1];
    return { tab: JSON.parse(tabRaw), params: JSON.parse(paramsRaw), raw };
  };

  it('产出 panel 声明且 component 为 taskPanel.TaskLoopView', () => {
    const raw = buildTaskPanelAixUI('t1', '任务A', { taskId: 't1' });
    expect(raw).toContain('<AixUI type="panel" component="taskPanel.TaskLoopView"');
    expect(raw).toMatch(/<\/AixUI>$/);
  });

  it('tab.id = task-${taskId}、closable=true', () => {
    const { tab } = parse(buildTaskPanelAixUI('t-9', '任务', {}));
    expect(tab.id).toBe('task-t-9');
    expect(tab.closable).toBe(true);
  });

  it('携带完整标题——超长标题不在此截断（截断交给副屏 SDK）', () => {
    const long = '整理某某某公司基础架构方向:技术栈概览 + 业务/数据双视角分析 + 架构师名册';
    const { tab } = parse(buildTaskPanelAixUI('t1', long, {}));
    expect(tab.title).toBe(long);
    expect(tab.title.length).toBeGreaterThan(10);
  });

  it('剔除破坏单引号属性 / JSON 的危险字符，但保留完整长度', () => {
    const { tab } = parse(buildTaskPanelAixUI('t1', `abc'<?>"\`def 我是有空格的标题`, {}));
    // 单引号 / 尖括号 / 双引号 / 反引号被替换为空格，不影响属性边界
    expect(tab.title).not.toMatch(/['<>"]/);
    expect(tab.title).not.toContain('`');
    expect(tab.title).toContain('我是有空格的标题');
  });

  it('空标题 / 全危险字符兜底「任务」', () => {
    expect(parse(buildTaskPanelAixUI('t1', '', {})).tab.title).toBe('任务');
    expect(parse(buildTaskPanelAixUI('t1', '   ', {})).tab.title).toBe('任务');
    expect(parse(buildTaskPanelAixUI('t1', `<>'"\``, {})).tab.title).toBe('任务');
  });

  it('不同 taskId 产出不同 tab.id（独立 tab 不互相覆盖）', () => {
    const a = parse(buildTaskPanelAixUI('a', '任务A', {})).tab.id;
    const b = parse(buildTaskPanelAixUI('b', '任务B', {})).tab.id;
    expect(a).toBe('task-a');
    expect(b).toBe('task-b');
    expect(a).not.toBe(b);
  });

  it('同 taskId 同 id（重复执行/loadHistory 拉回时按 id 幂等合并，不重复开 tab）', () => {
    const a = parse(buildTaskPanelAixUI('same', '任务A', { round: 1 })).tab.id;
    const b = parse(buildTaskPanelAixUI('same', '任务A', { round: 2 })).tab.id;
    expect(a).toBe(b);
  });

  it('params 原样透传为 JSON', () => {
    const { params } = parse(buildTaskPanelAixUI('t1', '任务', { taskId: 't1', round: 3 }));
    expect(params).toEqual({ taskId: 't1', round: 3 });
  });
});

describe('buildRelayRootExecutionMessage', () => {
  const input = {
    taskId: 'task-1',
    nodeId: 'task-1',
    holderId: 'bot-main',
    instruction: '完成需求实现',
    objective: '交付可运行功能',
    acceptances: ['测试通过'],
  };

  it('保留副屏声明并给当前主 Bot 注入首棒执行上下文', () => {
    const panel = buildTaskPanelAixUI('task-1', '接力任务', { taskId: 'task-1' });
    const raw = buildRelayRootExecutionMessage(panel, input);

    expect(raw).toContain(panel);
    expect(raw).toContain(`${panel}\n<!--\n`);
    expect(raw).toMatch(/\n-->$/);
    expect(raw).toContain('[task-execute]');
    expect(raw).toContain('orchestration_mode=relay');
    expect(raw).toContain('task_id=task-1; node_id=task-1; holder_id=bot-main');
    expect(raw).not.toContain('backend=https://backend.example');
    expect(raw).toContain('RELAY_BACKEND_BASE_URL');
    expect(raw).toContain('不要等待 TaskService 派发根节点');
    expect(raw).toContain('严格按固定 S1-S8 执行');
    for (const stage of [
      'S1/8 解析任务最新上下文',
      'S2/8 计算当前GAP',
      'S3/8 Bot能力匹配',
      'S4/8 任务执行并统一上报',
      'S5/8 更新GAP',
      'S6/8 解析下一棒',
      'S7/8 搜推并指定执行者',
      'S8/8 实际交接',
    ]) {
      expect(raw).toContain(stage);
    }
    expect(raw).toContain('之后不得重复 GET context');
    expect(raw).toContain('禁止调用上报接口');
    expect(raw).toContain('禁止输出步骤0、步骤0确认、S2-S3 合并编号或协议章节号');
    expect(raw).toContain('正常有 GAP 链路最多 6 次调用');
    expect(raw).toContain('无 GAP 链路不得调用 search、DISPATCH_RESULT 或 dispatch');
  });

  it('转义用户内容中的 HTML comment 终止序列，避免隐藏指令提前结束', () => {
    const panel = buildTaskPanelAixUI('task-1', '接力任务', { taskId: 'task-1' });
    const raw = buildRelayRootExecutionMessage(panel, {
      ...input,
      instruction: '先执行 --> 再验收 --!> 收尾',
    });
    const hiddenPart = raw.slice(panel.length);
    const host = document.createElement('div');
    host.innerHTML = raw;

    expect(hiddenPart.match(/-->/g)).toHaveLength(1);
    expect(hiddenPart).toContain('--\u200b>');
    expect(hiddenPart).not.toContain('--!>');
    expect(hiddenPart).toContain('--\u200b!>');
    expect(host.textContent?.trim()).toBe('');
  });
});

describe('buildTaskLaunchMessage', () => {
  const record = (mode?: 'centralized' | 'relay'): TaskRecord =>
    ({
      task_id: 'task-1',
      task_info: { execution_config: mode ? { orchestration_mode: mode, root_node_id: 'root-1' } : {} },
    } as TaskRecord);
  const input = {
    holderId: 'bot-main',
    instruction: '执行任务',
    objective: '完成目标',
    acceptances: ['通过验收'],
  };

  it.each([undefined, 'centralized'] as const)('%s 模式保持原副屏消息且不触发 Bot', (mode) => {
    expect(buildTaskLaunchMessage('<AixUI-panel/>', record(mode), input)).toEqual({
      content: '<AixUI-panel/>',
      shouldTriggerBot: false,
    });
  });

  it('relay 模式生成根节点执行消息并要求触发 holder', () => {
    const result = buildTaskLaunchMessage('<AixUI-panel/>', record('relay'), input);

    expect(result.shouldTriggerBot).toBe(true);
    expect(result.content).toContain('node_id=root-1; holder_id=bot-main');
  });
});
