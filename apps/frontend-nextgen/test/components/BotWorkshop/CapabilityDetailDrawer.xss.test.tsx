/** @jest-environment jsdom */
// CapabilityDetailDrawer XSS 防护：SKILL.md 内容经 markdown-it html:false 渲染后
// 必须再过 sanitizeMarkdownHtml（DOMPurify 白名单），内联 script/事件处理器不得成为可执行元素。
import type { CapabilityDetailTarget } from '@/services/botWorkshop/botCapabilityDetailService';
import { describe, expect, it, jest } from '@jest/globals';
import { render } from '@testing-library/react';

const mockDetail = {
  name: 'demo-skill',
  description: '演示技能',
  content: [
    '---',
    'name: demo-skill',
    'description: 演示技能',
    '---',
    '',
    '# 文档',
    '<script>alert(1)</script>',
    '<img src=x onerror="alert(2)">',
  ].join('\n'),
};
jest.mock('@/hooks/useBotCapabilityDetail', () => ({
  useBotCapabilityDetail: () => ({ detail: mockDetail, loading: false, error: null, retry: jest.fn() }),
}));

const { CapabilityDetailDrawer } = require('@/components/BotWorkshop/Editor/CapabilityDetailDrawer') as typeof import(
  '@/components/BotWorkshop/Editor/CapabilityDetailDrawer'
);

describe('CapabilityDetailDrawer XSS 消毒', () => {
  it('SKILL.md 内联 HTML 不产生可执行元素', () => {
    const target: CapabilityDetailTarget = { kind: 'skill', id: 's1' } as unknown as CapabilityDetailTarget;
    render(<CapabilityDetailDrawer botId="b1" target={target} ownerId="u1" onClose={jest.fn()} />);
    // Radix Drawer 内容挂 document.body portal，断言查 document.body。
    expect(document.body.textContent).toContain('文档');
    expect(document.body.querySelector('script')).toBeNull();
    expect(document.body.querySelector('img[onerror]')).toBeNull();
  });
});