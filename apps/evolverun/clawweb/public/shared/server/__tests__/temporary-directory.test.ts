import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { createConfiguredTemporaryDirectory, TemporaryDirectoryError } from "../env.js";

afterEach(() => vi.unstubAllEnvs());

it("creates directories under CLAWWEB_TEMP_DIR", async () => {
  const parent = mkdtempSync(join(tmpdir(), "clawweb-configured-temp-"));
  vi.stubEnv("CLAWWEB_TEMP_DIR", parent);
  const directory = await createConfiguredTemporaryDirectory("scope");
  expect(directory.startsWith(resolve(parent))).toBe(true);
  rmSync(directory, { recursive: true, force: true });
  rmSync(parent, { recursive: true, force: true });
});

it("falls back to TMPDIR without CLAWWEB_TEMP_DIR", async () => {
  const parent = mkdtempSync(join(tmpdir(), "clawweb-tmpdir-temp-"));
  vi.stubEnv("TMPDIR", parent);
  const directory = await createConfiguredTemporaryDirectory("scope");
  expect(directory.startsWith(resolve(parent))).toBe(true);
  rmSync(directory, { recursive: true, force: true });
  rmSync(parent, { recursive: true, force: true });
});

it("rejects a missing configured parent without disclosing its path", async () => {
  const parent = mkdtempSync(join(tmpdir(), "clawweb-missing-temp-"));
  const missing = join(parent, "missing");
  vi.stubEnv("CLAWWEB_TEMP_DIR", missing);
  try {
    const failure = await createConfiguredTemporaryDirectory("scope").then(
      () => { throw new Error("expected the configured parent to be missing"); },
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(TemporaryDirectoryError);
    if (!(failure instanceof TemporaryDirectoryError)) throw new Error("expected a TemporaryDirectoryError");
    expect(failure.message).toBe("临时目录不可用或配置无效");
    expect(failure.message).not.toContain(missing);
    expect(failure.message).not.toContain(resolve(missing));
    expect(failure.message).not.toContain("CLAWWEB_TEMP_DIR");
    expect(failure.message).not.toContain("parent=");
  } finally {
    vi.unstubAllEnvs();
    rmSync(parent, { recursive: true, force: true });
  }
  await expect(createConfiguredTemporaryDirectory("invalid/scope")).rejects.toThrow("Invalid temporary directory scope");
});
