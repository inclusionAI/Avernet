import { localBotService, type DesktopDevice } from '@/services/botWorkshop/localBotService';
import { useCallback, useEffect, useState } from 'react';

export function useDesktopCreateDevice() {
  const [devices, setDevices] = useState<DesktopDevice[]>([]);
  const [machineId, setMachineId] = useState('');
  const [basePath, setBasePath] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setBasePath('');
    void localBotService
      .devices()
      .then((items) => {
        if (!active) return;
        setDevices(items);
        setMachineId((current) =>
          items.some((d) => d.id === current)
            ? current
            : items.length === 1
            ? items[0].id
            : items.find((d) => ['ACTIVE', 'ONLINE'].includes(d.status))?.id || '',
        );
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : '设备查询失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    let active = true;
    setBasePath('');
    if (machineId) setError('');
    if (machineId)
      void localBotService
        .directory(machineId)
        .then((path) => {
          if (active) setBasePath(path);
        })
        .catch((e) => {
          if (active) setError(e instanceof Error ? e.message : '工作目录查询失败');
        });
    return () => {
      active = false;
    };
  }, [machineId, revision]);
  return {
    devices,
    machineId,
    setMachineId,
    basePath,
    loading,
    error,
    refresh: useCallback(() => setRevision((n) => n + 1), []),
  };
}
