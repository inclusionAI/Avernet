import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs";
import type { GitModule, GitTagAnnotation, TagListEntry, DeployTag } from "./types.js";
import { GitOperationQueue } from "./queue.js";

const queue = new GitOperationQueue();

/** Execute a git command in the given working directory. */
function git(packsDir: string, args: string[]): string {
  try {
    const result = execFileSync("git", args, {
      cwd: packsDir,
      encoding: "utf-8",
      maxBuffer: 10 * 1024 * 1024,
      timeout: 30_000,
    });
    return result.trim();
  } catch (err: any) {
    // execFileSync error: .message = "Command failed: git ..." (no stderr!)
    // Actual git output is in .stderr (or .stdout for some commands)
    const stderr = typeof err?.stderr === "string" ? err.stderr.trim() : "";
    const stdout = typeof err?.stdout === "string" ? err.stdout.trim() : "";
    const detail = stderr || stdout || "";
    throw new Error(`git ${args.join(" ")} in ${packsDir}: ${detail || err?.message || err}`);
  }
}

/** Serialize annotation as key=value lines for `git tag -a -m`. */
function serializeAnnotation(a: GitTagAnnotation): string {
  const lines = [`version=${a.version}`, `action=${a.action}`, `workflowIds=${a.workflowIds.join(",")}`];
  if (a.fromTag) lines.push(`fromTag=${a.fromTag}`);
  if (a.note) lines.push(`note=${a.note}`);
  return lines.join("\n");
}

