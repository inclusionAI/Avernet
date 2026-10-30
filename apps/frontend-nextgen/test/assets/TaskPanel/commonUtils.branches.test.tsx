/** @jest-environment jsdom */
// TaskPanel 基础工具的空值、无效时间与富文本净化分支。
import { formatArtifactUpdatedAt } from '@/assets/TaskPanel/artifactTime';
import { sanitizeMarkdownHtml, sanitizeRichHtml } from '@/assets/TaskPanel/sanitizeHtml';
import { truncateText } from '@/assets/TaskPanel/text';
import { describe, expect, it, jest } from '@jest/globals';

describe('TaskPanel 基础工具分支', () => {
  it('truncateText 覆盖无效长度与空值', () => {
    expect(truncateText('文本', 0)).toBe('');
    expect(truncateText(null, 3)).toBe('');
    expect(truncateText('中文文本串', 4)).toBe('中文文…');
  });

  it('formatArtifactUpdatedAt 空串返回空文案', () => {
    expect(formatArtifactUpdatedAt('')).toBe('');
  });

  it('formatArtifactUpdatedAt 缺少时间字段时返回空文案', () => {
    jest.spyOn(Intl.DateTimeFormat.prototype, 'formatToParts').mockReturnValueOnce([]);
    expect(formatArtifactUpdatedAt('2026-10-23 10:00:00')).toBe('');
    jest.restoreAllMocks();
  });

  it('sanitizeRichHtml 剔除脚本与事件属性', () => {
    const clean = sanitizeRichHtml('<p onclick="alert(1)">发布说明</p><script>alert(1)</script>');
    expect(clean).toContain('发布说明');
    expect(clean).not.toContain('script');
    expect(clean).not.toContain('onclick');
  });

  it('sanitizeMarkdownHtml 保留白名单链接并剔除事件属性', () => {
    const clean = sanitizeMarkdownHtml('<a href="https://example.com" title="链接">文档</a><span onmouseover="alert(1)">x</span>');
    expect(clean).toContain('https://example.com');
    expect(clean).toContain('文档');
    expect(clean).not.toContain('onmouseover');
  });
});
