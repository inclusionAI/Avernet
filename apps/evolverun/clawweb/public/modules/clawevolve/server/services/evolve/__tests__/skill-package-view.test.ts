import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { editSkillPackage, skillPackageDiff, skillPackagesEquivalent, skillPackageView } from "../skill-package-view.js";

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
    zip.file("large.txt", Buffer.alloc(101 * 1024 * 1024));
    const packageBytes = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });

    await expect(skillPackageView(packageBytes)).rejects.toThrow("解压后过大");
  }, 30_000);

  it("previews registered packages above the former 32/50 MiB limits", async () => {
    const zip = new JSZip();
    zip.file("SKILL.md", "# Large Skill\n");
    zip.file("assets/large.bin", Buffer.alloc(51 * 1024 * 1024));
    const packageBytes = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
    const view = await skillPackageView(packageBytes);
    expect(view.files.find((file) => file.path === "assets/large.bin")?.size).toBe(51 * 1024 * 1024);
    expect(view.selected?.content).toBe("# Large Skill\n");
  }, 30_000);

  it("edits selected text files while preserving every other package file", async () => {
    const zip = new JSZip();
    zip.file("SKILL.md", "# Before\n");
    zip.file("references/guide.md", "Keep this guide\n");
    zip.file("assets/icon.bin", Buffer.from([0, 1, 2, 255]));

    const edited = await editSkillPackage(await zip.generateAsync({ type: "nodebuffer" }), [
      { path: "SKILL.md", content: "# After\n" },
    ]);
    const result = await JSZip.loadAsync(edited);

    await expect(result.file("SKILL.md")!.async("string")).resolves.toBe("# After\n");
    await expect(result.file("references/guide.md")!.async("string")).resolves.toBe("Keep this guide\n");
    await expect(result.file("assets/icon.bin")!.async("nodebuffer")).resolves.toEqual(Buffer.from([0, 1, 2, 255]));
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
