// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { execFile, type ExecFileOptions, type ExecFileException } from "node:child_process";
import { executeGit } from "@avernet/clawweb-shared/server/services/git-execution";
import {
  existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import * as fsPromises from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  cloneClawflows, commitAndPushSaved, commitAndPushDeploy, createTempRepo, removeTempRepo,
  type ClawFlowsGitConfig,
} from "../clawflows-git-client.js";
import {
  createLocalRemote, credentialFiles, credentialInput, gitEnvironment, runGit,
} from "./clawflows-git-client-fixture.js";

vi.mock("node:child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:child_process")>();
  return { ...actual, execFile: vi.fn(actual.execFile) };
});

vi.mock("node:fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs/promises")>();
  return { ...actual, writeFile: vi.fn(actual.writeFile) };
});

const realWriteFile = (await vi.importActual<typeof import("node:fs/promises")>("node:fs/promises")).writeFile;
const realExecFile = (await vi.importActual<typeof import("node:child_process")>("node:child_process")).execFile;
const execMock = vi.mocked(execFile);
type GitCallback = (error: ExecFileException | null, stdout: Buffer, stderr: Buffer) => void;
// Deliberately synthetic credentials, including URL/shell metacharacters.
const username = "fixture user+name@example.invalid";
const token = "fixture-only:p@ss /?#%+'\"$;雪";
const author = { name: "Fixture Author", email: "fixture@example.invalid" };
const config: ClawFlowsGitConfig = {
  remoteUrl: "https://git.example.invalid:8443/team/repo.git",
  username, token, email: author.email,
};
let root: string;
let tempParent: string;
let repo: string;
let bare: string;
let globalConfig: string;
let globalContents: string;

beforeEach(async () => {
  execMock.mockReset().mockImplementation(realExecFile);
  vi.mocked(fsPromises.writeFile).mockReset().mockImplementation(realWriteFile);
  root = mkdtempSync(path.join(tmpdir(), "cw-git-client-test-"));
  // Both shell quote forms, substitution and whitespace must stay literal.
  tempParent = path.join(root, "space ' quote \" $HOME $(false) `false` ;");
  mkdirSync(tempParent);
  const home = path.join(root, "home");
  mkdirSync(home);
  globalConfig = path.join(home, ".gitconfig");
  globalContents = "[user]\n\tname = unwanted-global-user\n[credential]\n\thelper = must-not-run-global-helper\n";
  writeFileSync(globalConfig, globalContents);
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("GIT_")) vi.stubEnv(key, undefined);
  }
  vi.stubEnv("HOME", home);
  vi.stubEnv("CLAWWEB_TEMP_DIR", tempParent);
  vi.stubEnv("GIT_TERMINAL_PROMPT", "0");
  bare = createLocalRemote(root);
  repo = await createTempRepo();
});

afterEach(() => {
  expect(readFileSync(globalConfig, "utf8")).toBe(globalContents);
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  rmSync(root, { recursive: true, force: true });
});

function expectNoSecrets(value: unknown, secrets = [token, encodeURIComponent(token)]): void {
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  for (const secret of secrets) expect(serialized).not.toContain(secret);
}

function expectCredential(cwd: string, remoteUrl: string, prefix: string[] = [], password = token): void {
  const filled = runGit(cwd, [...prefix, "credential", "fill"], credentialInput(remoteUrl));
  expect(filled).toContain(`username=${username}\n`);
  expect(filled).toContain(`password=${password}`);
}

/**
 * No sockets or external hosts: only intercept transport selection. Before the
 * first real local clone, run REAL Git's credential protocol with the exact
 * clone-time -c options. All clone/config/commit/push operations still use Git.
 */
