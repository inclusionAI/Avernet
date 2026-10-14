/** @jest-environment jsdom */
// ReleaseNotesModal XSS 防护：雨燕运营富文本（userReadmeHtml/releaseNoteHtml）
// 注入前必须经 sanitizeRichHtml（DOMPurify）消毒，script/事件处理器不得成为可执行元素。
// Radix Modal 内容挂 document.body portal，断言一律查 document.body。
import type { ReleaseNotesData } from '@/capabilities';
import { describe, expect, it, jest } from '@jest/globals';
import { render } from '@testing-library/react';

const { ReleaseNotesModal } = require('@/shell/ReleaseNotesModal') as typeof import('@/shell/ReleaseNotesModal');

const malicious: ReleaseNotesData = {
  version: '1.2.0',
  date: '2026-10-14',
  userReadmeHtml: '<h3>须知</h3><img src=x onerror="alert(1)"><script>alert(2)</script>',
  releaseNoteHtml: '<p>发布说明</p><a href="javascript:alert(3)">bad link</a>',
};

describe('ReleaseNotesModal', () => {
  it('渲染用户须知与新版本发布内容', () => {
    render(<ReleaseNotesModal open data={malicious} onClose={jest.fn()} />);
    expect(document.body.textContent).toContain('用户须知');
    expect(document.body.textContent).toContain('新版本发布');
    expect(document.body.textContent).toContain('须知');
    expect(document.body.textContent).toContain('发布说明');
  });

  it('富文本经 DOMPurify 消毒：script/onerror/javascript: 不产生可执行元素', () => {
    render(<ReleaseNotesModal open data={malicious} onClose={jest.fn()} />);
    expect(document.body.querySelector('script')).toBeNull();
    expect(document.body.querySelector('img[onerror]')).toBeNull();
    expect(document.body.innerHTML).not.toContain('javascript:');
  });

  it('空数据时两区显示占位', () => {
    render(<ReleaseNotesModal open data={null} onClose={jest.fn()} />);
    expect(document.body.textContent).toContain('暂无内容');
  });
});