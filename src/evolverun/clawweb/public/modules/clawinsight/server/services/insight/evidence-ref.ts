export const DEFAULT_INSIGHT_OSS_BUCKET = "antsys-agentclaw-prod";
export const INSIGHT_EVIDENCE_ROOT = "evolution";
export const INSIGHT_EVIDENCE_ENVIRONMENTS = ["pre", "prod"] as const;

export type InsightEvidenceEnvironment = typeof INSIGHT_EVIDENCE_ENVIRONMENTS[number];

export type ParseInsightEvidenceRefOptions = {
  expectedBucket?: string;
  expectedEnvironment?: string;
  allowedEnvironments?: readonly InsightEvidenceEnvironment[];
  expectedUserId?: string;
  expectedBotId?: string;
  expectedSourceDt?: string;
  expectedSessionId?: string;
};

export type ParsedInsightEvidenceRef = {
  bucket: string;
  environment: InsightEvidenceEnvironment;
  objectKey: string;
  userId: string;
  botId: string;
  sourceDt: string;
  sessionId: string;
};

function decodeSegment(value: string, name: string): string {
  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    throw new Error(`${name} URL 编码不合法`);
  }
  if (!decoded || decoded === "." || decoded === ".." || decoded.includes("/")) {
    throw new Error(`${name} 路径段不合法`);
  }
  return decoded;
}

export function parseInsightEvidenceRef(
  payloadRef: string,
  options: ParseInsightEvidenceRefOptions = {},
): ParsedInsightEvidenceRef {
  let url: URL;
  try {
    url = new URL(payloadRef);
  } catch {
    throw new Error("payload_ref 必须是完整 OSS URI");
  }
  if (url.protocol !== "oss:" || !url.hostname || url.search || url.hash) {
    throw new Error("payload_ref 必须是无查询参数的 oss:// URI");
  }

  const expectedBucket = options.expectedBucket ?? DEFAULT_INSIGHT_OSS_BUCKET;
  if (url.hostname !== expectedBucket) {
    throw new Error(`OSS Bucket 必须为 ${expectedBucket}`);
  }

  const segments = url.pathname.split("/").filter(Boolean);
  if (segments.length !== 7 || segments[0] !== INSIGHT_EVIDENCE_ROOT || segments[2] !== "evidence") {
    throw new Error(
      `OSS 路径必须为 ${INSIGHT_EVIDENCE_ROOT}/{env}/evidence/{user_id}/{bot_id}/{yyyyMMdd}/{session_id}.json`,
    );
  }

  const environment = decodeSegment(segments[1], "env");
  if (!(INSIGHT_EVIDENCE_ENVIRONMENTS as readonly string[]).includes(environment)) {
    throw new Error("env 只允许 pre 或 prod");
  }
  if (options.expectedEnvironment && environment !== options.expectedEnvironment) {
    throw new Error(`OSS env 必须为 ${options.expectedEnvironment}`);
  }
  if (options.allowedEnvironments && !options.allowedEnvironments.includes(environment as InsightEvidenceEnvironment)) {
    throw new Error(`OSS env 只允许 ${options.allowedEnvironments.join(" 或 ")}`);
  }

  const userId = decodeSegment(segments[3], "user_id");
  const botId = decodeSegment(segments[4], "bot_id");
  const sourceDt = decodeSegment(segments[5], "yyyyMMdd");
  const fileName = decodeSegment(segments[6], "session 文件名");
  if (!/^\d{8}$/.test(sourceDt)) throw new Error("OSS 日期目录必须为 yyyyMMdd");
  if (!fileName.endsWith(".json")) throw new Error("Evidence 对象必须以 .json 结尾");
  const sessionId = fileName.slice(0, -5);
  if (!sessionId) throw new Error("session_id 不能为空");

  const expectedValues: Array<[string, string | undefined, string]> = [
    [userId, options.expectedUserId, "user_id"],
    [botId, options.expectedBotId, "bot_id"],
    [sourceDt, options.expectedSourceDt, "yyyyMMdd"],
    [sessionId, options.expectedSessionId, "session_id"],
  ];
  for (const [actual, expected, name] of expectedValues) {
    if (expected !== undefined && actual !== expected) {
      throw new Error(`payload_ref 中的 ${name} 与失败任务字段不一致`);
    }
  }

  return {
    bucket: url.hostname,
    environment: environment as InsightEvidenceEnvironment,
    objectKey: segments.map((segment) => decodeURIComponent(segment)).join("/"),
    userId,
    botId,
    sourceDt,
    sessionId,
  };
}