function bridgeTransport(options: {
  remoteUrl?: string;
  password?: string;
  beforeClone?: (args: string[], opts: ExecFileOptions) => void;
  fail?: (args: string[]) => boolean;
  failure?: Error;
} = {}): { authenticatedClones: number; authenticatedPushes: number; authenticatedFetches: number } {
  const counts = { authenticatedClones: 0, authenticatedPushes: 0, authenticatedFetches: 0 };
  execMock.mockImplementation(((file: string, args: string[], opts: ExecFileOptions, callback: GitCallback) => {
    expect(file).toBe("git");
    expectNoSecrets(args);
    expect(opts.env?.GIT_CONFIG_NOSYSTEM).toBe("1");
    expect(opts.env?.GIT_CONFIG_GLOBAL).toBe("/dev/null");
    expect(Object.keys(opts.env ?? {}).filter((key) => key.startsWith("GIT_CONFIG_"))).toEqual([
      "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
    ]);
    expect(args).not.toContain("--global");
    if (options.fail?.(args)) throw options.failure ?? new Error("synthetic failure");
    const cloneIndex = args.indexOf("clone");
    if (cloneIndex !== -1) {
      const prefix = args.slice(0, cloneIndex);
      expect(prefix).toContain("credential.helper=");
      expectCredential(repo, options.remoteUrl ?? config.remoteUrl, prefix, options.password);
      counts.authenticatedClones++;
      options.beforeClone?.(args, opts);
      return realExecFile(file, [...prefix, "clone", "--quiet", bare, "."], { ...opts, encoding: "buffer" }, callback);
    }
    if (args[0] === "push" || args[0] === "fetch") {
      expectCredential(repo, options.remoteUrl ?? config.remoteUrl, [], options.password);
      if (args[0] === "push") counts.authenticatedPushes++;
      else counts.authenticatedFetches++;
      return realExecFile(file, args.map((arg) => arg === "origin" ? bare : arg), { ...opts, encoding: "buffer" }, callback);
    }
    return realExecFile(file, args, { ...opts, encoding: "buffer" }, callback);
  }) as typeof execFile);
  return counts;
}

function expectAuthRemoved(): void {
  expect(credentialFiles(root)).toEqual([]);
  expect(readdirSync(tempParent)).toEqual([path.basename(repo)]);
}

