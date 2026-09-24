import { createHash } from "node:crypto";
import type { ObjectStore } from "../object-storage/oss-object-store.js";

export type RunnerLaunch = {
  schemaVersion: "clawevolve.runner-launch.v1";
  taskId: string;
  stepId: string;
  stage: string;
  invocationId: string;
  runtimeMaintenance: boolean;
  args: string;
};

export type LaunchStorage = {
  artifactStore?: ObjectStore;
  artifactUrlStore?: Pick<ObjectStore, "createSignedUrl">;
};
/** Module-owned storage; never shared through process-global mutable state. */
export async function freezeRunnerLaunch(launch: RunnerLaunch, storage: LaunchStorage): Promise<{ url: string; sha256: string }> {
  const { artifactStore, artifactUrlStore = artifactStore } = storage;
  if (!artifactStore?.putObject || !artifactUrlStore) {
    throw new Error("Runner 启动参数存储未配置");
  }
  const content = Buffer.from(JSON.stringify(launch), "utf8");
  const sha256 = createHash("sha256").update(content).digest("hex");
  // Content addressing prevents one HITL invocation from overwriting another.
  const key = `runner-launches/${sha256}.json`;
  await artifactStore.putObject(key, content, "application/json");
  const url = await artifactUrlStore.createSignedUrl(key, "GET", 3600);
  const parsed = new URL(url);
  if (!((parsed.protocol === "https:") || (parsed.protocol === "http:"
    && ["localhost", "127.0.0.1"].includes(parsed.hostname)))
    || parsed.username || parsed.password || parsed.hash || /[\s'\u0000-\u001f]/.test(url)) {
    throw new Error("Runner 启动参数下载地址不合法");
  }
  return { url, sha256 };
}
