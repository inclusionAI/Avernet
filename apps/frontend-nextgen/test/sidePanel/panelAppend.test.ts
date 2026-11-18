// 副屏 append 模式纯逻辑单测：消息串构造（含 SDK 同款属性解析回环）+ 历史重建选择。
// 覆盖 AC-1（追加显示多条）、AC-2（缺省 mode 等价 replace）、AC-3（重建保留累积序列）、
// AC-4（显式 replace 清空累积）。无需 jsdom / SDK 渲染——全部为纯函数。
import { extractTagAttr, parseAixJson } from '@tc-chat/ui/es/utils/aixTags';
import type { ChatMessage } from '@tc-chat/core';
import type { PanelAppendParams } from '@/components/PanelAppend';
import { buildPanelAppendAixUI } from '@/services/tasks/panelAppendMessage';
import { chooseAppendWinners } from '@/pages/Workspace/Chat/hooks/panelAppendRebuild';

function mkMsg(content: string): ChatMessage {
  return { messageId: Math.random().toString(36).slice(2), content, role: 'assistant' } as unknown as ChatMessage;
}

/** 用 SDK live 渲染同款路径解析构造出的标签：extractTagAttr 取属性 → parseAixJson 反解实体/JSON。 */
function parseBuiltTag(tag: string): { tab: Record<string, unknown>; params: PanelAppendParams } {
  const attrs = tag.match(/^<AixUI\b([^>]*)>/i)?.[1] ?? '';
  const tab = parseAixJson(extractTagAttr(attrs, 'tab')) as Record<string, unknown>;
  const params = parseAixJson(extractTagAttr(attrs, 'params')) as PanelAppendParams;
  return { tab, params };
}

describe('buildPanelAppendAixUI', () => {
  it('构造 panel-append 声明式 AixUI：component=panel-append + tab.id + params.mode/blocks', () => {
    const tag = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }]);
    expect(tag).toContain('<AixUI type="panel" component="panel-append"');
    const { tab, params } = parseBuiltTag(tag);
    expect(tab.id).toBe('task-app');
    expect(params.mode).toBe('append');
    expect(params.blocks).toHaveLength(1);
    expect(params.blocks[0].content).toBe('第一步');
  });

  it('块内容含引号/尖括号/&/换行时，经 SDK 同款解析仍完整保留（P1 回归）', () => {
    const content = `it's fine & a > b < c "quoted"\n第二行`;
    const tag = buildPanelAppendAixUI('task-app', '副屏', [{ content, label: `步骤'1` }]);
    const { params } = parseBuiltTag(tag);
    expect(params.blocks[0].content).toBe(content);
    expect(params.blocks[0].label).toBe(`步骤'1`);
  });

  it('连续两次 append（调用方传完整累积 blocks）→ 第二条 params.blocks 长度增长（AC-1）', () => {
    const t1 = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }]);
    const t2 = buildPanelAppendAixUI('task-app', '副屏', [{ content: '第一步' }, { content: '第二步' }]);
    const p1 = parseBuiltTag(t1).params;
    const p2 = parseBuiltTag(t2).params;
    expect(p1.blocks).toHaveLength(1);
    expect(p2.blocks).toHaveLength(2);
    expect(p2.blocks.map((b) => b.content)).toEqual(['第一步', '第二步']);
  });

  it('显式 replace 模式：params.mode=replace，blocks 为单块最新（AC-4 调用侧）', () => {
    const tag = buildPanelAppendAixUI('task-app', '副屏', [{ content: '仅最新' }], { mode: 'replace' });
    const params = parseBuiltTag(tag).params;
    expect(params.mode).toBe('replace');
    expect(params.blocks).toHaveLength(1);
    expect(params.blocks[0].content).toBe('仅最新');
  });

  it('标题剔除会破坏单引号/JSON 的字符（健壮性）', () => {
    const tag = buildPanelAppendAixUI('id', "a'b`c<d\"", []);
    const tab = parseBuiltTag(tag).tab;
    expect(String(tab.title)).not.toMatch(/[<>'"`]/);
  });
});

describe('chooseAppendWinners', () => {
  it('单调增长：取最长 append blocks 为 winner（AC-1/AC-3）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].tabId).toBe('task-app');
    expect(winners[0].mode).toBe('append');
    expect(winners[0].params.blocks).toHaveLength(2);
    expect(winners[0].params.blocks.map((b) => b.content)).toEqual(['A', 'B']);
  });

  it('显式 replace 穿插后按最新单块重建，历史被清空（AC-4 重建侧）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }, { content: 'C' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '仅最新' }], { mode: 'replace' })),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].mode).toBe('replace');
    expect(winners[0].params.blocks).toHaveLength(1);
    expect(winners[0].params.blocks[0].content).toBe('仅最新');
  });

  it('replace 之后的新 append 序列从新序列重建（AC-4 + AC-3）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }, { content: 'C' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '重置' }], { mode: 'replace' })),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '重置' }, { content: '新B' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].mode).toBe('append');
    expect(winners[0].params.blocks.map((b) => b.content)).toEqual(['重置', '新B']);
  });

  it('append 序列内非单调短消息不折叠更长历史（AC-3 容错）', () => {
    const msgs = [
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }, { content: 'C' }])),
      mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: '缺失步' }])),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].params.blocks.map((b) => b.content)).toEqual(['A', 'B', 'C']);
  });

  it('缺省 mode 的 panel-append 声明等价 replace（AC-2）', () => {
    const msgs = [
      mkMsg(
        `<AixUI type="panel" component="panel-append" tab='{"id":"x"}' ` +
          `params='{"blocks":[{"content":"A"},{"content":"B"}]}'></AixUI>`,
      ),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].mode).toBe('replace');
  });

  it('特殊字符内容在重建解析中同样完整保留', () => {
    const content = `a & b 'c' <d> "e"`;
    const msgs = [mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content }]))];
    const winners = chooseAppendWinners(msgs);
    expect(winners[0].params.blocks[0].content).toBe(content);
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
      mkMsg(
        `<AixUI type="panel" component="panel-append" ` +
          `params='{"mode":"append","blocks":[{"content":"A"}]}'></AixUI>`,
      ),
    ];
    const winners = chooseAppendWinners(msgs);
    expect(winners).toHaveLength(1);
    expect(winners[0].tabId).toBe('panel-append');
  });
});
