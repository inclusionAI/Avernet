import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { getAgentSdkRunner } from "../src/platform/agent-sdk-runner.js";

/** Run one query() against a stand-in CLI and return the --model it was spawned with. */
async function spawnedModel(model?: string): Promise<string | undefined> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "taskguard-sdk-runner-"));
  try {
    const argvFile = path.join(dir, "argv.json");
    const cli = path.join(dir, "claude.mjs");
    fs.writeFileSync(
      cli,
      `import fs from "node:fs";\n` +
        `fs.writeFileSync(${JSON.stringify(argvFile)}, JSON.stringify(process.argv.slice(2)));\n` +
        `process.exit(1);\n`,
    );
    const run = getAgentSdkRunner({ model, claudeCodeExecutablePath: cli });
    await run({ prompt: "hi" });
    const argv: string[] = JSON.parse(fs.readFileSync(argvFile, "utf8"));
    return argv[argv.indexOf("--model") + 1];
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

describe("getAgentSdkRunner model", () => {
  it("defaults to a model that has not been retired", async () => {
    assert.equal(await spawnedModel(), "claude-sonnet-5-5");
  });

  it("passes a configured model through", async () => {
    assert.equal(await spawnedModel("claude-opus-4-8"), "claude-opus-4-8");
  });
});
