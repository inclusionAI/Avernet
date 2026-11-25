/** @jest-environment jsdom */
import { MarkdownReport } from '@/pages/EvolutionFactory/OutputPrimitives';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() }, useParams: () => ({}) }));

test('报告保留 Markdown 标题、表格、列表、代码与安全链接', () => {
  const { container } = render(<MarkdownReport content={[
    '# 评估报告', '', '| 指标 | 分数 |', '| --- | --- |', '| 通过率 | 90 |', '',
    '- 检查项目', '', '```js', 'const result = "<script>";', '```', '',
    '[文档](https://example.com/docs)',
  ].join('\n')} />);
  expect(screen.getByRole('heading', { name: '评估报告' })).toBeInTheDocument();
  expect(screen.getByRole('table')).toHaveTextContent('通过率');
  expect(screen.getByRole('listitem')).toHaveTextContent('检查项目');
  expect(container.querySelector('pre code')).toHaveTextContent('const result = "<script>";');
  expect(screen.getByRole('link', { name: '文档' })).toHaveAttribute('href', 'https://example.com/docs');
});

test('外部报告内容不能生成脚本、事件处理器或危险 URL', () => {
  const { container } = render(<MarkdownReport content={[
    '<script>alert(1)</script><img src=x onerror="alert(2)">',
    '<svg onload="alert(3)"><a href="javascript:alert(4)">x</a></svg>',
    '[攻击链接](javascript:alert%281%29)',
    '[编码攻击](jav&#x61;script:alert%281%29)',
    '![攻击图片](data:image/svg+xml;base64,PHN2Zz4=)',
    '![正常图片](https://example.com/image.png)',
  ].join('\n\n')} />);
  expect(container.querySelector('script, svg, iframe, object, embed')).toBeNull();
  expect(container.querySelector('[onerror], [onload], [onclick]')).toBeNull();
  expect(container.querySelector('[href^="javascript:"], [src^="data:"]')).toBeNull();
  expect(screen.getByAltText('正常图片')).toHaveAttribute('src', 'https://example.com/image.png');
});
