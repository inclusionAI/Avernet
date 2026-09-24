import { describe, expect, it, vi } from "vitest";
import { SkillPackageStorage } from "../skill-package-storage.js";
import { getArtifactBucket } from "../oss-object-store.js";

function store() {
  const objects = new Map<string, Buffer>();
  return {
    putObject: vi.fn(async (key: string, bytes: Buffer | Uint8Array | string) => {
      objects.set(key, Buffer.from(bytes)); return { etag: null };
    }),
    getObject: vi.fn(async (key: string) => ({ content: objects.get(key)!, contentType: "application/zip", etag: null })),
    createSignedUrl: vi.fn(async (key: string, method: string) => `https://packages.example.test/${key}?method=${method}`),
  };
}

describe("Skill package storage routing", () => {
  it("writes and reads the same bytes in the configured bucket/prefix and signs with the container store", async () => {
    const current = store(), legacy = store(), download = store();
    const packages = new SkillPackageStorage({ bucket: "skill-packages", prefix: "packages/", store: current, urlStore: download }, legacy);
    const key = "evolve/stage-implementations/IMPL-1/v1/package.zip";
    const bytes = Buffer.from("immutable package");
    const ref = await packages.put(key, bytes);
    expect(ref).toBe(`oss://skill-packages/packages/${key}`);
    expect(current.putObject).toHaveBeenCalledWith(`packages/${key}`, bytes, "application/zip");
    expect((await packages.read(ref)).content).toEqual(bytes);
    expect(await packages.sign(ref, "GET", 3600)).toContain(`/packages/${key}?method=GET`);
    expect(download.createSignedUrl).toHaveBeenCalledWith(`packages/${key}`, "GET", 3600);
    expect(current.createSignedUrl).not.toHaveBeenCalled();
    expect(legacy.putObject).not.toHaveBeenCalled();
    expect(legacy.getObject).not.toHaveBeenCalled();
  });

  it("routes persisted old references through the legacy store and supports in-flight candidate uploads", async () => {
    const current = store(), legacy = store(), legacySigner = store();
    const packages = new SkillPackageStorage({ bucket: "skill-packages", prefix: "packages", store: current }, legacy, legacySigner);
    const oldKey = "evolve/skills/tasks/EV-OLD/candidate/package.zip";
    await legacy.putObject(oldKey, Buffer.from("old"));
    expect((await packages.read(`oss://${getArtifactBucket()}/${oldKey}`)).content.toString()).toBe("old");
    await packages.sign(`oss://${getArtifactBucket()}/${oldKey}`, "PUT", 60, { "Content-Type": "application/zip" });
    expect(legacySigner.createSignedUrl).toHaveBeenCalledWith(oldKey, "PUT", 60, { "Content-Type": "application/zip" });
    expect(current.createSignedUrl).not.toHaveBeenCalled();
    expect(packages.matches(`oss://${getArtifactBucket()}/${oldKey}`, oldKey)).toBe(true);
  });

  it("preserves the existing single-store behavior when the host supplies no dedicated storage", async () => {
    const legacy = store();
    const packages = new SkillPackageStorage(undefined, legacy);
    const ref = await packages.put("evolve/skills/SKILL-1/versions/v1/package.zip", Buffer.from("skill"));
    expect(ref).toBe(`oss://${getArtifactBucket()}/evolve/skills/SKILL-1/versions/v1/package.zip`);
    expect((await packages.read(ref)).content.toString()).toBe("skill");
  });

  it.each([
    "oss://unconfigured/packages/evolve/skills/a.zip",
    "oss://skill-packages/elsewhere/evolve/skills/a.zip",
    "oss://skill-packages/packages/evolve/skills/../secret",
    "oss://skill-packages/packages/evolve/skills/%2e%2e/secret",
    "oss://skill-packages/packages/evolve/skills/a.zip?key=x",
  ])("rejects unconfigured or unsafe references before signing: %s", async ref => {
    const current = store();
    const packages = new SkillPackageStorage({ bucket: "skill-packages", prefix: "packages", store: current });
    await expect(packages.sign(ref, "GET", 60)).rejects.toThrow();
    expect(current.createSignedUrl).not.toHaveBeenCalled();
  });

  it("does not redirect Pack or log archive writes into the Skill bucket", async () => {
    const current = store();
    const packages = new SkillPackageStorage({ bucket: "skill-packages", prefix: "packages", store: current });
    await expect(packages.put("evolution/EV-1/snapshots/artifact.zip", Buffer.from("pack"))).rejects.toThrow();
    expect(current.putObject).not.toHaveBeenCalled();
  });
});
