import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "@avernet/workflow/server/repositories/app-config-repository";
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
    return EMPTY_GIT_CONFIG;
  }

  try {
    const parsed = parseYaml(row.config_yaml) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return EMPTY_GIT_CONFIG;
    }

    const record = parsed as Record<string, unknown>;
    const stringField = (key: string): string => {
      const value = record[key];
      return typeof value === "string" ? value : "";
    };

    return {
      remoteUrl: stringField("remoteUrl"),
      username: stringField("username"),
      email: stringField("email"),
      token: stringField("token"),
      authorName: stringField("authorName"),
      authorEmail: stringField("authorEmail"),
    };
  } catch (err) {
    console.warn(
      `[clawweb] Failed to parse cm_app_config git config: ${err instanceof Error ? err.message : String(err)}`,
    );
    return EMPTY_GIT_CONFIG;
  }
}
