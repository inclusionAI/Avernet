/** Tag annotation stored via `git tag -a`. Parsed from key=value format. */
export type GitTagAnnotation = {
  version: number;
  action: "deploy" | "rollback" | "pull" | "share" | "migration";
  workflowIds: string[];
  fromTag?: string;
  note?: string;
};

/** Tag format: deploy/<workflowId>/#<N> */
export type DeployTag = {
  workflowId: string;
  sequenceNumber: number;
  tagName: string; // e.g. "deploy/tech-research/#3"
};

/** Result of a tag list query. */
export type TagListEntry = {
  tagName: string;
  annotation: GitTagAnnotation;
};

/** Public API of the git module. */
export type GitModule = {
  init(packsDir: string): Promise<void>;
  addRemote(packsDir: string, remoteUrl: string): Promise<void>;
  configureCredential(packsDir: string, username: string, token: string): Promise<void>;
  fetchRemote(packsDir: string, branch?: string): Promise<boolean>;
  pushBranch(packsDir: string, branch: string, includeTags?: boolean | string, allowForce?: boolean): Promise<void>;
  pullRebase(packsDir: string, branch: string): Promise<void>;
  ensureBranch(packsDir: string, branchName: string, options?: { localAuthoritative?: boolean }): Promise<void>;
  addCommit(packsDir: string, paths: string[], message: string): Promise<void>;
  createTag(packsDir: string, tagName: string, annotation: GitTagAnnotation): Promise<void>;
  listTags(packsDir: string, prefix: string): Promise<TagListEntry[]>;
  readTagAnnotation(packsDir: string, tagName: string): Promise<GitTagAnnotation | null>;
  checkoutPaths(packsDir: string, ref: string, paths: string[]): Promise<void>;
  stash(packsDir: string): Promise<void>;
  stashPop(packsDir: string): Promise<void>;
  getCurrentBranch(packsDir: string): Promise<string>;
  nextDeployNumber(packsDir: string, workflowId: string): Promise<number>;
};