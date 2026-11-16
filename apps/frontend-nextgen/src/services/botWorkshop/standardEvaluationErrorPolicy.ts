const NOT_FOUND_MESSAGE_RE = /^(?:not\s*found|not_found)$/i;

export const SAMPLE_SET_NOT_FOUND_MESSAGE = '获取评测样本集失败：未找到对应的 MASA 评测智能体';

export const SAMPLE_SET_FETCH_FAILED_MESSAGE = '获取评测样本集失败';

type MaybeError = { status?: unknown; message?: unknown };

function readErrorStatus(error: unknown): unknown {
  if (error === null || error === undefined) return undefined;
  return (error as MaybeError).status;
}

function readErrorMessage(error: unknown): string {
  if (error === null || error === undefined) return '';
  if (typeof error === 'string') return error;
  const message = (error as MaybeError).message;
  return typeof message === 'string' ? message : '';
}

export function isSampleSetNotFoundError(error: unknown): boolean {
  return (
    readErrorStatus(error) === 404 ||
    NOT_FOUND_MESSAGE_RE.test(readErrorMessage(error).trim())
  );
}

export function getSampleSetErrorMessage(
  error: unknown,
  fallback = SAMPLE_SET_FETCH_FAILED_MESSAGE,
): string {
  if (isSampleSetNotFoundError(error)) return SAMPLE_SET_NOT_FOUND_MESSAGE;
  const message = readErrorMessage(error).trim();
  return message || fallback;
}
