/** Read a response stream without imposing a caller-specific status policy. */
export async function readResponseBody(response: Response, limit: number): Promise<Buffer> {
  const reader = response.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  const chunks: Buffer[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > limit) throw new Error("文件读取响应过大");
      chunks.push(Buffer.from(value));
    }
  } finally { await reader.cancel(); }
  return Buffer.concat(chunks);
}

export async function readBoundedBody(response: Response, limit: number): Promise<Buffer> {
  if (!response.ok) {
    await response.body?.cancel();
    throw Object.assign(new Error(`文件读取失败（${response.status}）`), { statusCode: 502 });
  }
  if (!response.body) throw new Error("文件读取响应为空");
  return readResponseBody(response, limit);
}

/** Preserve container HTTP status, including errors and empty bodies. */
export async function runtimeResponse(response: Response) {
  const text = (await readResponseBody(response, 16 * 1024 * 1024)).toString("utf8");
  let body: unknown = text;
  if (text) { try { body = JSON.parse(text); } catch { /* plain text */ } }
  return { status: response.status, body };
}
