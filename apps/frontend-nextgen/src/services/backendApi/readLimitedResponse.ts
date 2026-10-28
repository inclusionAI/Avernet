/** Opt-in byte limit for small inline previews. Cancellation also closes chunked responses. */
export async function readLimitedResponse(response: Response, limit: number): Promise<string> {
  if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('Invalid response byte limit');
  const tooLarge = () => new Error(`内容超过 ${limit / 1024 / 1024} MiB，暂不支持在线预览`);
  const declared = Number(response.headers.get('content-length'));
  if (declared > limit) {
    await response.body?.cancel();
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
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
