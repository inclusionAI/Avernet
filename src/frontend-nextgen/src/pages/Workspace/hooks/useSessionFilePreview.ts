import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { getPreviewKind, type SessionFilePreviewKind } from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export interface SessionFilePreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  /** 供 <img> / <iframe> 标签直接加载的内容地址，部署态优先直连网关。 */
  contentUrl: string | null;
  errorMessage: string | null;
}

const initialState: SessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
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
    // 图片 / PDF / 文本统一由浏览器标签加载，避免 fetch 字节后再创建 object URL 引入跨域读取。
    const contentUrl = sessionFileService.buildContentUrl(sessionId, fileId);
    setState({ ...initialState, kind, status: 'ready', contentUrl });
  }, [fileId, sessionId, name, mimeType]);

  return state;
}
