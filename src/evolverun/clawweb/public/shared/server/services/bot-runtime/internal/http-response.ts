/** Bound response memory while preserving the container's HTTP status and body. */
export async function runtimeResponse(response: Response) {
  const reader = response.body?.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (reader) try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 16 * 1024 * 1024) throw new Error("Container HTTP response exceeds 16 MiB");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const text = Buffer.concat(chunks).toString("utf8");
  let body: unknown = text;
  if (text) { try { body = JSON.parse(text); } catch { /* plain text */ } }
  return { status: response.status, body };
}
