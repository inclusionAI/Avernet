import { createHash } from "node:crypto";
import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import type { SessionEvidence } from "../contracts.js";
import { DEFAULT_INSIGHT_OSS_BUCKET, parseInsightEvidenceRef } from "../evidence-ref.js";
import { validateSessionEvidence } from "../evidence-validation.js";
import { InsightDataNotReadyError } from "./insight-read-provider.js";
import type { EvidenceProvider, EvidenceReadOptions } from "./evidence-provider.js";

export class FileEvidenceProvider implements EvidenceProvider {
  constructor(
    private readonly fixtureRoot: string,
    private readonly bucketName: string = DEFAULT_INSIGHT_OSS_BUCKET,
  ) {}

  async readEvidence(payloadRef: string, options: EvidenceReadOptions = {}): Promise<SessionEvidence> {
    let objectKey: string;
    try {
      objectKey = parseInsightEvidenceRef(payloadRef, { expectedBucket: this.bucketName }).objectKey;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightDataNotReadyError(`Evidence 路径不合法: ${message}`);
    }

    const root = await realpath(this.fixtureRoot).catch(() => resolve(this.fixtureRoot));
    const target = resolve(join(root, objectKey));
    const relativePath = relative(root, target);
    if (relativePath.startsWith("..") || isAbsolute(relativePath)) {
      throw new InsightDataNotReadyError("Evidence 路径越界");
    }
    try {
      const payload = await readFile(target);
      if (options.expectedEtag) {
        const actualEtag = createHash("sha256").update(payload).digest("hex");
        const expectedEtag = options.expectedEtag.trim().toLowerCase();
        if (actualEtag !== expectedEtag) {
          throw new Error(`etag mismatch: expected ${expectedEtag}, got ${actualEtag}`);
        }
      }
      return validateSessionEvidence(JSON.parse(payload.toString("utf8")));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightDataNotReadyError(`Evidence 不可用: ${payloadRef}: ${message}`);
    }
  }
}
