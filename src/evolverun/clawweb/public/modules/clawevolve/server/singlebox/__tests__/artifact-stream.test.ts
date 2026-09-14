import { afterEach, expect, it } from "vitest";
import { mkdtemp, readdir, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });
it("streams more than 10 MiB and preserves the complete file", async () => {
  const root = await mkdtemp(join(tmpdir(), "artifact-stream-")); roots.push(root);
  const store = new FilesystemObjectStore(root, "http://127.0.0.1:5173", 16 * 1024 * 1024);
  const part = Buffer.alloc(1024 * 1024, 37);
  await store.putStream("pack.zip", Readable.from(Array.from({ length: 11 }, () => part)));
  const result = await store.openStream("pack.zip");
  expect(result.size).toBe(11 * part.length);
  let bytes = 0; for await (const chunk of result.stream) { expect((chunk as Buffer).every((value) => value === 37)).toBe(true); bytes += chunk.length; }
  expect(bytes).toBe(result.size);
});
it("rejects oversized or interrupted uploads without replacing the original", async () => {
  const root = await mkdtemp(join(tmpdir(), "artifact-stream-")); roots.push(root);
  const store = new FilesystemObjectStore(root, "", 8);
  await store.putStream("pack.zip", Readable.from([Buffer.from("original")]));
  await expect(store.putStream("pack.zip", Readable.from([Buffer.alloc(9)]))).rejects.toThrow("limit");
  async function* broken() { yield Buffer.from("x"); throw new Error("interrupted"); }
  await expect(store.putStream("pack.zip", Readable.from(broken()))).rejects.toThrow("interrupted");
  expect((await store.getObject("pack.zip")).content.toString()).toBe("original");
  expect(await readdir(root)).toEqual(["pack.zip"]);
});
it("rejects traversal, redirected directories and wrong-method tickets", async () => {
  const root = await mkdtemp(join(tmpdir(), "artifact-stream-")); roots.push(root);
  const outside = await mkdtemp(join(tmpdir(), "artifact-outside-")); roots.push(outside);
  const store = new FilesystemObjectStore(root);
  await expect(store.putStream("../x", Readable.from([]))).rejects.toThrow();
  await symlink(outside, join(root, "redirect"));
  await expect(store.putStream("redirect/x", Readable.from([]))).rejects.toThrow("escaped");
  const url = await store.createSignedUrl("pack.zip", "GET", 60);
  expect(() => store.resolveSignedRequest(url.split('/').at(-1)!, "PUT")).toThrow();
});
