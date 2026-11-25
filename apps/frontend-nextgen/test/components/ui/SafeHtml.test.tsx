/** @jest-environment jsdom */
import { SafeHtml, sanitizeMarkdownHtml, sanitizeRichHtml } from '@/components/Common/RichContent';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

describe('SafeHtml', () => {
  it('renders permitted formatting while excluding executable elements and attributes', () => {
    const { container } = render(
      <SafeHtml
        html={
          '<h2>发布说明</h2><img src="https://example.com/a.png" onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">危险链接</a>'
        }
      />,
    );

    expect(screen.getByRole('heading', { name: '发布说明' })).toBeInTheDocument();
    expect(container.querySelector('img')).toHaveAttribute('src', 'https://example.com/a.png');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img[onerror]')).toBeNull();
    expect(container.querySelector('a')).not.toHaveAttribute('href');
  });

  it('retains only safe href and image protocols', () => {
    const { container } = render(
      <SafeHtml
        html={
          '<a href="/docs">文档</a><a href="data:text/html,boom">不安全</a><img src="data:image/svg+xml,boom" alt="x">'
        }
      />,
    );

    expect(screen.getByRole('link', { name: '文档' })).toHaveAttribute('href', '/docs');
    expect(screen.getByText('不安全').closest('a')).not.toHaveAttribute('href');
    expect(container.querySelector('img')).not.toHaveAttribute('src');
  });
});

describe('SafeHtml richText 模式', () => {
  it('renders whitelisted color and font-size inline styles', () => {
    const { container } = render(
      <SafeHtml richText html={'<p><span style="color:#FF0000;font-size:20px">请重启 bot</span></p>'} />,
    );

    const span = container.querySelector('span');
    expect(span).not.toBeNull();
    expect(span?.style.color).toBe('rgb(255, 0, 0)');
    expect(span?.style.fontSize).toBe('20px');
  });

  it('drops style properties and values outside the whitelist', () => {
    const { container } = render(
      <SafeHtml
        richText
        html={
          '<p style="color:red;background:url(javascript:alert(1));font-size:expression(alert(2));margin-left:99px">x</p>'
        }
      />,
    );

    const p = container.querySelector('p');
    expect(p?.style.color).toBeTruthy();
    expect(p?.style.background).toBe('');
    expect(p?.style.fontSize).toBe('');
    expect(p?.style.marginLeft).toBe('');
    expect(container.innerHTML).not.toContain('javascript:');
    expect(container.innerHTML).not.toContain('expression');
  });

  it('drops invalid color and font-size values while keeping valid ones in the same attribute', () => {
    const { container } = render(
      <SafeHtml richText html={'<p style="color:red !important;font-size:20px;position:fixed">x</p>'} />,
    );

    const p = container.querySelector('p');
    expect(p?.style.color).toBe('');
    expect(p?.style.fontSize).toBe('20px');
    expect(p?.style.position).toBe('');
  });

  it('opens safe links in a new tab only when richText is enabled', () => {
    const { container, rerender } = render(<SafeHtml html={'<a href="https://example.com/doc">文档</a>'} />);

    expect(container.querySelector('a')).not.toHaveAttribute('target');

    rerender(<SafeHtml richText html={'<a href="https://example.com/doc">文档</a>'} />);
    expect(container.querySelector('a')).toHaveAttribute('target', '_blank');
    expect(container.querySelector('a')).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('prunes whitespace-only headings in richText mode', () => {
    const { container } = render(
      <SafeHtml richText html={'<h2>&nbsp;</h2><h2><span>【20260101】</span></h2>'} />,
    );

    const headings = container.querySelectorAll('h2');
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent('【20260101】');
  });

  it('keeps default mode unchanged: style ignored and empty heading kept', () => {
    const { container } = render(<SafeHtml html={'<p style="color:#FF0000">x</p><h2>&nbsp;</h2>'} />);

    expect(container.querySelector('p')).not.toHaveAttribute('style');
    expect(container.querySelectorAll('h2')).toHaveLength(1);
  });
});

describe('host-owned sanitizers', () => {

  it.each([sanitizeMarkdownHtml, sanitizeRichHtml])('removes executable HTML without depending on task assets', (sanitize) => {
    const html = sanitize('<p onclick="alert(1)">safe</p><script>alert(2)</script><a href="javascript:alert(3)">link</a><iframe src="/x"></iframe>');
    const root = document.createElement('div');
    root.innerHTML = html;
    expect(root.querySelector('p')?.textContent).toBe('safe');
    expect(root.querySelector('[onclick], script, iframe')).toBeNull();
    expect(root.querySelector('a')?.hasAttribute('href')).toBe(false);
  });

  it('keeps rich formatting attributes separate from markdown attributes', () => {
    const html = '<span class="md" style="color:red" data-secret="x">text</span>';
    expect(sanitizeMarkdownHtml(html)).toBe('<span class="md">text</span>');
    expect(sanitizeRichHtml(html)).toBe('<span style="color:red">text</span>');
  });
});
