/**
 * 环境预设配置
 *
 * 所有环境的预设配置集中在此文件
 * 使用方式：
 * - npm run dev          (预发环境，默认)
 * - npm run dev:local    (本地环境)
 * - npm run dev:dev      (内网开发环境)
 * - npm run dev:pre      (预发环境)
 * - npm run dev:prod     (生产环境)
 */

import type { ServerConfig, ServerConfigMap } from './servers.config';

/**
 * 服务器地址（构建期/Node）:
 * - 内部构建（UMI_ENV=internal）走 config/servers.internal.ts 真实地址
 * - 开源构建走核心 config/servers.config.ts 占位地址
 * config 文件在 Node 执行，开源构建 require 永不命中 servers.internal（文件不存在也安全）。
 */
const SERVERS: ServerConfigMap =
  process.env.UMI_ENV === 'internal'
    ? // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('./servers.internal').SERVERS
    : // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('./servers.config').SERVERS;

/**
 * 日志级别类型
 */
type LogLevel = 'silent' | 'error' | 'warn' | 'debug';

/**
 * 预设配置类型
 */
export interface PresetConfig {
  /** 环境名称 */
  name: string;
  /** 服务器地址配置 */
  servers: ServerConfig;
  /** 代理日志级别 */
  logLevel: LogLevel;
}

/**
 * 所有环境预设配置
 */
export const PRESETS = {
  /**
   * 本地开发环境
   * 用途：连接本地后端服务
   * 适用场景：
   * - 本地全栈开发
   * - 后端服务在本地运行
   * - 需要调试后端代码
   */
  local: {
    name: '本地环境',
    servers: SERVERS.LOCAL,
    logLevel: 'debug' as const,
  },

  /**
   * 内网开发环境
   * 用途：连接内网开发环境
   * 适用场景：
   * - 联调内网后端服务
   * - 测试开发中的功能
   * - 需要访问内网资源
   */
  dev: {
    name: '开发环境',
    servers: SERVERS.DEV,
    logLevel: 'warn' as const,
  },

  /**
   * 预发环境（默认）
   * 用途：连接预发服务器
   * 适用场景：
   * - 日常开发（最常用）
   * - 功能联调
   * - 测试验证
   */
  pre: {
    name: '预发环境',
    servers: SERVERS.PRE,
    logLevel: 'warn' as const,
  },

  /**
   * 生产环境
   * 用途：连接生产服务器
   * 适用场景：
   * - 调试线上问题
   * - 验证线上数据
   * - 紧急修复
   *
   * ⚠️ 警告：
   * - 连接生产环境时要格外小心
   * - 不要在生产数据库中进行写操作测试
   * - 建议只读模式调试
   */
  prod: {
    name: '生产环境',
    servers: SERVERS.PROD,
    logLevel: 'silent' as const,
  },
} as const;

export type PresetName = keyof typeof PRESETS;
