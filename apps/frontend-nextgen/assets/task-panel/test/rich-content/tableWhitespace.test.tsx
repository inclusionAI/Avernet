/** @vitest-environment jsdom */
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SafeHtml } from '../../src/rich-content/SafeHtml';
import { MarkdownCell } from '../../src/panel/MarkdownCell';

const table = '<table>\n<thead>\n<tr>\n<th>名称</th>\n<th>结果</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>报告</td>\n<td>完成</td>\n</tr>\n</tbody>\n</table>';

describe('task-panel table whitespace', () => {
  it.each([
    ['HTML', <SafeHtml html={table} />],
    ['Markdown', <MarkdownCell content={'| 名称 | 结果 |\n| --- | --- |\n| 报告 | 完成 |'} />],
  ])('renders %s tables without invalid structural text nodes', (_label, view) => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const { container } = render(view);
      expect(container.querySelectorAll('tbody tr')).toHaveLength(1);
      expect(container.querySelector('td')?.textContent).toBe('报告');
      expect(error.mock.calls.filter(args => args.some(arg => String(arg).includes('validateDOMNesting')))).toEqual([]);
      for (const element of container.querySelectorAll('table, thead, tbody, tr')) {
        expect(Array.from(element.childNodes).filter(node => node.nodeType === Node.TEXT_NODE)).toEqual([]);
      }
    } finally {
      error.mockRestore();
    }
  });

  it('preserves whitespace inside cells, paragraphs and preformatted content', () => {
    const { container } = render(<SafeHtml html={'<table><tbody><tr><td><strong>one</strong> <em>two</em>\nthree</td></tr></tbody></table><p>a <b>b</b> c</p><pre>  first\n  second\n</pre>'} />);
    expect(container.querySelector('td')?.textContent).toBe('one two\nthree');
    expect(container.querySelector('p')?.textContent).toBe('a b c');
    expect(container.querySelector('pre')?.textContent).toBe('  first\n  second\n');
  });
});
