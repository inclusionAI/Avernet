/**
 * SPM 埋点 facade（add-spm-tracking）。
 *
 * 消费方式（业务代码唯一入口，不得直取 capabilities.trackerAdapter）：
 *   import { Tracert } from '@/utils/tracert';
 *   import { Points } from '@/tracking/points';
 *   Tracert.click(Points.xxx.yyy, { bot_id: bot.bot_id });  // 点击
 *   Tracert.expo(Points.xxx.zzz);                            // 区块/手动曝光
 *
 * 约定：
 *   - pointKey 必须来自 `src/tracking/points.ts` 的语义点位常量（值=平台注册的中文点位名），
 *     任何 SPM 码值（a/b/c/d 位）不得出现在开源侧源码；
 *   - Open Core 默认 adapter 为 no-op（不上报）；internal overlay 注入真实实现，
 *     由 overlay 内码表把语义常量解析为 c/d 码后经 window.Tracert 上报；
 *   - 页面级 PV 不走本 facade——内部形态由 Bigfish tracert 插件按路由 spmBPos 自动采集；
 *   - 全同步吞错：埋点链路的任何异常不得影响业务主流程。
 */
import { getCapabilities } from '@/capabilities';

export const Tracert = {
  /** 手动点击上报。params 为该点位在 antlog 平台注册过的扩展参数（如 bot_id/session_id）。 */
  click: (pointKey: string, params?: Record<string, unknown>): void => {
    try {
      getCapabilities().trackerAdapter.click(pointKey, params);
    } catch (e) {
      // 埋点失败静默降级：不阻塞业务、不重复抛错到 UI。
      console.warn('[Tracert] click 上报失败:', pointKey, e);
    }
  },

  /** 手动/区块曝光上报。页面 PV 由 B 位机制自动采集，不用本接口。 */
  expo: (pointKey: string, params?: Record<string, unknown>): void => {
    try {
      getCapabilities().trackerAdapter.expo(pointKey, params);
    } catch (e) {
      console.warn('[Tracert] expo 上报失败:', pointKey, e);
    }
  },

  /**
   * 运行期重配（如临时切换 B 位）。常规流程由 tracert 插件按路由 spmBPos 自动维护，
   * 业务不主动调用；保留以对齐老前端 facade 形态。
   */
  config: (options: Record<string, unknown>): void => {
    try {
      getCapabilities().trackerAdapter.config(options);
    } catch (e) {
      console.warn('[Tracert] config 失败:', e);
    }
  },
};