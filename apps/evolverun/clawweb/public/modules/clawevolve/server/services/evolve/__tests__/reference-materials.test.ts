import { describe, expect, it, vi } from "vitest";
import JSZip from "jszip";
import { buildMaterials, digest, materialPath, ReferenceMaterials } from "../reference-materials.js";

async function archive(files: Record<string, string>, compression: "STORE" | "DEFLATE" = "STORE") {
  const zip = new JSZip();
  for (const [path, bytes] of Object.entries(files)) zip.file(path, bytes, { createFolders: false });
  return zip.generateAsync({ type: "nodebuffer", compression });
}
function storage() {
  const objects = new Map<string, Buffer>();
  const store = {
    putObject: vi.fn(async (key: string, bytes: Buffer | Uint8Array | string) => {
      objects.set(key, Buffer.from(bytes)); return { etag: null };
    }),
    getObject: vi.fn(async (key: string) => {
      if (!objects.has(key)) throw Object.assign(new Error("NoSuchKey"), { code: "NoSuchKey" });
      return { content: objects.get(key)!, etag: null, contentType: null };
    }),
    createSignedUrl: vi.fn(async (key: string) => `https://artifacts.example.test/${key}`),
  };
  return { objects, store, materials: new ReferenceMaterials(store, store) };
}

describe("task reference materials", () => {
  it("preserves arbitrary bytes, nested names and duplicate basenames, including a user index.json", async () => {
    const raw = await archive({ "规范/README.md": "仅工作日汇报", "示例/README.md": "示例", "index.json": "user file" });
    const { bytes, manifest } = await buildMaterials(raw);
    const zip = await JSZip.loadAsync(bytes);
    expect(manifest.files.map(item => item.path)).toEqual(["index.json", "示例/README.md", "规范/README.md"].sort());
    expect(await zip.file("files/index.json")!.async("string")).toBe("user file");
    expect(manifest.files.find(item => item.path === "规范/README.md")!.sha256).toBe(digest("仅工作日汇报"));
  });
  it.each(["../rule.md", "/absolute", "a/../b", "a\\b", "C:/file", "a//b", "a/./b", "a\u0000b"])("rejects unsafe path %j", path => {
    expect(() => materialPath(path)).toThrow();
  });
  it("rejects path components exceeding the target Linux filesystem byte limit", async () => {
    await expect(buildMaterials(await archive({ ["规".repeat(100) + ".md"]: "x" }))).rejects.toThrow(/路径/);
    await expect(buildMaterials(await archive({ ["规".repeat(84) + ".md"]: "x" }))).resolves.toBeDefined();
  });
  it("checks paths before JSZip can normalize traversal or collapse duplicates", async () => {
    await expect(buildMaterials(await archive({ "../rules.txt": "escape" }))).rejects.toThrow();
    const raw = await archive({ "a.txt": "one", "b.txt": "two" });
    // Both local and central names are changed, preserving valid offsets.
    const duplicate = Buffer.from(raw.toString("latin1").replaceAll("b.txt", "a.txt"), "latin1");
    await expect(buildMaterials(duplicate)).rejects.toThrow(/重复/);
  });
  it("rejects a file/directory conflict, symlink, compressed input and excessive count", async () => {
    await expect(buildMaterials(await archive({ a: "file", "a/b": "nested" }))).rejects.toThrow(/冲突/);
    const zip = new JSZip().file("link", "outside", { unixPermissions: 0o120777 });
    await expect(buildMaterials(await zip.generateAsync({ type: "nodebuffer", platform: "UNIX" }))).rejects.toThrow(/非法/);
    await expect(buildMaterials(await archive({ "huge.txt": "a".repeat(10000) }, "DEFLATE"))).rejects.toThrow();
    await expect(buildMaterials(await archive(Object.fromEntries(Array.from({ length: 501 }, (_, i) => [`f${i}`, ""]))))).rejects.toThrow(/数量/);
  });
  it("classifies corrupt CRC and invalid UTF-8 names as invalid user input", async () => {
    const raw = await archive({ "a.txt": "abc" });
    const corrupt = Buffer.from(raw); corrupt[35] ^= 1;
    await expect(buildMaterials(corrupt)).rejects.toMatchObject({ status: 422 });
    const invalid = Buffer.from(raw); invalid[30] = 0xff;
    const directory = invalid.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]));
    invalid[directory + 46] = 0xff;
    await expect(buildMaterials(invalid)).rejects.toMatchObject({ status: 422 });
  });
  it("freezes a compact Task-specific reference and signs the exact stored package", async () => {
    const { materials, store, objects } = storage();
    const upload = await materials.upload("owner", await archive({ "规则/日报.md": "按部门汇报" }));
    const frozen = await materials.bind("owner", upload.materialId, "EV-1");
    expect(JSON.stringify(frozen).length).toBeLessThan(1000);
    const input = await materials.stepInput(frozen, "EV-1");
    expect(input.download.url).toBe(`https://artifacts.example.test/${frozen.objectKey}`);
    expect(input).not.toHaveProperty("objectKey");
    expect(objects.has(frozen.objectKey)).toBe(true);
    store.getObject.mockClear();
    expect((await materials.view(frozen, "EV-1")).files[0].path).toBe("规则/日报.md");
    expect(store.getObject).toHaveBeenCalledTimes(1);
    expect(store.getObject).toHaveBeenCalledWith(frozen.objectKey.replace(/package\.zip$/, "index.json"));
    expect(store.putObject).toHaveBeenCalled();
    await expect(materials.stepInput(frozen, "EV-OTHER")).rejects.toThrow(/引用/);
    await expect(materials.bind("other", upload.materialId, "EV-2")).rejects.toThrow(/失效/);
  });
  it("rejects expired uploads and incomplete/corrupt content, and propagates storage failures", async () => {
    const { materials, objects, store } = storage();
    const upload = await materials.upload("owner", await archive({ "a.txt": "abc" }));
    const key = [...objects.keys()].find(key => key.endsWith("upload.json"))!;
    const metadata = JSON.parse(objects.get(key)!.toString());
    objects.set(metadata.objectKey, Buffer.from("corrupt"));
    await expect(materials.bind("owner", upload.materialId, "EV-1")).rejects.toThrow(/校验/);
    metadata.expiresAt = Date.now() - 1; objects.set(key, Buffer.from(JSON.stringify(metadata)));
    await expect(materials.bind("owner", upload.materialId, "EV-1")).rejects.toThrow(/失效/);
    store.putObject.mockRejectedValueOnce(new Error("write failed"));
    await expect(materials.upload("owner", await archive({ a: "a" }))).rejects.toThrow("write failed");
  });

});
