import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import MonitoringPanel from './MonitoringPanel';
import { MonitoringSettings } from './MonitoringSettings';
import { SessionRecoveryList } from './session-recovery/SessionRecoveryList';

export default function MonitoringWorkspace({ isAdmin, tab }: { isAdmin: boolean; tab: 'diagnosis' | 'recovery' }) {
  const recovery = tab === 'recovery';
  const [params, setParams] = useSearchParams();
  const settings = params.get('openMonitoringSettings') === '1';
  const setSettings = (open: boolean) => {
    const next = new URLSearchParams(params);
    if (open) next.set('openMonitoringSettings', '1');
    else next.delete('openMonitoringSettings');
    setParams(next, { replace: true });
  };
  const [revision, setRevision] = useState(0);
  return <div className="insight-monitoring">
    <div className="page-heading"><div><h1>{recovery ? '自愈任务' : 'Agent 诊断'}</h1><p>{recovery ? '查看 Bot 自愈任务的处理进展和恢复结果' : '实时监测 Bot 任务执行状态，诊断会话异常'}</p></div>
      <button type="button" className="settings-entry" onClick={() => setSettings(true)}>诊断监控设置</button></div>
    {recovery ? <SessionRecoveryList /> : <MonitoringPanel isAdmin={isAdmin} embedded revision={revision} onOpenSettings={() => setSettings(true)} />}
    {settings && <MonitoringSettings onClose={() => setSettings(false)} onChanged={() => setRevision(value => value + 1)} />}
  </div>;
}
