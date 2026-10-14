/** @jest-environment jsdom */
import { SafeHtml } from '@/assets/TaskPanel/SafeHtml';
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
