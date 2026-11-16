/** @jest-environment jsdom */
import { PreviewPane } from '@/pages/Workspace/components/GroupChatPane/SessionFilesPreviewPane';
import type { SessionFilePreviewState } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

function textPreview(overrides: Partial<SessionFilePreviewState> = {}): SessionFilePreviewState {
  return {
    kind: 'text',
    status: 'ready',
    contentUrl: null,
    text: null,
    truncated: false,
    errorMessage: null,
    ...overrides,
  };
}

describe('SessionFilesPreviewPane（预览体渲染）', () => {
  it('md 文本渲染为富文本：标题与粗体元素生成（markdown-it 生效）', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '说明文档.md', size: 1024 }}
        filesStateEmpty={false}
        preview={textPreview({ text: '# 部署手册\n\n发布前需 **回归测试**。' })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    // markdown 语法渲染成结构化元素，而非源码原文。
    expect(container.querySelector('h1')?.textContent).toBe('部署手册');
    expect(container.querySelector('strong')?.textContent).toBe('回归测试');
    // 源码标记不应原样出现。
    expect(screen.queryByText('# 部署手册')).not.toBeInTheDocument();
  });

  it('md 文本的原始 HTML 只显示为文本，事件属性不能进入 DOM', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '不可信说明.md', size: 1024 }}
        filesStateEmpty={false}
        preview={textPreview({ text: '<script>alert(1)</script><img src=x onerror="alert(2)">' })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );

    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img[onerror]')).toBeNull();
  });

  it('非 md 文本走等宽 pre 原文展示（txt/log/json 等）', () => {
    render(
      <PreviewPane
        file={{ name: '运行日志.log', size: 512 }}
        filesStateEmpty={false}
        preview={textPreview({ text: '2026-09-15 10:00:00 INFO boot ok' })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    expect(screen.getByTestId('session-files-plain-text')).toHaveTextContent('2026-09-15 10:00:00 INFO boot ok');
  });

  it('截断提示：内容超 512KB 被截断时提示下载查看全文', () => {
    render(
      <PreviewPane
        file={{ name: '大数据.json', size: 1024 * 1024 }}
        filesStateEmpty={false}
        preview={textPreview({ text: '{"a":1}', truncated: true })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    expect(screen.getByText(/内容较长，仅显示部分/)).toBeInTheDocument();
  });

  it('PDF 仍走浏览器 iframe（守护：二进制类不进自绘路径）', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '分析报告.pdf', size: 2048 }}
        filesStateEmpty={false}
        preview={{
          kind: 'pdf',
          status: 'ready',
          contentUrl: 'https://gw.example/file.pdf',
          text: null,
          truncated: false,
          errorMessage: null,
        }}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    expect(container.querySelector('iframe')).toBeInTheDocument();
  });

  it('视频类型：内嵌播放器（video 标签带 controls）+ 视频徽标', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '演示录屏.mp4', size: 4096 }}
        filesStateEmpty={false}
        preview={{
          kind: 'video',
          status: 'ready',
          contentUrl: 'https://gw.example/file.mp4',
          text: null,
          truncated: false,
          errorMessage: null,
        }}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    const video = container.querySelector('video');
    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('src', 'https://gw.example/file.mp4');
    expect(screen.getByText('视频')).toBeInTheDocument();
  });

  it('网页类型：沙箱 iframe 渲染（sandbox 限制脚本执行）', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '落地页.html', size: 2048 }}
        filesStateEmpty={false}
        preview={{
          kind: 'html',
          status: 'ready',
          contentUrl: 'https://gw.example/page.html',
          text: null,
          truncated: false,
          errorMessage: null,
        }}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    const iframe = container.querySelector('iframe');
    expect(iframe).toBeInTheDocument();
    expect(iframe).toHaveAttribute('src', 'https://gw.example/page.html');
    expect(iframe).toHaveAttribute('sandbox', '');
    expect(screen.getByText('网页')).toBeInTheDocument();
  });

  it('引用按钮可选：传 onReference 渲染并透传，不传不渲染（群聊侧零影响）', () => {
    const onReference = jest.fn();

    // 不传 onReference（群聊 / 回归守护）：预览头无「引用」。
    const { rerender } = render(
      <PreviewPane
        file={{ name: '说明文档.md', size: 1024 }}
        filesStateEmpty={false}
        preview={textPreview({ text: 'x' })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: '引用' })).not.toBeInTheDocument();

    // 传 onReference（单聊副屏）：渲染「引用」并透传点击。
    rerender(
      <PreviewPane
        file={{ name: '说明文档.md', size: 1024 }}
        filesStateEmpty={false}
        preview={textPreview({ text: 'x' })}
        onDownload={jest.fn()}
        onReference={onReference}
        onBack={jest.fn()}
      />,
    );
    const refBtn = screen.getByRole('button', { name: '引用' });
    fireEvent.click(refBtn);
    expect(onReference).toHaveBeenCalledTimes(1);
  });
});

describe('group text URL preview', () => {
  it.each(['说明.md', '配置.json', '服务.yaml', '样式.scss', 'Dockerfile'])(
    'navigates %s inside a fully sandboxed iframe',
    (name) => {
      const onDownload = jest.fn();
      const onBack = jest.fn();
      const contentUrl = 'https://gateway.example/content?show=true';
      const { container } = render(
        <PreviewPane
          file={{ name, size: 9837 }}
          filesStateEmpty={false}
          preview={textPreview({ contentUrl })}
          onDownload={onDownload}
          onBack={onBack}
        />,
      );
      const iframe = screen.getByTitle(name);
      expect(iframe).toHaveAttribute('src', contentUrl);
      expect(iframe).toHaveAttribute('sandbox', '');
      expect(iframe).not.toHaveAttribute('srcdoc');
      expect(container.querySelector('pre')).toBeNull();
      expect(screen.queryByText('该文件类型暂不支持预览')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '下载' }));
      fireEvent.click(screen.getByRole('button', { name: '返回文件列表' }));
      expect(onDownload).toHaveBeenCalledTimes(1);
      expect(onBack).toHaveBeenCalledTimes(1);
    },
  );

  it('preserves supplied Bot Markdown even when a content URL is also available', () => {
    const { container } = render(
      <PreviewPane
        file={{ name: '说明.md', size: 100 }}
        filesStateEmpty={false}
        preview={textPreview({ contentUrl: 'https://gateway.example/content', text: '# Bot 原有预览' })}
        onDownload={jest.fn()}
        onBack={jest.fn()}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Bot 原有预览' })).toBeInTheDocument();
    expect(container.querySelector('iframe')).toBeNull();
  });
});
