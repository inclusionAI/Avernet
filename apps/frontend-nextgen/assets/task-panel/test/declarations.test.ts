import { describe, it, expect } from 'vitest';
import { parseTaskPanelDeclarations } from '../src/history/parseTaskPanelDeclarations';
describe('task panel declarations', () => {
  const tag = (id: string) => `<AixUI type="panel" component="taskPanel.TaskLoopView" params='{"taskId":"${id}"}' />`;
  it('parses stable task identity without executing card code', () => {
    expect(parseTaskPanelDeclarations(tag('t1'))[0]).toMatchObject({ id: 'task-t1', params: { taskId: 't1' } });
  });
  it('handles multiline attributes and > inside quoted JSON', () => {
    const text = `<AixUI\n type=panel component='taskPanel.TaskLoopView' tab='{"title":"a > b"}' params='{"taskId":"t2"}'></AixUI>`;
    expect(parseTaskPanelDeclarations(text)[0].title).toBe('a > b');
  });
  it('supports the existing legacy alias', () => {
    expect(parseTaskPanelDeclarations(`<AixUI type='panel' component='task-loop' payload='{"taskId":"t3"}'/>`)[0].params.taskId).toBe('t3');
  });
  it('does not execute examples, scripts, malformed or incomplete declarations', () => {
    expect(parseTaskPanelDeclarations('```tsx\n' + tag('t1') + '\n```')).toEqual([]);
    expect(parseTaskPanelDeclarations('`' + tag('t1') + '`')).toEqual([]);
    expect(parseTaskPanelDeclarations(`<script>${tag('t1')}</script>`)).toEqual([]);
    expect(parseTaskPanelDeclarations(`<pre>${tag('t1')}</pre>`)).toEqual([]);
    expect(parseTaskPanelDeclarations(`<code>${tag('t1')}</code>`)).toEqual([]);
    expect(parseTaskPanelDeclarations(`<AixUI type="panel" component="taskPanel.TaskLoopView" params='{bad}'/>`)).toEqual([]);
    expect(parseTaskPanelDeclarations(tag('t1').replace('/>', '>'))).toEqual([]);
  });
  it('never accepts a message-supplied remote asset', () => {
    expect(parseTaskPanelDeclarations(tag('t1').replace('params=', 'cdn="https://untrusted.invalid/x.js" params='))).toEqual([]);
    expect(parseTaskPanelDeclarations(tag('t1').replace('taskPanel.TaskLoopView', 'other.Panel'))).toEqual([]);
  });
});
