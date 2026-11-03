/**
 * 区块/手动曝光埋点组件（add-spm-tracking）。
 *
 * 渲染即曝光一次（StrictMode 双渲染下 useEffect 双触发由 once ref 吸收）：
 *   <TrackExpo point={Points.collabSquare.heroExpo} />
 * 页面 PV 不用本组件——由 Bigfish tracert 插件按路由 spmBPos 自动采集（内部形态）；
 * Open Core 形态下 trackerAdapter 为 no-op，本组件渲染开销可忽略。
 * 空态引导等条件区块：直接放在对应 JSX 分支内即可。
 */
import { useEffect, useRef } from 'react';

import { Tracert } from '@/utils/tracert';

export function TrackExpo({ point, params }: { point: string; params?: Record<string, unknown> }) {
  const firedRef = useRef(false);
  useEffect(() => {
    if (firedRef.current) return;
    firedRef.current = true;
    Tracert.expo(point, params);
    // params 变化不重复曝光：曝光语义绑定首次进入视口区块生命周期
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [point]);
  return null;
}