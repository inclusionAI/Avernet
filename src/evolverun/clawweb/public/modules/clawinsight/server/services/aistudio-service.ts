export const SESSION_ANALYSIS_SNAPSHOT_ID = 62310015;
export const DEFAULT_AISTUDIO_PROJECT_NAME = "alipay_ctuop_dev";
// Credentials are deployment inputs. Public source must never provide a token fallback.
const API_BASE = "https://aistudio.alipay.com/api/v2.0/guiApi";

export type AistudioConfig = { token: string; projectName: string; snapshotId: number };
export type AistudioStatus = "running" | "success" | "failed" | "stopped";
export type AistudioJobStatusDetail = {
  status: AistudioStatus;
  rawStatus: string;
  errorMessage: string | null;
};

export function resolveAistudioConfig(env = process.env): AistudioConfig {
  const token = env.CLAWWEB_AISTUDIO_TOKEN?.trim() || "";
  const projectName = env.CLAWWEB_AISTUDIO_PROJECT_NAME?.trim() || DEFAULT_AISTUDIO_PROJECT_NAME;
  return { token, projectName, snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID };
}

export class AistudioService {
  constructor(private readonly config: AistudioConfig, private readonly fetcher = fetch) {}

  async execute(userId: string, globalParam: Record<string, string>, snapshotId = this.config.snapshotId): Promise<string> {
    if (!this.config.token || !this.config.projectName) throw new Error("AIS token/projectName 未配置");
    const response = await this.fetcher(`${API_BASE}/executeSnapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        userNumber: userId, token: this.config.token, snapshotId,
        userGlobalParameter: globalParam, inputParams: null, projectName: this.config.projectName,
      }),
      signal: AbortSignal.timeout(60_000),
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`AIS executeSnapshot HTTP ${response.status}: ${raw.slice(0, 1000)}`);
    let payload: {
      success?: boolean;
      errorCode?: string;
      errorMessage?: string;
      message?: string;
      data?: { jobId?: string | number } | unknown;
      [key: string]: unknown;
    };
    try { payload = JSON.parse(raw) as typeof payload; }
    catch { throw new Error(`AIS executeSnapshot 返回非 JSON: ${raw.slice(0, 1000)}`); }
    const data = payload.data as { jobId?: string | number } | undefined;
    if (!payload.success || data?.jobId == null) {
      // AIStudio's standard failure envelope uses errorCode/errorMessage.
      // Preserve the parsed response so dispatch failures never collapse to
      // an unactionable "{}" when message/data are absent.
      const detail = JSON.stringify(payload).slice(0, 1500);
      throw new Error(`AIS executeSnapshot 失败: ${detail || "空响应"}`);
    }
    return String(data.jobId);
  }

  async getJobStatus(jobId: string): Promise<AistudioStatus> {
    return (await this.getJobStatusDetail(jobId)).status;
  }

  async stopExecution(jobId: string): Promise<void> {
    if (!this.config.token) throw new Error("AIS token 未配置");
    const recordId = jobId.trim();
    if (!recordId || recordId.length > 255 || /[\r\n\0]/u.test(recordId)) {
      throw new Error("AIS recordId 格式不合法");
    }
    const url = new URL(`${API_BASE}/stopExecution`);
    url.searchParams.set("recordId", recordId);
    url.searchParams.set("token", this.config.token);
    const response = await this.fetcher(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw new Error(`AIS stopExecution HTTP ${response.status}`);
    let payload: { success?: boolean; message?: string };
    try { payload = await response.json() as typeof payload; }
    catch { throw new Error("AIS stopExecution 返回非 JSON"); }
    if (!payload.success) throw new Error("AIS stopExecution 返回失败");
  }

  async getJobStatusDetail(jobId: string): Promise<AistudioJobStatusDetail> {
    if (!this.config.token) throw new Error("AIS token 未配置");
    const url = new URL(`${API_BASE}/getAlgoJobStatusAndResult`);
    url.searchParams.set("jobId", jobId);
    url.searchParams.set("token", this.config.token);
    const response = await this.fetcher(url, { headers: { Accept: "*/*" }, signal: AbortSignal.timeout(20_000) });
    if (!response.ok) throw new Error(`AIS status HTTP ${response.status}`);
    const payload = await response.json() as {
      success?: boolean;
      message?: string;
      data?: {
        executeStatus?: { status?: string; errorMessage?: string; errorMsg?: string; message?: string };
        errorMessage?: string;
        errorMsg?: string;
        message?: string;
        result?: { errorMessage?: string; message?: string };
      };
    };
    if (!payload.success) throw new Error("AIS status 返回失败");
    const rawStatus = payload.data?.executeStatus?.status ?? "notfound";
    const status: AistudioStatus = ["default", "prepare", "pending", "queued", "scheduling", "running"].includes(rawStatus)
      ? "running" : rawStatus === "success" ? "success" : rawStatus === "stopped" ? "stopped" : "failed";
    const candidates = [
      payload.data?.executeStatus?.errorMessage,
      payload.data?.executeStatus?.errorMsg,
      payload.data?.executeStatus?.message,
      payload.data?.errorMessage,
      payload.data?.errorMsg,
      payload.data?.message,
      payload.data?.result?.errorMessage,
      payload.data?.result?.message,
      payload.message,
    ];
    const errorMessage = candidates.find((value) => typeof value === "string" && value.trim())?.trim() ?? null;
    return { status, rawStatus, errorMessage: errorMessage?.slice(0, 4000) ?? null };
  }
}
