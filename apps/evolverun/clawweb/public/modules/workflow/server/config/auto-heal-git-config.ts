import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../repositories/app-config-repository.js";
import type { ClawFlowsGitConfig, GitAuthor } from "../services/clawflows-git-client.js";
import { parse as parseYaml } from "yaml";

export type AutoHealDbGitConfig = {
  remoteUrl: string;
  username: string;
  email: string;
  token: string;
  authorName: string;
  authorEmail: string;
};

const EMPTY_GIT_CONFIG: AutoHealDbGitConfig = {
  remoteUrl: "",
  username: "",
  email: "",
  token: "",
  authorName: "",
  authorEmail: "",
};

/**
 * Resolve the auto-heal git configuration from the DB-stored `cm_app_config`
 * table under the `git` config key.
 *
 * This allows operators to configure the clawflows remote via the ClawWeb
 * admin UI instead of environment variables or application.yaml.
 */
export async function resolveAutoHealGitConfigFromDb(db: IDatabase): Promise<AutoHealDbGitConfig> {
  const repo = new AppConfigRepository(db);
  const row = await repo.findByKey("git");
  if (!row || !row.config_yaml || row.enabled !== 1) {
    return { ...EMPTY_GIT_CONFIG };
  }

  try {
    const parsed = parseYaml(row.config_yaml) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ...EMPTY_GIT_CONFIG };
    }

    const record = parsed as Record<string, unknown>;
    const stringField = (key: string): string => {
      const value = record[key];
      if (typeof value !== "string") return "";
      return key === "token" ? value : value.trim();
    };

    return {
      remoteUrl: stringField("remoteUrl"),
      username: stringField("username"),
      email: stringField("email"),
      token: stringField("token"),
      authorName: stringField("authorName"),
      authorEmail: stringField("authorEmail"),
    };
  } catch {
    // Parser errors can include the YAML source, including credentials.
    console.warn("[clawweb] Failed to parse cm_app_config git config; using fallback Git settings");
    return { ...EMPTY_GIT_CONFIG };
  }
}

/** Resolve a fresh DB override for each save/deploy before constructing its service. */
export async function resolveAutoHealGitSettings(
  db: IDatabase,
  fallbackGitConfig: ClawFlowsGitConfig,
  fallbackAuthor: GitAuthor,
): Promise<{ gitConfig: ClawFlowsGitConfig; gitAuthor: GitAuthor }> {
  const dbConfig = await resolveAutoHealGitConfigFromDb(db);
  const gitConfig: ClawFlowsGitConfig = {
    remoteUrl: dbConfig.remoteUrl || fallbackGitConfig.remoteUrl.trim(),
    username: dbConfig.username || fallbackGitConfig.username.trim(),
    email: dbConfig.email || fallbackGitConfig.email.trim(),
    token: dbConfig.token || fallbackGitConfig.token,
  };
  const gitAuthor: GitAuthor = {
    name: dbConfig.authorName || fallbackAuthor.name.trim(),
    email: dbConfig.authorEmail || fallbackAuthor.email.trim(),
  };

  if (!gitConfig.remoteUrl) {
    throw Object.assign(new Error(
      "AutoHeal Git remote URL is not configured. Set remoteUrl in the enabled cm_app_config git row " +
      "(git.remoteUrl), AUTO_HEAL_CLAWFLOWS_REMOTE_URL, or autoHeal.clawflowsRemoteUrl in application.yaml.",
    ), { status: 503, error: "Service Unavailable" });
  }

  return { gitConfig, gitAuthor };
}
