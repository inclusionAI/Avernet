import { spawnSync, type SpawnSyncOptionsWithStringEncoding } from "node:child_process";
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

export function gitEnvironment(): NodeJS.ProcessEnv {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_CONFIG_")),
  ) as NodeJS.ProcessEnv;
  return {
    ...env,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: "/dev/null",
    GIT_TERMINAL_PROMPT: "0",
  };
}

export function runGit(cwd: string, args: string[], input?: string): string {
  const options: SpawnSyncOptionsWithStringEncoding = {
    cwd, input, env: gitEnvironment(), encoding: "utf8", timeout: 10_000,
    stdio: ["pipe", "pipe", "pipe"],
  };
  const result = spawnSync("git", args, options);
  if (result.error || result.status !== 0) {
    // Do not print helper output: it may contain the fixture password.
    throw new Error(`Fixture git ${args[0]} failed (status ${result.status})`);
  }
  return result.stdout.trim();
}

export function createLocalRemote(root: string): string {
  const bare = path.join(root, "remote.git");
  const seed = path.join(root, "seed");
  mkdirSync(seed);
  runGit(root, ["init", "--bare", "--initial-branch=main", bare]);
  runGit(seed, ["init", "--initial-branch=main"]);
  runGit(seed, ["config", "user.name", "Fixture Author"]);
  runGit(seed, ["config", "user.email", "fixture@example.invalid"]);
  writeFileSync(path.join(seed, "README"), "local fixture\n");
  runGit(seed, ["add", "."]);
  runGit(seed, ["commit", "-m", "fixture"]);
  runGit(seed, ["push", bare, "main"]);
  return bare;
}

export function credentialInput(remoteUrl: string): string {
  const url = new URL(remoteUrl);
  return `protocol=${url.protocol.slice(0, -1)}\nhost=${url.host}\n\n`;
}

export function credentialFiles(root: string): string[] {
  return readdirSync(root, { recursive: true })
    .map(String)
    .filter((entry) => path.basename(entry) === "credentials")
    .map((entry) => path.join(root, entry));
}
