import { uploadToUrl } from '@/services/backendApi/bots/botSessionFileController';

export interface UploadIntentView {
  resourceId: string;
  transferId: string;
  uploadType: string;
  httpMethod: string;
  uploadUrl: string | null;
  partSize: number | null;
  partCount: number | null;
  parts: Array<Record<string, unknown>> | null;
  expiresAt: string | null;
}

const MULTIPART_CONCURRENCY = 3;

function stringifyPartHeaders(raw: unknown): Record<string, string> | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).map(([key, value]) => [key, String(value)]));
}

/** 直传字节到 upload_url(SINGLE)或并行 PUT 分片(MULTIPART)。 */
export async function directUpload(
  intent: UploadIntentView,
  file: File,
  signal: AbortSignal,
  onProgress?: (loaded: number, total: number) => void,
) {
  if ((intent.uploadType || '').toUpperCase() === 'MULTIPART' && intent.parts && intent.parts.length > 0) {
    const partSize = intent.partSize ?? Math.ceil(file.size / intent.parts.length);
    const parts = intent.parts;
    const partLoaded = new Array<number>(intent.parts.length).fill(0);
    let nextIndex = 0;
    let firstError: unknown = null;

    const uploadPart = async (index: number) => {
      const p = parts[index] as Record<string, unknown>;
      const partUrl = String(p.upload_url ?? p.url ?? '');
      if (!partUrl) throw new Error(`分片 ${index + 1} 缺少上传凭证`);
      const offsetNum = p.offset !== undefined ? Number(p.offset) : index * partSize;
      const sizeNum = Number(p.size ?? Math.min(partSize, file.size - offsetNum));
      const partBody = file.slice(offsetNum, offsetNum + sizeNum);
      await uploadToUrl(partUrl, String(p.http_method ?? p.method ?? intent.httpMethod ?? 'PUT'), partBody, {
        signal,
        headers: stringifyPartHeaders(p.headers),
        onProgress: (loaded) => {
          partLoaded[index] = loaded;
          onProgress?.(
            partLoaded.reduce((sum, current) => sum + current, 0),
            file.size,
          );
        },
      });
    };

    const worker = async () => {
      while (!firstError) {
        const index = nextIndex;
        nextIndex += 1;
        if (index >= parts.length) return;
        try {
          await uploadPart(index);
        } catch (err) {
          if (!firstError) firstError = err;
          return;
        }
      }
    };

    await Promise.allSettled(Array.from({ length: Math.min(MULTIPART_CONCURRENCY, parts.length) }, () => worker()));
    if (firstError) {
      throw firstError;
    }
    onProgress?.(file.size, file.size);
    return;
  }
  if (!intent.uploadUrl) throw new Error('缺少上传凭证');
  await uploadToUrl(intent.uploadUrl, intent.httpMethod ?? 'PUT', await file.arrayBuffer(), {
    signal,
    onProgress,
  });
}
