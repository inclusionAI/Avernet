export async function readBoundedBody(response: Response, limit: number): Promise<Buffer> {
  if (!response.ok) {
    await response.body?.cancel();
    throw Object.assign(new Error(`文件读取失败（${response.status}）`), { statusCode: 502 });
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("文件读取响应为空");
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
