import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useDesktopCreateDevice } from '@/hooks/useDesktopCreateDevice';
import type { BotCreateInput } from '@/services/botWorkshop';
import { RefreshCw } from 'lucide-react';
import type { Dispatch, SetStateAction } from 'react';
import { useEffect } from 'react';

export function DesktopDeviceFields({
  values,
  setValues,
}: {
  values: BotCreateInput;
  setValues: Dispatch<SetStateAction<BotCreateInput>>;
}) {
  const device = useDesktopCreateDevice();
  useEffect(() => {
    const mountPath =
      device.basePath && values.name.trim() ? `${device.basePath.replace(/[\\/]$/, '')}/${values.name.trim()}` : '';
    setValues((current) => ({ ...current, local: { machineId: device.machineId, mountPath } }));
  }, [device.machineId, device.basePath, values.name, setValues]);
  return (
    /* figma 稿 node 2027:26654:「运行设备」标签行右侧挂行内操作组 =
       刷新设备(icon 小按钮)+ 未启动引导链接;下方 36 高桌面设备 Select。 */
    <div className="space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span id="desktop-device-label" className="text-xs font-medium text-foreground">
          运行设备
        </span>
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={device.refresh}
            disabled={device.loading}
            className="h-auto cursor-pointer gap-1 px-0 text-xs text-muted-foreground"
            leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
          >
            刷新设备
          </Button>
          {!device.loading && !device.devices.length ? (
            <span className="text-xs text-muted-foreground">
              未启动？
              {/* 协议唤起改用原生锚点:JS 导航(window.location.href)到自定义 scheme 在弹窗
                  上下文会被部分浏览器策略静默拦截;原生链接交给浏览器协议层处理更可靠。 */}
              <a
                href="teamclaw://open"
                className="inline-flex h-auto cursor-pointer items-center px-0 text-xs text-primary underline-offset-4 hover:underline"
              >
                打开桌面客户端并登录
              </a>
            </span>
          ) : null}
        </div>
      </div>
      <Select
        /* 稿 node 2027:26659:无设备时下拉显示空态占位「选择桌面设备」;
           machineId 为空串时须转 undefined,否则 Radix 将空串视为已选值,placeholder 不渲染。 */
        value={device.machineId || undefined}
        onValueChange={(id) => {
          if (id) device.setMachineId(id);
        }}
        disabled={device.loading}
      >
        <SelectTrigger aria-labelledby="desktop-device-label" className="h-9">
          <SelectValue placeholder={device.loading ? '正在查询设备…' : '选择桌面设备'} />
        </SelectTrigger>
        <SelectContent>
          {device.devices.length ? (
            device.devices.map((d) => (
              <SelectItem key={d.id} value={d.id}>
                {d.name} · {d.status}
              </SelectItem>
            ))
          ) : (
            <SelectItem value="no-device" disabled>
              暂无可用设备
            </SelectItem>
          )}
        </SelectContent>
      </Select>
      {device.error ? (
        <p role="alert" className="text-destructive">
          {device.error}
        </p>
      ) : null}
    </div>
  );
}
