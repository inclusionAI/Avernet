import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useDesktopCreateDevice } from '@/hooks/useDesktopCreateDevice';
import type { BotCreateInput } from '@/services/botWorkshop';
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
    <div className="space-y-2 text-xs">
      <span id="desktop-device-label">运行设备</span>
      <Select
        value={device.machineId}
        onValueChange={(id) => {
          if (id) device.setMachineId(id);
        }}
        disabled={device.loading}
      >
        <SelectTrigger aria-labelledby="desktop-device-label">
          <SelectValue placeholder={device.loading ? '正在查询设备…' : '选择桌面设备'} />
        </SelectTrigger>
        <SelectContent>
          {device.devices.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              {d.name} · {d.status}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {device.error ? (
        <p role="alert" className="text-destructive">
          {device.error}
        </p>
      ) : null}
      {!device.loading && !device.error && !device.devices.length ? (
        <p className="text-muted-foreground">请先启动桌面客户端并登录。</p>
      ) : null}
      <div className="flex gap-2">
        <Button type="button" variant="outline" onClick={device.refresh} disabled={device.loading}>
          刷新设备
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            window.location.href = 'teamclaw://open';
          }}
        >
          打开桌面客户端
        </Button>
      </div>
    </div>
  );
}
