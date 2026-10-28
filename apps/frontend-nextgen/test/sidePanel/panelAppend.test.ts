// 副屏 append 模式纯逻辑单测：消息串构造 + 历史重建取最长 blocks。
// 覆盖 AC-1（追加显示多条）、AC-2（默认/非 append 等价 replace）、AC-3（重建保留累积序列）、
// AC-4（replace 穿插不清空历史）。无需 jsdom / SDK——全部为纯函数。
import { buildPanelAppendAixUI } from '@/services/tasks/panelAppendMessage';
import { chooseAppendWinners } from '@/pages/Workspace/Chat/hooks/panelAppendRebuild';
import type { ChatMessage } from '@tc-chat/core';

function mkMsg(content: string): ChatMessage {
  return { messageId: Math.random().toString(36).slice(2), content, role: 'assistant' } as unknown as ChatMessage;
}

describe('buildPanelAppendAixUI', () => {
  it('构造 panel-append 声明式 AixUI：component=panel-append + tab.id + params.mode/blocks', () => {
    const tag = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }]);
    expect(tag).toContain('<AixUI type="panel" component="panel-append"');
    expect(tag).toContain("tab='");
    expect(tag).toContain("params='");
    // tab JSON 含 id
    const tabJson = tag.match(/tab='(\{.*?\})'/)![1];
    expect(JSON.parse(tabJson).id).toBe('task-app');
    // params JSON 含 mode:append + blocks
    const paramsJson = tag.match(/params='(\{.*\})'\s*><\/AixUI>$/s)![1];
    const params = JSON.parse(paramsJson);
    expect(params.mode).toBe('append');
    expect(Array.isArray(params.blocks)).toBe(true);
    expect(params.blocks).toHaveLength(1);
    expect(params.blocks[0].content).toBe('第一步');
  });

  it('连续两次 append（调用方传完整累积 blocks）→ 第二条 params.blocks 长度增长（AC-1）', () => {
    const t1 = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }]);
    const t2 = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }, { content: '第二步' }]);
    const p1 = JSON.parse(t1.match(/params='(\{.*\})'\s*><\/AixUI>$/s)![1]);
    const p2 = JSON.parse(t2.match(/params='(\{.*\})'\s*><\/AixUI>$/s)![1]);
    expect(p1.blocks).toHaveLength(1);
    expect(p2.blocks).toHaveLength(2);
    expect(p2.blocks.map((b: { content: string }) => b.content)).toEqual(['第一步', '第二步']);
  });

  it('replace 语义：调用方只传单块最新（AC-4 调用侧）', () => {
    const tag = buildPanelAppendAixUI('task-app', '副屏', [{ content: '仅最新' }]);
    const params = JSON.parse(tag.match(/params='(\{.*\})'\s*><\/AixUI>$/s)![1]);
    expect(params.blocks).toHaveLength(1);
    expect(params.blocks[0].content).toBe('仅最新');
  });

  it('标题剔除会破坏单引号/JSON 的字符（AC-无关健壮性）', () => {
    const tag = buildPanelAppendAixUI('id', "a'b`c<d\"", []);
    const tab = JSON.parse(tag.match(/tab='(\{.*?\})'/)![1]);
    expect(tab.title).not.toMatch(/[<>'"`]/);
  });
});

describe('chooseAppendWinners', () => {
  it('单调增长：取最长 blocks 为 winner（AC-1/AC-3）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].tabId).toBe('task-app');
    expect(winners[0].params.blocks).toHaveLength(2);
    expect(winners[0].params.blocks.map((b) => b.content)).toEqual(['A', 'B']);
  });

  it('非单调：replace 单块穿插后仍保留历史最长（AC-3/AC-4 重建侧）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }, { content: 'C' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '仅最新' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].params.blocks).toHaveLength(3);
    expect(winners[0].params.blocks.map((b) => b.content)).toEqual(['A', 'B', 'C']);
  });

  it('replace 为唯一最大长度时按单块重建（AC-2 默认/replace）', () => {
    const msgs = [mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '仅' }]))];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].params.blocks).toHaveLength(1);
  });

  it('不同 tabId 分别产生 winner', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('t1', '甲', [{ content: '1' }])),
      mkMsg(buildPanelAppendAixUI('t2', '乙', [{ content: 'x' }, { content: 'y' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners.map((w) => w.tabId).sort()).toEqual(['t1', 't2']);
  });

  it('无 panel-append 标记的消息被忽略', () => {
    const msgs = [
      mkMsg('hello'),
      mkMsg(`<AixUI type="panel" component="taskPanel.TaskLoopView" tab='{"id":"x"}'></AixUI>`),
    ];
    expect(chooseAppendWinners(msgs)).toHaveLength(0);
  });

  it('坏 params JSON 跳过，不抛异常', () => {
    const msgs = [
      mkMsg(`<AixUI type="panel" component="panel-append" tab='{"id":"x"}' params='not-json'></AixUI>`),
      mkMsg(buildPanelAppendAixUI('x', '副屏', [{ content: 'ok' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].params.blocks[0].content).toBe('ok');
  });

  it('缺 tab.id 回落到默认 tabId（同一副屏）', () => {
    const msgs = [
      mkMsg(`<AixUI type="panel" component="panel-append" params='{"mode":"append","blocks":[{"content":"A"}]}'></AixUI>`),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].tabId).toBe('panel-append');
  });
});
