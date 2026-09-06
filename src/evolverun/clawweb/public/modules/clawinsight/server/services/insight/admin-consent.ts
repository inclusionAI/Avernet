import { createHmac, timingSafeEqual } from "node:crypto";

export type AdminConsentClaims = {
  improvementId: number;
  ownerUserId: string;
  botId: string;
  sourceRuleId: string;
  ruleVersion: number;
  exp: number;
};

function secret(): string {
  const value = process.env.INSIGHT_ADMIN_CONSENT_SECRET?.trim()
    || process.env.INSIGHT_INTERNAL_WRITE_TOKEN?.trim();
  if (!value) throw new Error("INSIGHT_ADMIN_CONSENT_SECRET 未配置");
  return value;
}

function encode(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

function signature(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

export function createAdminConsentToken(claims: AdminConsentClaims): string {
  const payload = encode(claims);
  return `${payload}.${signature(payload)}`;
}

export function verifyAdminConsentToken(
  token: string,
  expected: Pick<AdminConsentClaims, "improvementId" | "ownerUserId" | "botId" | "sourceRuleId" | "ruleVersion">,
  now = Math.floor(Date.now() / 1000),
): AdminConsentClaims {
  const [payload, actualSignature] = token.split(".");
  if (!payload || !actualSignature) throw new Error("管理员授权链接无效");
  const expectedSignature = signature(payload);
  const actual = Buffer.from(actualSignature, "utf8");
  const expectedBuffer = Buffer.from(expectedSignature, "utf8");
  if (actual.length !== expectedBuffer.length || !timingSafeEqual(actual, expectedBuffer)) {
    throw new Error("管理员授权链接签名无效");
  }
  let claims: AdminConsentClaims;
  try {
    claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as AdminConsentClaims;
  } catch {
    throw new Error("管理员授权链接内容无效");
  }
  if (!Number.isInteger(claims.improvementId) || claims.improvementId !== expected.improvementId
    || claims.ownerUserId !== expected.ownerUserId
    || claims.botId !== expected.botId
    || claims.sourceRuleId !== expected.sourceRuleId
    || claims.ruleVersion !== expected.ruleVersion
    || !Number.isInteger(claims.exp) || claims.exp < now) {
    throw new Error("管理员授权链接已过期或作用域已变化");
  }
  return claims;
}