/** Parse annotation from `git tag -n99` or `git cat-file -p`. */
function parseAnnotation(raw: string): GitTagAnnotation | null {
  const map: Record<string, string> = {};
  for (const line of raw.split("\n")) {
    const idx = line.indexOf("=");
    if (idx > 0) {
      map[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
  }
  if (!map.version || !map.action) return null;
  return {
    version: parseInt(map.version, 10),
    action: map.action as GitTagAnnotation["action"],
    workflowIds: map.workflowIds ? map.workflowIds.split(",").filter(Boolean) : [],
    ...(map.fromTag ? { fromTag: map.fromTag } : {}),
    ...(map.note ? { note: map.note } : {}),
  };
}

/** Parse a tag name like "deploy/tech-research/#3" into components. */
export function parseDeployTag(tagName: string): DeployTag | null {
  const match = tagName.match(/^deploy\/(.+)\/#(\d+)$/);
  if (!match) return null;
  return { workflowId: match[1], sequenceNumber: parseInt(match[2], 10), tagName };
}

export const gitModule: GitModule = {
  async init(packsDir) {
    return queue.enqueue(async () => {
      // Ensure directory exists before running git commands
      if (!fs.existsSync(packsDir)) {
        fs.mkdirSync(packsDir, { recursive: true });
      }
      try {
        git(packsDir, ["rev-parse", "--git-dir"]);
      } catch {
        git(packsDir, ["init"]);
        // Create .gitignore — but do NOT commit yet!
        // user.name/user.email hasn't been set at this point (set later by
        // ensureGitRepoForPack). Committing now fails with "Author identity unknown"
        // on servers without global gitconfig. The .gitignore will be committed
        // by the first addCommit() call after identity is configured.
        const gitignorePath = path.join(packsDir, ".gitignore");
        // Canonical ignore rules for per-pack repos.
        // `._*` — macOS AppleDouble / resource fork files created by Finder,
        //          SMB/AFS shares, or cross-volume copies. These must never
        //          enter version control.
        const REQUIRED_GITIGNORE_RULES = [
          "*.pyc",
          "__pycache__/",
          ".DS_Store",
          "._*",          // macOS AppleDouble resource fork backups
          ".env.derived",
        ];
        if (!fs.existsSync(gitignorePath)) {
          fs.writeFileSync(gitignorePath, REQUIRED_GITIGNORE_RULES.join("\n") + "\n");
        } else {
          // Merge missing rules into existing .gitignore (for repos created
          // before `._*` was added — containers may restart with mounted packs).
          const existing = fs.readFileSync(gitignorePath, "utf-8");
          const existingLines = new Set(existing.split(/\r?\n/).map(l => l.trim()).filter(Boolean));
          const missing = REQUIRED_GITIGNORE_RULES.filter(r => !existingLines.has(r));
          if (missing.length > 0) {
            fs.writeFileSync(gitignorePath, existing.trimEnd() + "\n" + missing.join("\n") + "\n");
          }
        }
        git(packsDir, ["add", ".gitignore"]);
      }
    });
  },

  async addRemote(packsDir, remoteUrl) {
    return queue.enqueue(async () => {
      try {
        const existing = git(packsDir, ["remote", "get-url", "origin"]);
        if (existing !== remoteUrl) {
          git(packsDir, ["remote", "set-url", "origin", remoteUrl]);
          console.log(`[git] Remote origin updated: ${existing} → ${remoteUrl}`);
        }
      } catch {
        try {
          git(packsDir, ["remote", "add", "origin", remoteUrl]);
          console.log(`[git] Remote origin added: ${remoteUrl}`);
        } catch (addErr) {
          console.warn(`[git] Failed to add remote origin: ${addErr instanceof Error ? addErr.message : addErr}`);
          throw addErr; // Re-throw so the caller knows remote setup failed
        }
      }
    });
  },

  async configureCredential(packsDir, username, token) {
    return queue.enqueue(async () => {
      try {
        // Use git credential-cache (memory only, no disk file) — LLM cannot cat the token
        // Timeout 24h; engine re-injects on every startup via init
        let remoteUrl = "";
        try { remoteUrl = git(packsDir, ["remote", "get-url", "origin"]); } catch { /* no remote */ }
        const hostMatch = remoteUrl.match(/https?:\/\/([^/]+)/);
        const host = hostMatch ? hostMatch[1] : "";
        if (host && username && token) {
          git(packsDir, ["config", "credential.helper", "cache --timeout 86400"]);
          // Feed credential via stdin to git-credential approve
          execFileSync("git", ["credential", "approve"], {
            cwd: packsDir,
            input: `protocol=https\nhost=${host}\nusername=${username}\npassword=${token}\n`,
            encoding: "utf-8",
            timeout: 5000,
          });
        }
      } catch (err) {
        // credential-cache may not be available on all platforms (e.g., some macOS configs).
        // Don't let this block the init flow.
        console.warn(`[git] configureCredential failed (non-fatal): ${err instanceof Error ? err.message : err}`);
      }
    });
  },

  async fetchRemote(packsDir, branch) {
    return queue.enqueue(async () => {
      // Returns true if the fetch succeeded AND the requested branch exists
      // on the remote (or, when no branch is specified, the fetch succeeded).
      // Callers use this to distinguish "remote genuinely has no branch"
      // (safe to treat as first-time) from "fetch failed / network issue"
      // (must NOT fabricate a divergent root commit and push).
      try {
        if (branch) {
          git(packsDir, ["fetch", "origin", branch]);
          console.log(`[git] Fetched origin/${branch}`);
        } else {
          git(packsDir, ["fetch", "origin"]);
          console.log(`[git] Fetched origin`);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("not found") || msg.includes("couldn't find") || msg.includes("does not exist")) {
          console.log(`[git] fetch: remote branch not found (first-time setup is OK)`);
          return false;
        }
        console.warn(`[git] fetch failed (non-fatal): ${msg}`);
        return false;
      }
      // Fetch succeeded. Verify the branch ref actually exists on the remote.
      if (branch) {
        try {
          git(packsDir, ["rev-parse", "--verify", `origin/${branch}`]);
        } catch {
          return false;
        }
      }
      return true;
    });
  },

  async pushBranch(packsDir, branch, includeTags, allowForce = true) {
    return queue.enqueue(async () => {
      // includeTags: true = push --tags (all tags, legacy), string = push specific tag, false/undefined = no tags
      const tagArgs: string[] = [];
      if (typeof includeTags === "string") {
        // Push branch + specific tag (avoids "already exists" on old tags)
        tagArgs.push("origin", branch, includeTags);
      } else if (includeTags) {
        // Legacy: push branch + --tags (push ALL local tags)
        tagArgs.push("origin", branch, "--tags");
      } else {
        tagArgs.push("origin", branch);
      }
      try {
        git(packsDir, ["push", ...tagArgs]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const divergent = msg.includes("non-fast-forward") || msg.includes("fetch first") || msg.includes("updates were rejected");
        if (divergent && allowForce) {
          // Retry with --force-with-lease (safe force: only overwrites if no one else pushed since we fetched).
          // Only safe to do when the caller has confirmed the local history deliberately diverges
          // (e.g. deploy/rollback). Migration/pull paths must pass allowForce=false — a non-fast-forward
          // there means we failed to adopt the remote history and would clobber real deploy commits.
          console.warn(`[git] push rejected (divergent history), retrying with --force-with-lease`);
          try {
            git(packsDir, ["push", ...tagArgs, "--force-with-lease"]);
          } catch (forceErr) {
            throw forceErr; // Re-throw the force-push error
          }
        } else if (divergent) {
          // allowForce=false: refuse to overwrite remote history. Surface the rejection so the
          // caller can log it and skip — never fabricate a force-push that would drop deploy commits.
          throw new Error(`[git] push to ${branch} rejected (non-fast-forward); force-with-lease disabled for this path — remote history not overwritten`);
        } else {
          throw err;
        }
      }
    });
  },

  async pullRebase(packsDir, branch) {
    return queue.enqueue(async () => {
      // Rebase failure must NOT be swallowed: callers (smartPullFromRemote)
      // depend on knowing whether the local branch actually adopted the
      // remote history. A silent failure leaves the local HEAD divergent,
      // and a subsequent pushBranch would force-with-lease over the remote.
      git(packsDir, ["pull", "--rebase", "origin", branch]);
    });
  },

  async ensureBranch(packsDir, branchName, options?: { localAuthoritative?: boolean }) {
    return queue.enqueue(async () => {
      try {
        git(packsDir, ["rev-parse", "--verify", branchName]);
        git(packsDir, ["checkout", branchName]);
      } catch {
        // Local branch doesn't exist. Prefer basing it on the remote if
        // origin/<branch> was fetched, so the local branch inherits the
        // remote deploy history instead of starting from an unborn HEAD
        // (which would create a divergent root commit and risk a force-push
        // clobbering the remote). Only fall back to a fresh branch when the
        // remote truly has nothing.
        if (options?.localAuthoritative) {
          // 本地权威:不 checkout origin/<branch>(那会把工作区回退到远端旧内容)。
          // 从当前 HEAD 建 <branch>(unborn 则建空分支),保住刚装的工作区内容。
          git(packsDir, ["checkout", "-b", branchName]);
          return;
        }
        try {
          git(packsDir, ["rev-parse", "--verify", `origin/${branchName}`]);
          git(packsDir, ["checkout", "-b", branchName, `origin/${branchName}`]);
        } catch {
          git(packsDir, ["checkout", "-b", branchName]);
        }
      }
    });
  },

  async addCommit(packsDir, paths, message) {
    return queue.enqueue(async () => {
      for (const p of paths) {
        git(packsDir, ["add", p]);
      }
      try {
        git(packsDir, ["commit", "-m", message]);
      } catch (e: unknown) {
        // `git commit` fails when there's nothing to commit — that's OK.
        const msg = e instanceof Error ? e.message : String(e);
        const isNoChange = msg.includes("nothing to commit") || msg.includes("no changes added") || msg.includes("nothing added");
        if (!isNoChange) throw e;
      }
    });
  },

  async createTag(packsDir, tagName, annotation) {
    return queue.enqueue(async () => {
      const msg = serializeAnnotation(annotation);
      git(packsDir, ["tag", "-a", tagName, "-m", msg]);
    });
  },

  async listTags(packsDir, prefix) {
    return queue.enqueue(async () => {
      let raw: string;
      try {
        raw = git(packsDir, ["tag", "-l", `${prefix}*`, `-n99`]);
      } catch {
        return [];
      }
      const entries: TagListEntry[] = [];
      for (const line of raw.split("\n")) {
        const parts = line.trim().split(/\s+/, 2);
        const tagName = parts[0];
        if (!tagName) continue;
        const annotationText = parts.slice(1).join(" ");
        const annotation = parseAnnotation(annotationText);
        if (annotation) {
          entries.push({ tagName, annotation });
        }
      }
      return entries;
    });
  },

  async readTagAnnotation(packsDir, tagName) {
    return queue.enqueue(async () => {
      try {
        const raw = git(packsDir, ["cat-file", "-p", `refs/tags/${tagName}`]);
        // cat-file -p shows the tag object; annotation is in the message part
        // after the blank line separator
        const blankLineIdx = raw.indexOf("\n\n");
        const message = blankLineIdx >= 0 ? raw.slice(blankLineIdx + 2) : raw;
        return parseAnnotation(message);
      } catch {
        return null;
      }
    });
  },

  async checkoutPaths(packsDir, ref, paths) {
    return queue.enqueue(async () => {
      git(packsDir, ["checkout", ref, "--", ...paths]);
    });
  },

  async stash(packsDir) {
    return queue.enqueue(async () => {
      try {
        git(packsDir, ["stash"]);
      } catch {
        // No changes to stash — OK
      }
    });
  },

  async stashPop(packsDir) {
    return queue.enqueue(async () => {
      try {
        git(packsDir, ["stash", "pop"]);
      } catch {
        // Stash pop can fail if dirty working tree conflicts — non-fatal
      }
    });
  },

  async getCurrentBranch(packsDir) {
    return queue.enqueue(async () => git(packsDir, ["rev-parse", "--abbrev-ref", "HEAD"]));
  },

  async nextDeployNumber(packsDir, workflowId) {
    return queue.enqueue(async () => {
      const prefix = `deploy/${workflowId}/#`;
      let raw: string;
      try {
        raw = git(packsDir, ["tag", "-l", `${prefix}*`]);
      } catch {
        return 1;
      }
      const tags = raw.split("\n").filter(Boolean);
      if (tags.length === 0) return 1;
      let max = 0;
      for (const t of tags) {
        const parsed = parseDeployTag(t.trim());
        if (parsed && parsed.sequenceNumber > max) {
          max = parsed.sequenceNumber;
        }
      }
      return max + 1;
    });
  },
};