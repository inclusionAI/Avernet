import { Badge, Button } from '@/components/ui';
import type { SessionFilePreviewState } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { formatFileSize } from '@/services/workspace/sessionFileUtils';
import { ArrowLeft, Download, FileText, FolderOpen, Share2 } from 'lucide-react';
import { isMarkdownFile, renderSessionFileMarkdown } from './sessionFileMarkdown';
/** 预览面板所需的最小文件字段（群聊 / 单聊共用）。 */
export interface PreviewFile {
  name: string;
  size: number;
}

const KIND_META: Record<
  Exclude<SessionFilePreviewState['kind'], 'other'>,
  { label: string; tone: 'primary' | 'warning' }
> = {
  text: { label: '文本', tone: 'primary' },
  pdf: { label: 'PDF', tone: 'warning' },
  image: { label: '图片', tone: 'primary' },
  video: { label: '视频', tone: 'primary' },
  html: { label: '网页', tone: 'primary' },
};

interface UnsupportedPreviewProps {
  onDownload: () => void;
}

function UnsupportedPreview({ onDownload }: UnsupportedPreviewProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center py-10 text-center text-muted-foreground">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <FileText className="h-6 w-6" aria-hidden />
      </div>
      <p className="m-0 text-sm font-semibold text-foreground">该文件类型暂不支持预览</p>
      <p className="mt-2 text-sm leading-6">请下载后使用对应软件查看</p>
      <Button
        size="md"
        variant="secondary"
        className="mt-5 bg-muted/40 text-foreground hover:bg-muted"
        leftIcon={<Download className="h-3.5 w-3.5" aria-hidden />}
        onClick={onDownload}
      >
        下载文件
      </Button>
    </div>
  );
}

interface PreviewBodyProps {
  file: PreviewFile;
  preview: SessionFilePreviewState;
  onDownload: () => void;
}

function PreviewBody({ file, preview, onDownload }: PreviewBodyProps) {
  if (preview.kind === 'other' || preview.status === 'unsupported') {
    return <UnsupportedPreview onDownload={onDownload} />;
  }
  if (preview.status === 'loading') {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 py-10 text-muted-foreground">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <span className="text-sm">正在加载预览…</span>
      </div>
    );
  }
  if (preview.status === 'error') {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
        <p className="m-0 text-sm font-semibold text-destructive">预览加载失败</p>
        <p className="m-0 text-xs">{preview.errorMessage ?? '请稍后重试或下载文件查看。'}</p>
      </div>
    );
  }

  if (preview.kind === 'image' && preview.contentUrl) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4">
        <img
          src={preview.contentUrl}
          alt={file.name}
          className="max-h-full max-w-full rounded-xl border border-border object-contain"
        />
      </div>
    );
  }

  if (preview.kind === 'video' && preview.contentUrl) {
    /* 视频内嵌播放器：原生 video 标签带播放控制，居中自适应（清单 §一）。 */
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4">
        <video src={preview.contentUrl} controls className="max-h-full max-w-full rounded-xl" />
      </div>
    );
  }

  if (preview.kind === 'text' && preview.text !== null) {
    /* 文本自绘（验收微调）：md 走富文本渲染，json 美化缩进，其余等宽原文；
       px-6 py-4 呼吸边，替换 iframe（浏览器纯文本样式不可控）。
       滚动契约：分支根 flex-1（非 h-full 百分比——overflow 父级下解析不可靠），
       最内层 overflow-y-auto 独占滚动。 */
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {isMarkdownFile(file.name) ? (
          <div
            className="markdown-body app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-4 text-sm leading-6 text-foreground"
            dangerouslySetInnerHTML={{ __html: renderSessionFileMarkdown(preview.text) }}
          />
        ) : (
          <pre
            data-testid="session-files-plain-text"
            className="app-scrollbar m-0 min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-all px-6 py-4 font-mono text-xs leading-6 text-foreground"
          >
            {preview.text}
          </pre>
        )}
        {preview.truncated ? (
          <div className="shrink-0 border-t border-border px-6 py-3 text-xs text-muted-foreground">
            内容较长，仅显示部分，请下载查看完整内容。
          </div>
        ) : null}
      </div>
    );
  }

  if (preview.kind === 'html' && preview.contentUrl) {
    /* 网页沙箱渲染（清单 §一）：sandbox 空值 = 全限制（禁脚本/禁同源/禁弹窗），
       零 XSS 面；静态渲染页面内容。 */
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <iframe title={file.name} src={preview.contentUrl} sandbox="" className="min-h-0 w-full flex-1 bg-background" />
      </div>
    );
  }

  if (preview.kind === 'pdf' && preview.contentUrl) {
    return (
      /* 平直铺满：去掉圆角与边框（副屏内部为平直分区风格），iframe 拿到全部可用高度。 */
      <div className="flex min-h-0 flex-1 flex-col">
        <iframe title={file.name} src={preview.contentUrl} className="min-h-0 w-full flex-1 bg-background" />
      </div>
    );
  }

  return <UnsupportedPreview onDownload={onDownload} />;
}

