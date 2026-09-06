import { describe, expect, it } from "vitest";
import { stripLeadingSenderMetadata } from "../timeline.js";

describe("stripLeadingSenderMetadata", () => {
  it("removes a leading Sender JSON block and transport timestamp", () => {
    const text = `Sender (untrusted metadata):\n\`\`\`json\n{\n  "label": "gateway-client",\n  "id": "gateway-client"\n}\n\`\`\`\n\n[Tue 2026-08-11 10:37 GMT+8] 用户真实输入`;
    expect(stripLeadingSenderMetadata(text)).toBe("用户真实输入");
  });

  it("does not remove an invalid or embedded Sender block", () => {
    const invalid = "Sender (untrusted metadata): not-json\n用户输入";
    const embedded = "请解释 Sender (untrusted metadata): 这段文字";
    expect(stripLeadingSenderMetadata(invalid)).toBe(invalid);
    expect(stripLeadingSenderMetadata(embedded)).toBe(embedded);
  });
});
