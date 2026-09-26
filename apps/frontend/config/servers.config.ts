/**
 * 服务器地址配置（核心文件，开源占位默认）
 *
 * 本文件只持「占位默认」服务器地址（全 localhost，零内网域名）。
 * 真实内网地址在 config/servers.internal.ts（单一事实源，.internal-paths，开源仓不含）:
 * - 构建期(Node): config/presets.config.ts 按 UMI_ENV==='internal' 条件 require
 * - 运行期(浏览器): src/internal/servers.ts 经 extend(AppExt,{servers}) 注入
 *
 * 类型 ServerConfig / ServerConfigMap / EnvName 由本文件导出，两仓共享。
 */

/**
 * 服务器配置类型
 */
export interface ServerConfig {
  /** 管理类服务器（设备管理、访问控制等） */
  MANAGEMENT: string;
  /** 会话相关服务器（chat、sessions 等） */
  SESSION: string;
  /** aixharness Web Server：/langfuse（devFlow 工作流看板）/dima /hello /auth */
  AIXHARNESS: string;
  /** asfagent 服务器 */
  ASFAGENT: string;
  /** BCN 群聊服务器 */
  BCN?: string;
  /** aixcore 服务器（卡片/能力中心） */
  AIXCORE?: string;
  /** MCP Center 服务器（办公网权限申请） */
  MCPCENTER: string;
  /** secbaas 服务器 */
  SECBAAS?: string;
  /** 架构工作台 ADC 服务器 */
  ADC?: string;
  /** BCS Fuse 智能融合服务器 */
  BCNFUSE?: string;
  /** HarnessFlow 工作流引擎服务器 */
  HARNESSFLOW?: string;
}

/** 全环境服务器配置 map（LOCAL/DEV/PRE/PROD）。 */
export type ServerConfigMap = Record<
  'LOCAL' | 'DEV' | 'PRE' | 'PROD',
  ServerConfig
>;

/**
 * 占位默认服务器地址（零内网域名）。
 * 开源形态四套环境一致走本地占位；真实分环境地址见 config/servers.internal.ts。
 *
 * 关于 BCN/BCNFUSE 端口（BCS server / BCSFUSE）的「漂移」与单一事实源：
 * 这里的 21000 / 8765 只是「纯静态占位默认」。真正生效的 dev proxy 目标在
 * config/config.local.ts 里按环境变量动态算出：
 *
 *   const BCS_TARGET = process.env.BCS_PORT
 *     ? `http://127.0.0.1:${process.env.BCS_PORT}` // ← 设了 BCS_PORT 时走这里
 *     : preset.servers.BCN || 'http://127.0.0.1:21000'; // ← 没设时才回退本占位
 *
 * - 用户改了端口（设了 BCS_PORT，由本地后端启动脚本经 .env.local 注入）→ 直接用新端口，不读本占位。
 * - 用户没改端口（没设 BCS_PORT）→ 回退本占位 21000，此时后端也是默认 21000，正好对上。
 *
 * ⚠️ 本占位的默认端口与本地后端启动脚本里的默认端口常量是两处独立常量，
 * 需保持一致；改其一要同步另一处，否则「默认场景」会再次漂移。
 */
// 注意：用 127.0.0.1 而非 localhost。macOS /etc/hosts 常只有 `::1 localhost`，
// Node(dns verbatim) 会把 localhost 优先解析到 IPv6 ::1；而本地后端（BCS/Backend）
// 多数只监听 IPv4 127.0.0.1，导致 dev proxy 间歇性 504（happy-eyeballs 回退时好时坏）。
const PLACEHOLDER: ServerConfig = {
  AIXHARNESS: 'http://127.0.0.1:8888',
  MANAGEMENT: 'http://127.0.0.1:8888',
  SESSION: 'http://127.0.0.1:8888',
  BCN: 'http://127.0.0.1:21000',
  ASFAGENT: 'http://127.0.0.1:8888',
  AIXCORE: 'http://127.0.0.1:8888',
  MCPCENTER: 'http://127.0.0.1:8888',
  SECBAAS: 'http://127.0.0.1:8888',
  ADC: 'http://127.0.0.1:8888',
  BCNFUSE: 'http://127.0.0.1:8765',
  HARNESSFLOW: 'http://127.0.0.1:3001',
};

export const SERVERS: ServerConfigMap = {
  LOCAL: { ...PLACEHOLDER },
  DEV: { ...PLACEHOLDER },
  PRE: { ...PLACEHOLDER },
  PROD: { ...PLACEHOLDER },
};

export type EnvName = keyof ServerConfigMap;