interface PreviewPaneProps {
  file: PreviewFile | null;
  filesStateEmpty: boolean;
  preview: SessionFilePreviewState;
  onDownload: () => void;
  onShare?: () => void;
  /** 下钻模式：传入即在预览头渲染「返回文件列表」导航（合并原独立导航层，释放纵向空间）。 */
  onBack?: () => void;
}

export function PreviewPane({ file, filesStateEmpty, preview, onDownload, onShare, onBack }: PreviewPaneProps) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-card">
      {file ? (
        <>
          {/* 两行预览头：工具行（返回 + 文件操作）与 meta 行（文件名 + 类型 badge + 大小）分离，
              最小副屏宽度下 badge 不再与长文件名/操作按钮挤同一行。 */}
          <header className="flex flex-col gap-1.5 border-b border-border px-4 py-2.5">
            <div className="flex items-center gap-2">
              {onBack ? (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="返回文件列表"
                  leftIcon={<ArrowLeft className="h-3.5 w-3.5" aria-hidden />}
                  onClick={onBack}
                  className="px-2 text-muted-foreground hover:text-foreground"
                >
                  返回文件列表
                </Button>
              ) : null}
              <div className="ml-auto flex shrink-0 items-center gap-2">
                {onShare ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    leftIcon={<Share2 className="h-3.5 w-3.5" aria-hidden />}
                    onClick={() => void onShare()}
                  >
                    分享
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<Download className="h-3.5 w-3.5" aria-hidden />}
                  onClick={onDownload}
                >
                  下载
                </Button>
              </div>
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <h3 className="m-0 truncate text-sm font-semibold text-foreground">{file.name}</h3>
              {preview.kind !== 'other' && (
                <Badge tone={KIND_META[preview.kind].tone}>{KIND_META[preview.kind].label}</Badge>
              )}
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatFileSize(file.size)}</span>
            </div>
          </header>

          {/* 滚动契约（验收修复）：外层不再 overflow-auto（h-full 百分比在其下解析不可靠），
              改为 flex 容器；各预览分支根 flex-1，滚动收敛到最内层 overflow-y-auto。 */}
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <PreviewBody file={file} preview={preview} onDownload={onDownload} />
          </div>
        </>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center px-4 text-center sm:px-8">
          <div className="flex max-w-sm flex-col items-center text-muted-foreground">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <FolderOpen className="h-6 w-6" aria-hidden />
            </div>
            <p className="m-0 text-sm font-semibold text-foreground">
              {filesStateEmpty ? '会话内暂时没有文件' : '选择一个文件开始预览'}
            </p>
            <p className="mt-2 text-sm leading-6">
              {filesStateEmpty
                ? '点击左侧「上传文件」即可上传资源，所有成员均可查看与管理。'
                : '文件预览会显示在这里。'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default PreviewPane;
