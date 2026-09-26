import { sessionFileService } from './sessionFileService';
import { extractPartUrl } from './sessionFileUtils';

const MULTIPART_CONCURRENCY = 3;

export interface SessionFileMultipartOptions {
  mime: string;
  signal: AbortSignal;
  onProgress: (percent: number) => void;
}

export async function uploadMultipartSessionFile(
  sessionId: string,
  fileId: string,
  file: Blob,
  size: number,
  parts: Array<Record<string, unknown>>,
  options: SessionFileMultipartOptions,
): Promise<void> {
  const partCount = parts.length;
  const chunkSize = Math.ceil(size / partCount);
  const completedBytes = new Array<number>(partCount).fill(0);
  let nextPart = 0;
  let firstError: unknown = null;

  const reportProgress = () => {
    const uploaded = completedBytes.reduce((sum, value) => sum + value, 0);
    options.onProgress(Math.min(99, size > 0 ? Math.round((uploaded / size) * 100) : 0));
  };

  const runPart = async (index: number) => {
    const start = index * chunkSize;
    const end = Math.min(start + chunkSize, size);
    const partSize = end - start;
    const chunk = file.slice(start, end);
    const url = extractPartUrl(parts[index]);

    const onPartProgress = (loaded: number) => {
      completedBytes[index] = Math.min(loaded, partSize);
      reportProgress();
    };

    try {
      if (url) {
        await sessionFileService.uploadBytes(url, chunk, {
          mime: options.mime,
          signal: options.signal,
          onProgress: onPartProgress,
        });
      } else {
        await sessionFileService.uploadContent(sessionId, fileId, chunk, {
          mime: options.mime,
          part: index,
          signal: options.signal,
          onProgress: onPartProgress,
        });
      }
      completedBytes[index] = partSize;
      onPartProgress(partSize);
    } catch (err) {
      if ((err as { name?: string })?.name === 'AbortError' || options.signal.aborted) throw err;
      if (!firstError) {
        firstError = err;
      }
      throw err;
    }
  };

  const worker = async () => {
    while (!firstError && !options.signal.aborted) {
      const index = nextPart;
      nextPart += 1;
      if (index >= partCount) return;
      await runPart(index);
    }
  };

  await Promise.allSettled(Array.from({ length: Math.min(MULTIPART_CONCURRENCY, partCount) }, () => worker()));
  if (firstError) throw firstError;
  if (options.signal.aborted) throw new DOMException('Aborted', 'AbortError');
  options.onProgress(99);
}
