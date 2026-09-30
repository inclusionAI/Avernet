const DIRECT_DOWNLOAD_LIMIT = 30 * 1024 * 1024;

const MAX_DOWNLOAD_POLLS = 30;

type ExternalDownloadBody = {
  status?: string;
  retry_after_seconds?: number;
  delivery?: string;
  download_url?: string;
  url?: string;
  data?: { download_url?: string; url?: string };
  message?: string;
  detail?: string;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function extractDownloadUrl(body: ExternalDownloadBody): string | undefined {
  return body.download_url ?? body.url ?? body.data?.download_url ?? body.data?.url;
}

async function readErrorDetail(resp: Response): Promise<string> {
  let detail = '';
  try {
    const data = (await resp.json()) as ExternalDownloadBody;
    detail = data.detail ?? data.message ?? '';
  } catch {
    /* 非 JSON 响应，用 HTTP 状态兜底 */
  }
  return detail;
}

/** 大文件下载走引擎 data-plane 的 202 → external_url 协议。 */
export function isLargeBotSessionFile(sizeBytes: number | null | undefined): boolean {
  return typeof sizeBytes === 'number' && sizeBytes > DIRECT_DOWNLOAD_LIMIT;
}

/** 大文件 content 返回 JSON/OSS 外链，解析后交给浏览器跳转下载。 */
export async function fetchExternalDownloadUrl(contentUrl: string): Promise<string> {
  let polls = 0;
  while (polls < MAX_DOWNLOAD_POLLS) {
    polls += 1;
    const resp = await fetch(contentUrl, { method: 'GET', credentials: 'include' });
    let body: ExternalDownloadBody = {};

    if (resp.ok) {
      try {
        body = (await resp.json()) as ExternalDownloadBody;
      } catch {
        throw new Error('下载响应无效');
      }
    } else {
      const detail = await readErrorDetail(resp);
      throw new Error(detail || `下载失败: ${resp.status}`);
    }

    if (resp.status === 202 || body.status === 'preparing_download') {
      if (resp.status === 202 && body.status !== 'preparing_download') {
        throw new Error('下载响应无效');
      }
      const seconds = Math.min(Math.max(body.retry_after_seconds ?? 2, 1), 10);
      await sleep(seconds * 1000);
      continue;
    }

    const downloadUrl = extractDownloadUrl(body);
    if (!downloadUrl) throw new Error('下载响应无效');
    const parsed = new URL(downloadUrl);
    if (parsed.protocol !== 'https:') throw new Error('下载链接不安全');
    return downloadUrl;
  }
  throw new Error('下载准备超时，请稍后重试');
}
