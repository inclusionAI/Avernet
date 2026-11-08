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
import { PANEL_WIDTH_STORAGE_KEY } from '@/services/workspace/panelWidthMemory';

const backendRelayInstruction = [
  '[task-execute]',
  'task_id=task-1; node_id=task-1',
  'relay_token=generation-1',
  'backend_url=https://backend.example',
  'S1 读取上下文：GET /api/v1/collaboration/tasks/task-1/nodes/task-1/context',
  'S6 POST /api/v1/collaboration/tasks/dispatch',
].join('\n');

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
  it('保留副屏声明并原样转发 Backend 生成的首棒执行指令', () => {
    const panel = buildTaskPanelAixUI('task-1', '接力任务', { taskId: 'task-1' });
    const raw = buildRelayRootExecutionMessage(panel, backendRelayInstruction);

    expect(raw).toContain(panel);
    expect(raw).toContain(`${panel}\n<!--\n`);
    expect(raw).toMatch(/\n-->$/);
    expect(raw).toContain('[task-execute]');
    expect(raw).toContain('task_id=task-1; node_id=task-1');
    expect(raw).toContain('relay_token=generation-1');
    expect(raw).toContain('backend_url=https://backend.example');
    expect(raw).toContain('/nodes/task-1/context');
    expect(raw).not.toContain('holder_id');
    expect(raw).not.toContain('RELAY_BACKEND_BASE_URL');
    expect(raw).not.toContain('S1/8');
  });

  it('转义用户内容中的 HTML comment 终止序列，避免隐藏指令提前结束', () => {
    const panel = buildTaskPanelAixUI('task-1', '接力任务', { taskId: 'task-1' });
    const raw = buildRelayRootExecutionMessage(panel, `${backendRelayInstruction}\n先执行 --> 再验收 --!> 收尾`);
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
      task_info: {
        execution_config:
          mode === 'relay'
            ? {
                orchestration_mode: mode,
                root_node_id: 'root-1',
                root_execution_instruction: backendRelayInstruction,
              }
            : mode
            ? { orchestration_mode: mode }
            : {},
      },
    } as TaskRecord);

  it.each([undefined, 'centralized'] as const)('%s 模式保持原副屏消息且不触发 Bot', (mode) => {
    expect(buildTaskLaunchMessage('<AixUI-panel/>', record(mode))).toEqual({
      content: '<AixUI-panel/>',
      shouldTriggerBot: false,
    });
  });

  it('relay 模式转发 Backend 根节点指令并要求触发当前 Bot', () => {
    const result = buildTaskLaunchMessage('<AixUI-panel/>', record('relay'));

    expect(result.shouldTriggerBot).toBe(true);
    expect(result.content).toContain('relay_token=generation-1');
    expect(result.content).toContain('backend_url=https://backend.example');
  });

  it('relay 响应缺少 Backend 根节点指令时拒绝发送旧协议', () => {
    const invalid = {
      ...record('relay'),
      task_info: { execution_config: { orchestration_mode: 'relay', root_node_id: 'root-1' } },
    } as TaskRecord;

    expect(() => buildTaskLaunchMessage('<AixUI-panel/>', invalid)).toThrow('Backend 未返回 relay 根节点执行指令');
  });
});

describe('buildTaskPanelAixUI defaultWidth(V2 副屏初始宽 420,拖宽记忆恢复)', () => {
  beforeEach(() => localStorage.removeItem(PANEL_WIDTH_STORAGE_KEY));

  it('无拖宽记忆:tab 声明携带 defaultWidth: 420(PRD 初始宽,spec §3.1-2)', () => {
    const raw = buildTaskPanelAixUI('t-dw', '任务宽', { taskId: 't-dw' });
    const tabMatch = raw.match(/tab='([^']+)'/);
    expect(tabMatch).not.toBeNull();
    expect(JSON.parse(tabMatch![1]).defaultWidth).toBe(420);
  });

  it('共享右侧面板记忆为 350 时,任务 tab 仍使用 PRD 默认宽 420', () => {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, '350');
    const raw = buildTaskPanelAixUI('t-dw-narrow', '任务宽', { taskId: 't-dw-narrow' });
    const tabMatch = raw.match(/tab='([^']+)'/);
    expect(tabMatch).not.toBeNull();
    expect(JSON.parse(tabMatch![1]).defaultWidth).toBe(420);
  });

  it('用户拖宽至 560 后,新任务 tab 的 defaultWidth 恢复 560(SDK 下 defaultWidth 优先于容器级拖宽,切任务不回落)', () => {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, '560');
    const raw = buildTaskPanelAixUI('t-dw2', '任务宽2', { taskId: 't-dw2' });
    const tabMatch = raw.match(/tab='([^']+)'/);
    expect(tabMatch).not.toBeNull();
    expect(JSON.parse(tabMatch![1]).defaultWidth).toBe(560);
  });
});
