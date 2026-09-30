/** @jest-environment jsdom */
import {
  getSessionFileCategory,
  getSessionFileIconKind,
  SessionFileTypeIcon,
} from '@/pages/Workspace/components/SessionFileTypeIcon';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render } from '@testing-library/react';

describe('SessionFileTypeIcon（会话文件类型图标映射）', () => {
  it('按扩展名细分类型（覆盖上传白名单的主要族别）', () => {
    // 扩展名 → kind 映射表（含 tar.gz 双段后缀与无扩展名兜底）。
    expect(getSessionFileIconKind('截图.png')).toBe('image');
    expect(getSessionFileIconKind('照片.jpeg')).toBe('image');
    expect(getSessionFileIconKind('矢量图.svg')).toBe('image');
    expect(getSessionFileIconKind('分析报告.pdf')).toBe('pdf');
    expect(getSessionFileIconKind('数据表.xlsx')).toBe('spreadsheet');
    expect(getSessionFileIconKind('导出数据.csv')).toBe('spreadsheet');
    expect(getSessionFileIconKind('打包资料.zip')).toBe('archive');
    expect(getSessionFileIconKind('归档备份.tar.gz')).toBe('archive');
    expect(getSessionFileIconKind('逻辑模块.ts')).toBe('code');
    expect(getSessionFileIconKind('配置.json')).toBe('code');
    expect(getSessionFileIconKind('编排.yaml')).toBe('code');
    expect(getSessionFileIconKind('说明文档.md')).toBe('doc');
    expect(getSessionFileIconKind('运行日志.log')).toBe('doc');
    expect(getSessionFileIconKind('会议纪要.docx')).toBe('office');
    expect(getSessionFileIconKind('演示幻灯.pptx')).toBe('office');
    expect(getSessionFileIconKind('演示录屏.mp4')).toBe('video');
    expect(getSessionFileIconKind('片段.mov')).toBe('video');
    expect(getSessionFileIconKind('未知文件')).toBe('other');
  });

  it('mimeType 图片族兜底（无扩展名时按 MIME 判定为图片）', () => {
    expect(getSessionFileIconKind('无扩展名截图', 'image/png')).toBe('image');
    expect(getSessionFileIconKind('无扩展名截图', 'IMAGE/GIF')).toBe('image');
    // 非图片 MIME 不参与细分（扩展名优先，无扩展名归 other）。
    expect(getSessionFileIconKind('无扩展名文件', 'application/octet-stream')).toBe('other');
  });

  it('大类映射（过滤 Tab 用）：细分族聚合为媒体/文档/代码/表格/其他', () => {
    // 媒体 = 图片 + 视频。
    expect(getSessionFileCategory('截图.png')).toBe('media');
    expect(getSessionFileCategory('演示录屏.mp4')).toBe('media');
    // 文档 = 文本 + PDF + Office。
    expect(getSessionFileCategory('说明文档.md')).toBe('document');
    expect(getSessionFileCategory('分析报告.pdf')).toBe('document');
    expect(getSessionFileCategory('会议纪要.docx')).toBe('document');
    // 代码 / 表格 / 其他（压缩 + 未知）。
    expect(getSessionFileCategory('配置.json')).toBe('code');
    expect(getSessionFileCategory('数据表.xlsx')).toBe('sheet');
    expect(getSessionFileCategory('打包资料.zip')).toBe('other');
    expect(getSessionFileCategory('未知文件')).toBe('other');
  });

  it('渲染：图片类型走 FileImage 形状 + info 语义色，默认尺寸', () => {
    const { container } = render(<SessionFileTypeIcon name="截图.png" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('aria-hidden');
    // SVG 的 className 是 SVGAnimatedString，须用 toHaveClass 断言。
    expect(svg).toHaveClass('text-info', 'h-4', 'w-4');
  });

  it('渲染：PDF 红色 / 表格绿色 / 压缩橙色 / 代码与文档蓝色 / Office 与未知中性', () => {
    const colorOf = (name: string) => {
      const { container } = render(<SessionFileTypeIcon name={name} />);
      return container.querySelector('svg');
    };
    expect(colorOf('分析报告.pdf')).toHaveClass('text-destructive');
    expect(colorOf('数据表.xlsx')).toHaveClass('text-success');
    expect(colorOf('打包资料.zip')).toHaveClass('text-warning');
    expect(colorOf('配置.json')).toHaveClass('text-primary');
    expect(colorOf('说明文档.md')).toHaveClass('text-primary');
    expect(colorOf('会议纪要.docx')).toHaveClass('text-muted-foreground');
    expect(colorOf('未知文件')).toHaveClass('text-muted-foreground');
  });

  it('自定义 className 透传（图标尺寸跟随行布局）', () => {
    const { container } = render(<SessionFileTypeIcon name="截图.png" className="h-5 w-5" />);
    expect(container.querySelector('svg')).toHaveClass('h-5', 'w-5');
  });
});
