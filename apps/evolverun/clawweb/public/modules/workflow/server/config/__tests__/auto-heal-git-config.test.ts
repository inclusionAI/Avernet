import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stringify as stringifyYaml } from "yaml";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { resolveAutoHealGitConfigFromDb, resolveAutoHealGitSettings } from "../auto-heal-git-config.js";
import { createAutoHealConfigTestDb, fallbackAuthor, fallbackGitConfig } from "./auto-heal-git-fixtures.js";

describe("resolveAutoHealGitSettings", () => {
  let db: IDatabase;
  let repo: AppConfigRepository;

  beforeEach(() => {
    db = createAutoHealConfigTestDb();
    repo = new AppConfigRepository(db);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await db.close();
  });

  it("prefers every nonempty DB field, trims identity/URL fields, and preserves the secret", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: "  https://git.example.com/database.git \n",
      username: " db-user ",
      email: " db-git@example.com ",
      token: "  database-token \n",
      authorName: " DB Author ",
      authorEmail: " db-author@example.com ",
    }) });

    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: {
        remoteUrl: "https://git.example.com/database.git",
        username: "db-user",
        email: "db-git@example.com",
        token: "  database-token \n",
      },
      gitAuthor: { name: "DB Author", email: "db-author@example.com" },
    });
  });

  it("merges independently without mapping Git username/email onto commit author fields", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      username: "db-user", email: "db-git@example.com",
    }) });
    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: { ...fallbackGitConfig, username: "db-user", email: "db-git@example.com" },
      gitAuthor: fallbackAuthor,
    });

    await repo.update("git", { config_yaml: stringifyYaml({
      authorName: "DB Author", authorEmail: "db-author@example.com",
    }) });
    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: fallbackGitConfig,
      gitAuthor: { name: "DB Author", email: "db-author@example.com" },
    });
  });

  it("uses trimmed fallbacks for blank DB fields without trimming the fallback secret", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: " \n", username: "\t", email: "", token: "", authorName: " ", authorEmail: " ",
    }) });
    const gitConfig = Object.freeze({
      remoteUrl: ` ${fallbackGitConfig.remoteUrl} `,
      username: ` ${fallbackGitConfig.username} `,
      email: ` ${fallbackGitConfig.email} `,
      token: "  fallback-token \n",
    });
    const author = Object.freeze({ name: ` ${fallbackAuthor.name} `, email: ` ${fallbackAuthor.email} ` });
    expect(await resolveAutoHealGitSettings(db, gitConfig, author)).toEqual({
      gitConfig: { ...fallbackGitConfig, token: "  fallback-token \n" },
      gitAuthor: fallbackAuthor,
    });
  });

  it("does not trim a whitespace-only DB secret", async () => {
    await repo.create({ config_key: "git", config_yaml: 'token: "  "' });
    const settings = await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor);
    expect(settings.gitConfig.token).toBe("  ");
  });

  it("uses fallbacks when no git row exists and preserves an omitted fallback token", async () => {
    const { token: _token, ...withoutToken } = fallbackGitConfig;
    expect(await resolveAutoHealGitSettings(db, withoutToken, fallbackAuthor)).toEqual({
      gitConfig: { ...withoutToken, token: undefined }, gitAuthor: fallbackAuthor,
    });
  });

  it("ignores a disabled row even when it contains malformed YAML", async () => {
    await repo.create({ config_key: "git", config_yaml: 'token: "unclosed' });
    await repo.update("git", { enabled: 0 });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: fallbackGitConfig, gitAuthor: fallbackAuthor,
    });
    expect(warn).not.toHaveBeenCalled();
  });

  it.each(["", "null", "false", "42", "plain scalar", "- remoteUrl: ignored", "{}"])(
    "uses fallbacks for empty/non-mapping YAML: %j", async (config_yaml) => {
      await repo.create({ config_key: "git", config_yaml });
      expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
        gitConfig: fallbackGitConfig, gitAuthor: fallbackAuthor,
      });
    },
  );

  it("ignores non-string fields independently", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: 123, username: true, email: [], token: {}, authorName: null, authorEmail: false,
    }) });
    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: fallbackGitConfig, gitAuthor: fallbackAuthor,
    });
  });

  it("warns generically on malformed YAML without logging source or parser details", async () => {
    await repo.create({ config_key: "git", config_yaml: 'token: "do-not-log-this-fixture-secret' });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).toEqual({
      gitConfig: fallbackGitConfig, gitAuthor: fallbackAuthor,
    });
    expect(warn.mock.calls).toEqual([
      ["[clawweb] Failed to parse cm_app_config git config; using fallback Git settings"],
    ]);
  });

  it("propagates database query failures instead of treating them as missing configuration", async () => {
    const failure = new Error("database unavailable");
    const query = vi.spyOn(db, "query").mockRejectedValue(failure);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await expect(resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor)).rejects.toBe(failure);
    expect(query).toHaveBeenCalledWith(expect.stringContaining("cm_app_config"), ["git"]);
    expect(warn).not.toHaveBeenCalled();
  });

  it("refreshes updates and disabling on the next call without mutating earlier settings", async () => {
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/first.git"' });
    const first = await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor);
    await repo.update("git", { config_yaml: 'remoteUrl: "https://git.example.com/second.git"' });
    const second = await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor);
    await repo.update("git", { enabled: 0 });
    const disabled = await resolveAutoHealGitSettings(db, fallbackGitConfig, fallbackAuthor);
    expect(first.gitConfig.remoteUrl).toBe("https://git.example.com/first.git");
    expect(second.gitConfig.remoteUrl).toBe("https://git.example.com/second.git");
    expect(disabled.gitConfig).toEqual(fallbackGitConfig);
  });

  it.each(["", " \t\n "])("rejects a missing/blank merged remote URL: %j", async (remoteUrl) => {
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "   "' });
    const result = resolveAutoHealGitSettings(db, { ...fallbackGitConfig, remoteUrl }, fallbackAuthor);
    await expect(result).rejects.toBeInstanceOf(Error);
    await expect(result).rejects.toMatchObject({
      status: 503,
      error: "Service Unavailable",
      message: expect.stringMatching(/cm_app_config.*git\.remoteUrl.*AUTO_HEAL_CLAWFLOWS_REMOTE_URL.*autoHeal\.clawflowsRemoteUrl/),
    });
  });

  it("does not share mutable empty results between compatibility resolver calls", async () => {
    const first = await resolveAutoHealGitConfigFromDb(db);
    first.remoteUrl = "https://git.example.com/not-persisted.git";
    expect((await resolveAutoHealGitConfigFromDb(db)).remoteUrl).toBe("");
  });
});
