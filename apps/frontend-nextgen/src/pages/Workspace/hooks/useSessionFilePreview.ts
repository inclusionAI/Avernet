import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import {
  getFileExt,
  getPreviewKind,
  prettifyJsonText,
  SESSION_FILE_TEXT_PREVIEW_MAX_BYTES,
  type SessionFilePreviewKind,
} from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export interface SessionFilePreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  /** 供 <img> / <iframe> 标签直接加载的内容地址，部署态优先直连网关。 */
  contentUrl: string | null;
  /** 文本类自绘预览的文件内容（超 512KB 截断）——iframe 默认样式不可控，改为自绘排版。 */
  text: string | null;
  /** 文本超出截断上限（提示下载查看全文）。 */
  truncated: boolean;
  errorMessage: string | null;
}

const initialState: SessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
  text: null,
  truncated: false,
  errorMessage: null,
};

/** 拉取文件内容并生成站内预览所需数据（objectUrl / 文本）。 */
export function useSessionFilePreview(file: SessionFileView | null): SessionFilePreviewState {
  const [state, setState] = useState<SessionFilePreviewState>(initialState);
  const fileId = file?.fileId ?? null;
  const sessionId = file?.sessionId ?? null;
  const name = file?.name ?? '';
  const mimeType = file?.mimeType ?? '';

  useEffect(() => {
    if (!fileId || !sessionId) {
      setState(initialState);
      return;
    }
    const kind = getPreviewKind(name, mimeType);
    if (kind === 'other') {
      setState({ ...initialState, kind });
      return;
    }
    const contentUrl = sessionFileService.buildContentUrl(sessionId, fileId);
    if (kind === 'text') {
      // 文本类走自绘：fetch 字节（512KB 截断）转文本渲染，边界与字号可控。
      let cancelled = false;
      setState({ ...initialState, kind, status: 'loading', contentUrl });
      void sessionFileService.fetchContentBlob(sessionId, fileId).then(async (result) => {
        if (cancelled) return;
        if (!result.ok) {
          setState({
            ...initialState,
            kind,
            status: 'error',
            contentUrl,
            errorMessage: result.error.friendlyMessage,
          });
          return;
        }
        const truncated = result.data.size > SESSION_FILE_TEXT_PREVIEW_MAX_BYTES;
        const blob = truncated ? result.data.slice(0, SESSION_FILE_TEXT_PREVIEW_MAX_BYTES) : result.data;
        const raw = await blob.text();
        if (cancelled) return;
        // JSON 自动美化缩进（清单 §一）；截断后的半截内容解析失败则回退原文。
        const text = getFileExt(name) === 'json' ? prettifyJsonText(raw) : raw;
        setState({ kind, status: 'ready', contentUrl, text, truncated, errorMessage: null });
      });
      return () => {
        cancelled = true;
      };
    }
    // 图片 / PDF 统一由浏览器标签加载，避免 fetch 字节后再创建 object URL 引入跨域读取。
    setState({ ...initialState, kind, status: 'ready', contentUrl });
  }, [fileId, sessionId, name, mimeType]);

  return state;
}
