/**
 * EvolvetraceExtensions — EvolveTrace 企业扩展点接口。
 *
 * 设计原则与 TaskguardExtensions 一致：
 * - Avernet 定义接口 + 社区默认实现
 * - OCB 通过注入企业实现覆盖默认行为
 * - 所有字段均为可选，未提供时回退社区默认
 *
 * 本模块仅定义接口层，不包含社区默认实现或企业注入逻辑；
 * 具体实现在后续任务单独落地。
 */
import type { Express } from "express";
import type { IDatabase } from "../db.js";

// ── Repository 层 ──
//
// 这些 Like 接口与 server/repositories/*.ts 中的具体实现一一对应。
// 使用结构化宽松签名，允许企业实现以 duck-typing 方式注入，
// 而不强制继承内部具体类。具体方法签名由各自的 *-repository.ts 定义。

export interface FlowRunRepositoryLike {
  // 由 server/repositories/flow-run-repository.ts 定义
  [method: string]: (...args: any[]) => any;
}

export interface NodeExecutionRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface FlowEventRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface WorkflowSpecRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface FacadeBindingRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface BotWorkflowPermissionRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface MetricsRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface AlertRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface FlowControlRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface ExecutionStepLogRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface NotificationConfigRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface DeployHistoryRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

export interface HttpCallbackConfigRepositoryLike {
  [method: string]: (...args: any[]) => any;
}

// ── 扩展点接口 ──

export interface EvolvetraceExtensions {
  /**
   * 数据库适配器。
   * 社区默认: SQLite / MySQL (via initDatabase in server/db.ts)
   * 企业实现: ZDAS MySQL
   */
  createDatabase?: (config: unknown) => Promise<IDatabase>;

  /**
   * 认证提供者。
   * 社区默认: HMAC 签名验证 (middleware/signature.ts)
   * 企业实现: x-one-id IAM
   */
  createAuthProvider?: (config: unknown) => unknown;

  /**
   * 自定义路由注册。
   * 社区默认: 无
   * 企业实现: 注册企业专属 API 端点
   */
  registerRoutes?: (app: Express) => void;

  /**
   * 自定义中间件注册。
   * 社区默认: CORS (localhost only), compression, admin-auth
   * 企业实现: 企业认证中间件、审计日志等
   */
  registerMiddleware?: (app: Express) => void;

  /**
   * 存储后端。
   * 社区默认: 本地 SQLite 文件存储
   * 企业实现: 对象存储 (OSS/S3)
   */
  createStorage?: (config: unknown) => unknown;
}

/**
 * EvolveTrace 服务器配置，用于扩展点初始化。
 */
export interface EvolvetraceServerConfig {
  port: number;
  database: {
    mode: string;
    sqlite?: { path: string };
    mysql?: {
      host: string;
      port: number;
      user: string;
      password: string;
      database: string;
    };
  };
  security: {
    admin_user_ids: string;
  };
  internal: {
    public_key_b64: string;
  };
  static: {
    enabled: boolean;
    path: string;
  };
}