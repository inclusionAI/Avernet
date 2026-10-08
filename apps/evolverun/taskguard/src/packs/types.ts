export type WorkflowSourceKind = "workspace-pack" | "configured-pack";

export type WorkflowPackManifestWorkflow = {
  id: string;
  file: string;
};

export type WorkflowPackManifestAction = {
  id: string;
  type: string;
  root: string;
  commands?: Record<string, string>;
};

export type WorkflowPackFacadeHelp = {
  title?: string;
  summary?: string;
  examples?: string[];
};

export type WorkflowPackFacade = {
  command: string;
  defaultWorkflow: string;
  aliases?: string[];
  help?: WorkflowPackFacadeHelp;
};

export type WorkflowPackManifest = {
  id: string;
  version: string;
  title?: string;
  description?: string;
  workflows: WorkflowPackManifestWorkflow[];
  actions?: WorkflowPackManifestAction[];
  facades?: WorkflowPackFacade[];
  skills?: {
    required?: string[];
  };
  compat?: {
    minRuntimeVersion?: string;
    schemaVersion?: number;
  };
};

export type ResolvedWorkflowPackWorkflow = WorkflowPackManifestWorkflow & {
  absolutePath: string;
  digest: string;
};

export type ResolvedWorkflowPackActionCommand = {
  actionName: string;
  script: string;
  absolutePath: string;
};

export type ResolvedWorkflowPackAction = Omit<WorkflowPackManifestAction, "commands"> & {
  absoluteRoot: string;
  commands?: Record<string, ResolvedWorkflowPackActionCommand>;
};

export type ResolvedWorkflowPack = {
  manifest: WorkflowPackManifest;
  root: string;
  digest: string;
  source: {
    kind: WorkflowSourceKind;
    root: string;
  };
  workflows: ResolvedWorkflowPackWorkflow[];
  actions?: ResolvedWorkflowPackAction[];
};

export type WorkflowPackSource = ResolvedWorkflowPack["source"];

export type ResolvedWorkflow = {
  id: string;
  spec: import("../types.js").WorkflowSpec;
  digest: string;
  absolutePath: string;
  source: WorkflowPackSource | { kind: "db" };
  pack: {
    id: string;
    version: string;
    root: string;
    digest: string;
  };
};

/** A workflow that was discovered in a pack but failed to load/validate. */
export type FailedWorkflow = {
  id: string;
  packId: string;
  packVersion: string;
  absolutePath: string;
  error: string;
};

export type WorkflowPackCatalog = {
  packs: ResolvedWorkflowPack[];
  workflows: ResolvedWorkflow[];
  failedWorkflows: FailedWorkflow[];
};
