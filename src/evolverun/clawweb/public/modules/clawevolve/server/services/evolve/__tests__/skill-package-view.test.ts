import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { skillPackageDiff, skillPackagesEquivalent, skillPackageView } from "../skill-package-view.js";

async function archive(files: Record<string, string>): Promise<Buffer> {
  const zip = new JSZip();
  for (const [path, content] of Object.entries(files)) zip.file(path, content);
  return zip.generateAsync({ type: "nodebuffer" });
}

describe("Skill package view", () => {
  it("lists the whole package and previews SKILL.md by default", async () => {
    const result = await skillPackageView(await archive({
      "SKILL.md": "# v1\n",
      "scripts/check.py": "print('ok')\n",
    }));
    expect(result.files.map((item) => item.path)).toEqual(["scripts/check.py", "SKILL.md"]);
    expect(result.selected).toEqual({ path: "SKILL.md", content: "# v1\n" });
  });

  it("compares an accepted version with its task-frozen baseline", async () => {
    const result = await skillPackageDiff(
      await archive({ "SKILL.md": "# v1\n", "old.txt": "old\n" }),
      await archive({ "SKILL.md": "# v2\n", "new.txt": "new\n" }),
    );
    expect(result.files).toEqual([
      { path: "SKILL.md", change: "modified", before: "# v1\n", after: "# v2\n" },
      { path: "new.txt", change: "added", before: null, after: "new\n" },
      { path: "old.txt", change: "deleted", before: "old\n", after: null },
    ]);
  });

  it("rejects a highly compressed Skill package before previewing it", async () => {
    const zip = new JSZip();
    zip.file("large.txt", Buffer.alloc(33 * 1024 * 1024));
    const packageBytes = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });

    await expect(skillPackageView(packageBytes)).rejects.toThrow("解压后过大");
  });
});

describe("Skill package equivalence", () => {
  it("compares the complete file set instead of ZIP container bytes", async () => {
    const first = new JSZip();
    first.file("SKILL.md", "# Skill\n");
    first.file("assets/icon.bin", Buffer.from([0, 1, 2]));
    const second = new JSZip();
    second.file("assets/icon.bin", Buffer.from([0, 1, 2]));
    second.file("SKILL.md", "# Skill\n");
    const changed = new JSZip();
    changed.file("SKILL.md", "# Skill changed\n");
    changed.file("assets/icon.bin", Buffer.from([0, 1, 2]));

    await expect(skillPackagesEquivalent(
      await first.generateAsync({ type: "nodebuffer" }),
      await second.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }),
    )).resolves.toBe(true);
    await expect(skillPackagesEquivalent(
      await first.generateAsync({ type: "nodebuffer" }),
      await changed.generateAsync({ type: "nodebuffer" }),
    )).resolves.toBe(false);
  });
});
