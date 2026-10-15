import type { HttpInput } from "../contracts.js";
import { BotRuntimeError } from "../errors.js";

/** Same multipart file contract as the existing Engine filesystem and local-package adapters. */
export function runtimeHttpBody(input: HttpInput, headers: Record<string, string>): BodyInit | undefined {
  if (!input.upload) return input.body === undefined ? undefined : JSON.stringify(input.body);
  if (input.body !== undefined || typeof input.upload.content !== "string" || Buffer.byteLength(input.upload.content) > 2 * 1024 * 1024) throw new BotRuntimeError(422, "invalid_runtime_upload", "Invalid or oversized upload");
  const form = new FormData();
  for (const key of Object.keys(headers)) if (key.toLowerCase() === "content-type") delete headers[key];
  const data = input.upload.encoding === "base64" ? Buffer.from(input.upload.content, "base64") : input.upload.content;
  form.set("file", new Blob([data]), input.upload.filename ?? "recovery.txt");
  if (input.upload.targetPath) form.set("target_path", input.upload.targetPath);
  for (const [key, value] of Object.entries(input.upload.fields ?? {})) {
    if (["file", "target_path"].includes(key) || typeof value !== "string") throw new BotRuntimeError(422, "invalid_runtime_upload", "Reserved multipart field");
    form.set(key, value);
  }
  return form;
}
