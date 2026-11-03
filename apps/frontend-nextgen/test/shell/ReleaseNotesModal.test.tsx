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

  it('richText 渲染：markdown-body 排版、链接新窗口、颜色还原、空标题剪除', () => {
    render(
      <ReleaseNotesModal
        open
        data={{
          version: '1.2.0',
          date: '2026-10-14',
          userReadmeHtml: '<p>须知</p>',
          releaseNoteHtml:
            '<p>详见<a href="https://example.com/manual">文档</a></p><p><span style="color:#FF0000">请重启 bot</span></p><h2>&nbsp;</h2><h2><strong>【20260101】</strong></h2>',
        }}
        onClose={jest.fn()}
      />,
    );
    expect(document.body.querySelectorAll('.markdown-body').length).toBeGreaterThanOrEqual(2);
    const link = document.body.querySelector('a[href="https://example.com/manual"]');
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(document.body.querySelector('.markdown-body span[style]')?.getAttribute('style')).toContain('color');
    expect(document.body.querySelectorAll('.markdown-body h2').length).toBe(1);
    expect(document.body.textContent).toContain('v1.2.0 · 2026-10-14');
  });

  it('头部显示解耦：version 为空仍显示日期', () => {
    render(
      <ReleaseNotesModal
        open
        data={{ version: '', date: '2026-10-14', userReadmeHtml: '<p>须知</p>', releaseNoteHtml: '<p>发布说明</p>' }}
        onClose={jest.fn()}
      />,
    );
    expect(document.body.textContent).toContain('2026-10-14');
  });
});
