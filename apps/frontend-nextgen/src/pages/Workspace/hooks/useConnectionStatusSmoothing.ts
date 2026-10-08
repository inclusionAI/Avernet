import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { useEffect, useRef, useState } from 'react';

/**
 * 连接态展示补正：避免切换会话 / 首连时顶栏先闪一下「离线」。
 *
 * 背景：useBotChat / useGroupChat 切换会话时 provider 重建并 connect()，原始状态会经历
 * disconnected → connecting → connected。若直接展示原始态，顶栏 Badge 会先短暂显示
 * 「离线(灰)」再到「连接中(橙)」再到「在线(绿)」，色差下跳变明显。
 *
 * 策略（连接中优先）：
 * - connecting 或 连接尝试中的 disconnected → 一律先展示「连接中」，不展示「离线」；
 * - connected →「在线」、reconnecting →「重连中」、error →「连接失败」，均立即展示；
 * - 仅当 disconnected 持续超过宽限期（默认 5000ms，覆盖 SDK 默认 reconnectDelay=3000ms 的重连等待窗口 + 余量）才降级展示「离线」。
 *
 * 即贴合用户预期：切换时先从「连接中」开始——成功则「在线」，长时间连不上 / 失败则「离线」。
 * 真实的 protocol error 仍走红色「连接失败」并保留错误详情，便于感知并点「重新连接」。
 * 「离线」仅用于「静默断开 / 一直连不上」的终态。
 */
const DEFAULT_GRACE_MS = 5000;

export function useConnectionStatusSmoothing(
  raw: ProviderConnectionStatus,
  graceMs = DEFAULT_GRACE_MS,
): ProviderConnectionStatus {
  // 冷启动首连：原始即 disconnected 时，初始展示「连接中」，避免首帧闪烁「离线」。
  const [shown, setShown] = useState<ProviderConnectionStatus>(raw === 'disconnected' ? 'connecting' : raw);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // 非终态 connected/reconnecting/error 与进行中 connecting：立即展示，并取消挂起的「离线」降级计时。
    if (raw === 'connected' || raw === 'reconnecting' || raw === 'error' || raw === 'connecting') {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      setShown(raw);
      return;
    }
    // raw === 'disconnected'：先展示「连接中」（切换 / 首连时不再先显离线），宽限期内若进入
    // connecting/connected 等状态由上述分支收敛；仍持续 disconnected（连不上 / 失败）超过宽限期才降级为「离线」。
    setShown('connecting');
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setShown('disconnected');
    }, graceMs);
  }, [raw, graceMs]);

  // 卸载清理降级计时，避免泄漏。
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  return shown;
}

export { DEFAULT_GRACE_MS };
