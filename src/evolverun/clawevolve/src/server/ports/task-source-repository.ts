export type EvolveTaskSourceRow = {
  id: number;
  task_id: string;
  source_type: string;
  source_id: string;
  source_schema_version: string;
  adapter_version: string | null;
  source_ref_json: string;
  source_digest: string | null;
  status: string;
  error_code: string | null;
  error_message: string | null;
  resolved_at: number | string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type CreateFrozenTaskSourceInput = {
  taskId: string;
  sourceType: string;
  sourceId: string;
  sourceSchemaVersion: string;
  adapterVersion: string | null;
  sourceRef: Record<string, unknown>;
};

export type TaskSourceRepositoryPort = {
  createFrozen(input: CreateFrozenTaskSourceInput): Promise<EvolveTaskSourceRow>;
  findByTaskId(taskId: string): Promise<EvolveTaskSourceRow | null>;
  markResolving(taskId: string): Promise<void>;
  markReady(taskId: string, input: {
    digest: string;
    sourceSchemaVersion: string;
    adapterVersion: string | null;
  }): Promise<void>;
  markFailed(taskId: string, code: string, message: string): Promise<void>;
};
