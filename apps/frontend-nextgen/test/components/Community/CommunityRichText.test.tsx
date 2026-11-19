/** @jest-environment jsdom */
import { CommunityBody } from '@/components/Community/CommunityBody';
import { communityEditorMarkdown } from '@/components/Community/PublishTopicModal/richText';
import { RichTextEditor } from '@/components/Community/PublishTopicModal/RichTextEditor';
import { communityBodyPreview } from '@/domain/community/bodyPreview';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ProseMirror uses browser layout APIs which jsdom does not implement.
beforeAll(() => {
  Range.prototype.getClientRects ??= () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect ??= () => document.body.getBoundingClientRect();
  document.elementFromPoint ??= () => document.activeElement;
});

describe('community rich text', () => {
  test.each([
    ['first<div>second</div>', 'first\nsecond'],
    ['<strong> word </strong>', ' **word**'],
    ['<strong>a</strong><strong>b</strong>', '**ab**'],
    ['<code>a`b</code>', '``a`b``'],
    ['<code> a </code>', '`  a  `'],
    ['a*b_[x]', 'a\\*b\\_\\[x\\]'],
  ])('preserves browser DOM %s', (html, expected) => {
    const root = document.createElement('div');
    root.innerHTML = html;
    expect(communityEditorMarkdown(root)).toBe(expected);
  });
  test.each(['- literal', 'a\n===', '&copy;'])('preserves literal %s', (text) => {
    const root = document.createElement('div');
    root.textContent = text;
    const { container, unmount } = render(<CommunityBody body={communityEditorMarkdown(root)} />);
    expect(container.textContent?.trim()).toBe(text);
    expect(container.querySelector('ul,h1,h2')).toBeNull();
    unmount();
  });
  test.each(['https://example.com/path)tail', 'https://example.com/?x=1&copy;=2'])(
    'preserves rendered href %s',
    (href) => {
      const root = document.createElement('div');
      const link = document.createElement('a');
      link.href = href;
      link.textContent = '文档';
      root.append(link);
      const { container } = render(<CommunityBody body={communityEditorMarkdown(root)} />);
      expect(container.querySelector('a')?.getAttribute('href')).toBe(href.replace(')', '%29'));
    },
  );
  test('summary strips formatting without losing link text', () => {
    expect(communityBodyPreview('**重点** [文档](https://example.com)')).toBe('重点 文档');
  });
  test('serializes formatting and lists to the existing Markdown body contract', () => {
    const root = document.createElement('div');
    root.innerHTML = '<strong>重点</strong><em>斜体</em><s>旧内容</s><ul><li>第一项</li><li>第二项</li></ul>';
    expect(communityEditorMarkdown(root)).toBe('**重点***斜体*~~旧内容~~\n\n- 第一项\n- 第二项');
  });
  test('renders formatted content while keeping raw HTML and dangerous links inert', () => {
    const { container } = render(
      <CommunityBody body={'**重点**\n\n- 第一项\n\n<script>alert(1)</script>\n[危险](javascript:alert(1))'} />,
    );
    expect(container.querySelector('strong')).toHaveTextContent('重点');
    expect(container.querySelector('li')).toHaveTextContent('第一项');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  });
  test('toolbar formats a selected range without discarding surrounding text', () => {
    const onChange = jest.fn();
    render(<RichTextEditor value="保留重点结尾" onChange={onChange} invalid={false} />);
    const node = screen.getByRole('textbox', { name: '正文' }) as HTMLElement & { editor: import('@tiptap/core').Editor };
    act(() => { node.editor.commands.setTextSelection({ from: 3, to: 5 }); });
    fireEvent.click(screen.getByRole('button', { name: '加粗' }));
    expect(onChange).toHaveBeenLastCalledWith('保留**重点**结尾');
    fireEvent.click(screen.getByRole('button', { name: '加粗' }));
    expect(onChange).toHaveBeenLastCalledWith('保留重点结尾');
  });
  test('rejects non-HTTP links and never exposes draft or custom tag controls', async () => {
    const user = userEvent.setup();
    render(<RichTextEditor value="" onChange={jest.fn()} invalid={false} />);
    await user.click(screen.getByRole('button', { name: '插入链接' }));
    await user.type(screen.getByLabelText('链接地址'), 'javascript:alert(1)');
    await user.click(screen.getByRole('button', { name: '应用链接' }));
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP');
    expect(screen.queryByText('保存草稿')).not.toBeInTheDocument();
    expect(screen.queryByText('自定义标签')).not.toBeInTheDocument();
  });
  test('pastes plaintext rather than accepting HTML', () => {
    const onChange = jest.fn();
    render(<RichTextEditor value="" onChange={onChange} invalid={false} />);
    const editor = screen.getByRole('textbox', { name: '正文' });
    fireEvent.paste(editor, {
      clipboardData: { getData: (type: string) => (type === 'text/plain' ? '安全文本' : '<img onerror="alert(1)">') },
    });
    expect(onChange).toHaveBeenLastCalledWith('安全文本');
    expect(editor.querySelector('img')).toBeNull();
  });
});
