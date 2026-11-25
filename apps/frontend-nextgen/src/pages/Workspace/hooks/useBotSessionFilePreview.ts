/** useBotSessionFilePreview — 单聊会话文件预览，与群聊共用 PreviewPane(contentUrl/text 自绘)。 */
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import {
  getFileExt,
  getPreviewKind,
  prettifyJsonText,
  SESSION_FILE_TEXT_PREVIEW_MAX_BYTES,
  type SessionFilePreviewKind,
} from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export interface BotSessionFilePreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  /** 供 <img> / <iframe> 标签直接加载的网关内容地址。 */
  contentUrl: string | null;
  /** 文本类自绘预览的文件内容（超 512KB 截断）——与群聊 useSessionFilePreview 同构。 */
  text: string | null;
  /** 文本超出截断上限（提示下载查看全文）。 */
  truncated: boolean;
  errorMessage: string | null;
}

const initialState: BotSessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
  text: null,
  truncated: false,
  errorMessage: null,
};

export interface UseBotSessionFilePreviewParams {
  botId: string | null;
  sessionId: string | null;
  userId: string | null;
  ownerId?: string;
  stage?: BotRuntimeStage;
  /** 多实例选中实例(openspec add-session-files-device-uuid 扩展段),缺省不携带 */
  deviceUuid?: string;
  /** 好友会话身份(2026-11-19 需求追加:全链携带 f_user_id),缺省不携带 */
  friendUserId?: string;
}

export function useBotSessionFilePreview(
  file: BotSessionFileView | null,
  params: UseBotSessionFilePreviewParams,
): BotSessionFilePreviewState {
  const [state, setState] = useState<BotSessionFilePreviewState>(initialState);
  const resourceId = file?.resourceId ?? null;
  const name = file?.displayName ?? '';
  const { botId, sessionId, userId, ownerId, stage, deviceUuid, friendUserId } = params;

  useEffect(() => {
    if (!resourceId || !botId || !sessionId || !userId) {
      setState(initialState);
      return;
    }
    const kind = getPreviewKind(name);
    if (kind === 'other') {
      setState({ ...initialState, kind });
      return;
    }
    // 显式位参(2026-11-19 勘误):条件 spread 会让缺省位被后值顶替,undefined 占位等价省略。
    const contentUrl = botSessionFileService.resolveContentUrl(
      botId,
      sessionId,
      resourceId,
      userId,
      ownerId,
      'inline',
      stage,
      deviceUuid,
      friendUserId,
    );
    if (kind === 'text') {
      // 文本类走自绘：fetch 字节（512KB 截断）转文本渲染，与群聊侧同构。
      let cancelled = false;
      setState({ ...initialState, kind, status: 'loading', contentUrl });
      void botSessionFileService
        .fetchBlob(
          botId,
          sessionId,
          resourceId,
          userId,
          ownerId,
          'inline',
          stage,
          deviceUuid,
          friendUserId,
        )
        .then(async (result) => {
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
    setState({ ...initialState, kind, status: 'ready', contentUrl });
  }, [resourceId, name, botId, sessionId, userId, ownerId, stage, deviceUuid, friendUserId]);

  return state;
}