describe("ClawFlowsGitClient isolated credentials", () => {
  it.each(["explicit token", "embedded password"])("rejects HTTP with %s before creating credentials or invoking Git", async (source) => {
    const remoteUrl = new URL(config.remoteUrl);
    remoteUrl.protocol = "http:";
    if (source === "embedded password") {
      remoteUrl.username = encodeURIComponent(username);
      remoteUrl.password = encodeURIComponent(token);
    }
    // The old implementation must fail locally rather than contact a remote.
    execMock.mockImplementation(() => { throw new Error("Unexpected Git invocation"); });
    let caught: unknown;
    try {
      await cloneClawflows(repo, {
        ...config, remoteUrl: remoteUrl.href, token: source === "explicit token" ? token : undefined,
      });
    } catch (error) { caught = error; }
    expect.soft(caught).toBeInstanceOf(Error);
    expect.soft(String(caught)).toContain("Git credentials require HTTPS");
    expectNoSecrets(String(caught));
    expect.soft(execMock).not.toHaveBeenCalled();
    expect.soft(fsPromises.writeFile).not.toHaveBeenCalled();
    expectAuthRemoved();
  });

  it("authenticates the FIRST HTTPS clone outside the empty destination, then fetches and pushes", async () => {
    const remoteUrl = config.remoteUrl;
    let bootstrapFile = "";
    const counts = bridgeTransport({ remoteUrl, beforeClone(args, opts) {
      expect(args).toContain(remoteUrl);
      expect(opts.shell).toBe(false);
      expect(readdirSync(repo)).toEqual([]);
      const files = credentialFiles(root);
      expect(files).toHaveLength(1);
      bootstrapFile = files[0];
      expect(path.relative(repo, bootstrapFile).startsWith(`..${path.sep}`)).toBe(true);
      expect(statSync(bootstrapFile).mode & 0o777).toBe(0o600);
      expect(statSync(path.dirname(bootstrapFile)).mode & 0o777).toBe(0o700);
    } });
    await cloneClawflows(repo, { ...config, remoteUrl });
    expect(counts.authenticatedClones).toBe(1);
    expect(existsSync(bootstrapFile)).toBe(false);
    expect(existsSync(path.dirname(bootstrapFile))).toBe(false);
    expect(readdirSync(tempParent)).toEqual([path.basename(repo)]);
    const files = credentialFiles(repo);
    expect(files).toHaveLength(1);
    expect(path.relative(path.join(repo, ".git"), files[0]).startsWith("..")).toBe(false);
    expect(statSync(files[0]).mode & 0o777).toBe(0o600);
    expect(runGit(repo, ["remote", "get-url", "origin"])).toBe(remoteUrl);
    expectNoSecrets(readFileSync(path.join(repo, ".git", "config"), "utf8"));
    await executeGit(["fetch", "origin"], {
      cwd: repo, env: gitEnvironment(),
    });
    writeFileSync(path.join(repo, "saved.txt"), "saved\n");
    const saved = await commitAndPushSaved(repo, "git-fixture", "fixture", author);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", `refs/heads/${saved.branch}`])).toBe(saved.commit);
    writeFileSync(path.join(repo, "deployed.txt"), "deployed\n");
    const deployed = await commitAndPushDeploy(repo, "git-fixture", 1, 1, "fixture", author);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", `refs/tags/${deployed.tagName}^{commit}`])).toBe(deployed.commit);
    expect(counts).toEqual({ authenticatedClones: 1, authenticatedFetches: 1, authenticatedPushes: 2 });
    await removeTempRepo(repo);
    expect(credentialFiles(root)).toEqual([]);
  });

  it("does not return credentials for another host, port or protocol", async () => {
    bridgeTransport();
    await cloneClawflows(repo, config);
    expectCredential(repo, config.remoteUrl);
    for (const remote of [
      "https://other.example.invalid:8443/team/repo.git",
      "https://git.example.invalid:9443/team/repo.git",
      "http://git.example.invalid:8443/team/repo.git",
    ]) {
      expect(() => runGit(repo, ["credential", "fill"], credentialInput(remote))).toThrow("Fixture git credential failed");
    }
  });

  it("does not let an inherited helper run during clone, fetch, or push", async () => {
    vi.stubEnv("GIT_CONFIG_COUNT", "1");
    vi.stubEnv("GIT_CONFIG_KEY_0", "credential.helper");
    vi.stubEnv("GIT_CONFIG_VALUE_0", "must-not-run-inherited-helper");
    const counts = bridgeTransport({ beforeClone(args) {
      const prefix = args.slice(0, args.indexOf("clone"));
      const helpers = runGit(repo, [...prefix, "config", "--get-all", "credential.helper"]);
      expect(helpers).not.toContain("must-not-run-inherited-helper");
      expect(helpers).toContain("store --file ");
    } });
    await cloneClawflows(repo, config);
    await executeGit(["fetch", "origin"], {
      cwd: repo, env: gitEnvironment(),
    });
    writeFileSync(path.join(repo, "inherited-helper.txt"), "safe\n");
    await commitAndPushSaved(repo, "inherited-helper", "fixture", author);
    expect(counts.authenticatedFetches).toBe(1);
    expect(counts.authenticatedPushes).toBe(1);
  });

  it("strips embedded URL userinfo from argv and origin, and uses explicit credentials", async () => {
    const embedded = "embedded-fixture-secret";
    bridgeTransport({ beforeClone(args) {
      expect(args).toContain(config.remoteUrl);
      expectNoSecrets(args, [embedded, "old-user:"]);
    } });
    await cloneClawflows(repo, {
      ...config, remoteUrl: config.remoteUrl.replace("://", `://old-user:${embedded}@`),
    });
    expect(runGit(repo, ["remote", "get-url", "origin"])).toBe(config.remoteUrl);
  });

  it.each(["clone", "user.name", "credential.helper"])("cleans credentials and suppresses secret-bearing subprocess output on %s failure", async (stage) => {
    const failure = Object.assign(new Error(`secret in message ${token}`), {
      stderr: Buffer.from(`remote failure ${token} ${encodeURIComponent(token)}`),
      stdout: `stdout ${token}`, status: 128,
    });
    bridgeTransport({
      fail: (args) => stage === "clone" ? args.includes("clone") : args[0] === "config" && args.includes(stage),
      failure,
    });
    let caught: unknown;
    try { await cloneClawflows(repo, config); } catch (error) { caught = error; }
    expect(caught).toBeInstanceOf(Error);
    expectNoSecrets(String(caught));
    expect(String(caught)).toContain("128");
    expectAuthRemoved();
    for (const [, , opts] of execMock.mock.calls) {
      expect((opts as ExecFileOptions).shell).toBe(false);
    }
  });

  it("cleans a partially written persistent credential file when the write fails", async () => {
    bridgeTransport();
    vi.mocked(fsPromises.writeFile).mockImplementation(async (...args) => {
      await realWriteFile(...args);
      if (String(args[0]).includes(`${path.sep}.git${path.sep}`) && path.basename(String(args[0])) === "credentials") {
        throw new Error("synthetic disk failure");
      }
    });
    await expect(cloneClawflows(repo, config)).rejects.toThrow("synthetic disk failure");
    expectAuthRemoved();
  });

  it("does not pass a secret-bearing push error to the caller, and caller cleanup removes persisted credentials", async () => {
    bridgeTransport();
    await cloneClawflows(repo, config);
    bridgeTransport({ fail: (args) => args[0] === "push", failure: Object.assign(new Error(token), {
      stderr: `remote: ${token}`, status: 1,
    }) });
    writeFileSync(path.join(repo, "saved.txt"), "saved\n");
    let caught: unknown;
    try { await commitAndPushSaved(repo, "git-fixture", "fixture", author); } catch (error) { caught = error; }
    expect(caught).toBeInstanceOf(Error);
    expectNoSecrets(String(caught));
    await removeTempRepo(repo);
    expect(credentialFiles(root)).toEqual([]);
  });

  it.each([false, true])("keeps local and file transports usable without creating credentials (token=%s)", async (withToken) => {
    await cloneClawflows(repo, { ...config, remoteUrl: bare, token: withToken ? token : undefined });
    expect(credentialFiles(root)).toEqual([]);
    writeFileSync(path.join(repo, "saved.txt"), "local\n");
    const result = await commitAndPushSaved(repo, "local-fixture", "local", author);
    expect(runGit(root, ["--git-dir", bare, "rev-parse", `refs/heads/${result.branch}`])).toBe(result.commit);
    await removeTempRepo(repo);
    repo = await createTempRepo();
    await cloneClawflows(repo, { ...config, remoteUrl: pathToFileURL(bare).href, token: withToken ? token : undefined });
    expect(credentialFiles(root)).toEqual([]);
  });

  it.each(["https://git.example.invalid/team/repo.git", "http://git.example.invalid/team/repo.git", "git@git.example.invalid:team/repo.git", "ssh://git@git.example.invalid/team/repo.git"])("preserves no-token/SSH transport %s", async (remoteUrl) => {
    execMock.mockImplementation(((file: string, args: string[], opts: ExecFileOptions, callback: GitCallback) => {
      expectNoSecrets(args);
      if (args[0] === "clone") {
        expect(args).toContain(remoteUrl);
        expect(credentialFiles(root)).toEqual([]);
        return realExecFile(file, ["clone", "--quiet", bare, "."], { ...opts, encoding: "buffer" }, callback);
      }
      return realExecFile(file, args, { ...opts, encoding: "buffer" }, callback);
    }) as typeof execFile);
    await cloneClawflows(repo, { ...config, remoteUrl, token: remoteUrl.startsWith("http") ? undefined : token });
    expect(runGit(repo, ["remote", "get-url", "origin"])).toBe(remoteUrl);
    expect(credentialFiles(root)).toEqual([]);
  });
});
