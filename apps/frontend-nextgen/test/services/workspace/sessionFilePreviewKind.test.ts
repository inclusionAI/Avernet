/** @jest-environment jsdom */
import { getPreviewKind, prettifyJsonText, PREVIEW_TEXT_EXT } from '@/services/workspace/sessionFileUtils';
import { describe, expect, it } from '@jest/globals';

describe('sessionFileUtils 预览类型判定（对齐会话文件预览类型清单）', () => {
  it('视频类型：mp4/webm/mov 判定为 video（内嵌播放器预览）', () => {
    expect(getPreviewKind('演示录屏.mp4')).toBe('video');
    expect(getPreviewKind('动画.webm')).toBe('video');
    expect(getPreviewKind('片段.mov')).toBe('video');
    // 无扩展名时按 MIME 兜底。
    expect(getPreviewKind('无扩展名视频', 'video/webm')).toBe('video');
  });

  it('网页类型：html 判定为 html（沙箱渲染），并从文本白名单拆出', () => {
    expect(getPreviewKind('落地页.html')).toBe('html');
    expect(getPreviewKind('无扩展名网页', 'text/html')).toBe('html');
    // html 不再属于 text（等宽原文展示）。
    expect(PREVIEW_TEXT_EXT).not.toContain('html');
  });

  it('既有类型不回归：图片/pdf/文本/不可预览', () => {
    expect(getPreviewKind('截图.png')).toBe('image');
    expect(getPreviewKind('报告.pdf')).toBe('pdf');
    expect(getPreviewKind('说明.md')).toBe('text');
    expect(getPreviewKind('配置.json')).toBe('text');
    expect(getPreviewKind('打包.zip')).toBe('other');
    expect(getPreviewKind('文档.docx')).toBe('other');
  });

  it('JSON 美化：合法 JSON 缩进重排，非法 JSON 原样返回', () => {
    expect(prettifyJsonText('{"a":1,"b":[2,3]}')).toBe(JSON.stringify({ a: 1, b: [2, 3] }, null, 2));
    // 非法 JSON（如截断后的半截内容）回退原文，不抛错。
    const broken = '{"a":1,"b":[2,3';
    expect(prettifyJsonText(broken)).toBe(broken);
    expect(prettifyJsonText('普通文本')).toBe('普通文本');
  });
});
