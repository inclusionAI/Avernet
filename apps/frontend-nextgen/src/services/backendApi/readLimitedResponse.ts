/**
 * 体超限错误：2xx 预览体超限时对调用方上抛；!ok 响应由 httpClient 降级为按 HTTP 状态
 * 处置（鉴权/错误语义优先，预览大小限制不得覆盖，见 executeBackendRequest）。
 */
export class ResponseTooLargeError extends Error {
  constructor(limit: number) {
    super(`内容超过 ${formatPreviewLimit(limit)}，暂不支持在线预览`);
    this.name = 'ResponseTooLargeError';
  }
}

function formatPreviewLimit(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MiB` : `${Math.round(bytes / 1024)} KiB`;
}

/** Opt-in byte limit for small inline previews. Cancellation also closes chunked responses. */
export async function readLimitedResponse(response: Response, limit: number): Promise<string> {
  if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('Invalid response byte limit');
  const tooLarge = () => new ResponseTooLargeError(limit);
  const declared = Number(response.headers.get('content-length'));
  if (declared > limit) {
    await response.body?.cancel().catch(() => {});
    throw tooLarge();
  }
  if (!response.body) return '';
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let bytes = 0;
  let text = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > limit) throw tooLarge();
      text += decoder.decode(value, { stream: true });
    }
    return text + decoder.decode();
  } finally {
    try {
      // cancel 失败不应吞掉原始错误：体超限/网络错误优先上抛（httpClient 按类型识别降级）。
      await reader.cancel().catch(() => {});
    } finally {
      reader.releaseLock();
    }
  }
}
