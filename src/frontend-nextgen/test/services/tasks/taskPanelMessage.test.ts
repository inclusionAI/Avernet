/**
 * buildTaskPanelAixUI — 任务副屏声明式消息契约测试。
 *
 * 这里只锁住 TeamClaw 侧喂给副屏 SDK 的「数据层契约」：
 *  - 每个 task 独立 tab.id（`task-${taskId}`），不同任务互不覆盖（持久化前提）；
 *  - tab.title 携带完整标题（不在此截断），由副屏 SDK 统一做「≤10 字 + hover 全文」展示；
 *  - 剔除会破坏单引号 HTML 属性 / JSON 的危险字符，但保留完整长度；
 *  - 空标题兜底为「任务」。
 */
import { buildTaskPanelAixUI } from '@/services/tasks/taskPanelMessage';

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
