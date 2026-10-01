/**
 * Database layer — unified exports.
 *
 * Import from "./db/index.js" to access the full database API.
 */
export { IDatabase, Row, ExecResult, DatabaseConfig, MySqlConfig } from "./types.js";
export { SqliteDatabase, type SqliteDatabaseOptions } from "./sqlite-database.js";
export { NoOpDatabase, createDatabase, type CreateDatabaseOptions } from "./factory.js";
export { SchemaMigrator } from "./migrator.js";
export { FlowEventRepository } from "./repositories/event-repository.js";
export { FlowMetricsRepository, type FlowMetricsRow, type MetricsAggregateResult, type AggregateOptions } from "./repositories/metrics-repository.js";
export { TriggeredAlertRepository, type TriggeredAlertRow, type FindUnacknowledgedOptions } from "./repositories/alert-repository.js";
export { NodeExecutionRepository, type NodeExecutionRow, type NodeExecutionInsert, type NodeExecutionCompletion, type FindNodeExecutionsOptions, truncateJson, truncateError } from "./repositories/node-execution-repository.js";
export { FlowRunRepository, type FlowRunRow, type FlowRunInsert, type FlowRunCompletion, type FindFlowRunsOptions } from "./repositories/flow-run-repository.js";
export { ValidationTemplateRepository, type ValidationTemplateRow } from "./repositories/validation-template-repository.js";
export { FacadeBindingRepository, type FacadeBindingRow, type FacadeBindingInsert } from "./repositories/facade-binding-repository.js";
export { loadDatabaseConfig, resolveConfigPath } from "./config.js";
export { safeJsonStringify } from "./safe-json.js";