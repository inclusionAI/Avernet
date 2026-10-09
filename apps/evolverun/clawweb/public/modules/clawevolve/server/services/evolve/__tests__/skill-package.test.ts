import { describe, expect, it, vi } from "vitest";
import { encodeSkillPackage, MAX_SKILL_DIRECTORY_BYTES, readSkillPackage } from "../skill-package.js";

describe("CW Skill package reads", () => {
  it("matches the existing Python canonical ZIP bytes for ASCII and UTF-8 paths", () => {
    const result = encodeSkillPackage([
      { path: "references/中文.bin", bytes: Buffer.from([0, 1, 255]) },
      { path: "SKILL.md", bytes: Buffer.from("---\nname: example\n---\nHello\n") },
    ]);
    // Python zipfile: fixed 1980 timestamp, UNIX 0644, deflate level 9.
    expect(result.packageBytes.toString("base64")).toBe("UEsDBBQAAAAIAAAAIQDkK96eHQAAABwAAAAIAAAAU0tJTEwubWTT1dXlykvMTbVSSK1IzC3ISeXSBYp4pObk5HMBAFBLAwQUAAAICAAAACEA3gdYywUAAAADAAAAFQAAAHJlZmVyZW5jZXMv5Lit5paHLmJpbmNg/A8AUEsBAhQDFAAAAAgAAAAhAOQr3p4dAAAAHAAAAAgAAAAAAAAAAAAAAKSBAAAAAFNLSUxMLm1kUEsBAhQDFAAACAgAAAAhAN4HWMsFAAAAAwAAABUAAAAAAAAAAAAAAKSBQwAAAHJlZmVyZW5jZXMv5Lit5paHLmJpblBLBQYAAAAAAgACAHkAAAB7AAAAAAA=");
  });

  it.each([MAX_SKILL_DIRECTORY_BYTES + 1, Number.NaN, -1, undefined, "12"])("rejects bad/oversized directory sizes before reads: %s", async (size) => {
    const post = vi.fn(async () => new Response(JSON.stringify({ data: { files: [{
      is_dir: false, relative_path: "SKILL.md", size,
    }] } })));
    await expect(readSkillPackage("/skills-local/example", post)).rejects.toThrow(/大小|Skill 太大/);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/api/file/list");
  });

  it("counts recursive and ignored file sizes, not directory metadata", async () => {
    const post = vi.fn(async (_path: string, body: Record<string, unknown>) => new Response(JSON.stringify({ data: { files:
      body.dir_path === "/skills-local/example" ? [
        { is_dir: true, relative_path: "refs", size: 0 },
        { is_dir: true, relative_path: ".git", size: 0 },
      ] : body.dir_path === "/skills-local/example/refs" ? [
        { is_dir: false, relative_path: "a.bin", size: 70 * 1024 * 1024 },
      ] : [{ is_dir: false, relative_path: "object", size: 40 * 1024 * 1024 }],
    } })));
    await expect(readSkillPackage("/skills-local/example", post)).rejects.toMatchObject({ statusCode: 413 });
    expect(post).toHaveBeenCalledTimes(3);
    expect(post.mock.calls.every(([path]) => path === "/api/file/list")).toBe(true);
  });

  it("rejects a failed subdirectory listing without downloading any files", async () => {
    const post = vi.fn(async (_path: string, body: Record<string, unknown>) => body.dir_path === "/skills-local/example"
      ? new Response(JSON.stringify({ data: { files: [
        { is_dir: false, relative_path: "SKILL.md", size: 10 }, { is_dir: true, relative_path: "private", size: 0 },
      ] } })) : new Response("not readable", { status: 403 }));
    await expect(readSkillPackage("/skills-local/example", post)).rejects.toMatchObject({ code: "HOST_LOCAL_SKILL_SIZE_UNAVAILABLE" });
    expect(post.mock.calls.every(([path]) => path === "/api/file/list")).toBe(true);
  });

  it("accepts the exact size boundary and reads binary contents", async () => {
    const post = vi.fn(async (path: string) => path.endsWith("list")
      ? new Response(JSON.stringify({ data: { files: [{ is_dir: false, relative_path: "file.bin", size: MAX_SKILL_DIRECTORY_BYTES }] } }))
      : new Response(Uint8Array.from([0, 1, 255])));
    const result = await readSkillPackage("/skills-local/example", post);
    expect(result.sha256).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(post.mock.calls[1]).toEqual(["/api/file/read", { file_path: "/skills-local/example/file.bin" }]);
  });

  it("validates all paths before downloading any file", async () => {
    const post = vi.fn(async () => new Response(JSON.stringify({ data: { files: [
      { is_dir: false, relative_path: "SKILL.md", size: 1 },
      { is_dir: false, relative_path: "../private", size: 1 },
    ] } })));
    await expect(readSkillPackage("/skills-local/example", post)).rejects.toThrow("不安全路径");
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("retains complete content beyond the old 50 MiB expanded limit", () => {
    const result = encodeSkillPackage([{ path: "large.bin", bytes: Buffer.alloc(51 * 1024 * 1024, 65) }]);
    expect(result.packageBytes.length).toBeGreaterThan(0);
  });
});
