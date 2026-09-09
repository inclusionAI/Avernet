import type { BotManagementVerb } from '@/domain/botWorkshop';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';

const DELETE_NOT_SUPPORTED_MESSAGE = 'Operation not supported for this bot';

export function getBotManagementErrorMessage(action: BotManagementVerb, error: unknown, fallback = '操作失败'): string {
  if (action === 'delete' && error instanceof BackendRequestError && error.data && typeof error.data === 'object') {
    const envelope = error.data as { code?: string | number; message?: unknown };
    if (Number(envelope.code) === 409000 && envelope.message === DELETE_NOT_SUPPORTED_MESSAGE) {
      return '该 Bot 不允许删除';
    }
  }
  return extractFriendlyErrorMessage(error, fallback);
}
