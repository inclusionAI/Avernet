/**
 * Compatibility facade. ClawWeb environment and public-origin state are owned
 * by @avernet/clawweb-shared so every module observes the same Host value.
 */
export {
  configureClawWebPublicBaseUrl,
  getClawWebPublicBaseUrl,
  getCurrentEnv,
  getCurrentEnvWithGray,
  isDev,
  normalizeClawWebPublicBaseUrl,
} from "@avernet/clawweb-shared/server/env";
