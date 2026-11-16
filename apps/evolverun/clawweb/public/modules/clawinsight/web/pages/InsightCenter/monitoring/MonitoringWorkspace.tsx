import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import MonitoringPanel from './MonitoringPanel';
import { MonitoringSettings } from './MonitoringSettings';
import { SessionRecoveryList } from './session-recovery/SessionRecoveryList';

export default function MonitoringWorkspace({ isAdmin }: { isAdmin: boolean }) {
  const [params, setParams] = useSearchParams();
  const recovery = params.get('monitoringTab') === 'recovery';
  const [settings, setSettings] = useState(false);
  const [revision, setRevision] = useState(0);
  return <div className="insight-monitoring">
    <div className="page-heading"><div><h1>Agent 诊断</h1><p>实时监测 Bot 任务执行状态，诊断会话异常，查看自愈处理进展</p></div>
      <button type="button" className="settings-entry" onClick={() => setSettings(true)}>诊断监控设置</button></div>
    <div role="tablist" aria-label="Agent 诊断" className="monitoring-subtabs">{[['diagnosis', '诊断'], ['recovery', '自愈任务']].map(([value, label]) =>
      <button type="button" role="tab" aria-selected={recovery === (value === 'recovery')} key={value} onClick={() => {
        const next = new URLSearchParams(params); next.set('monitoringTab', value); setParams(next);
      }}>{label}</button>)}</div>
    <div role="tabpanel">{recovery ? <SessionRecoveryList /> : <MonitoringPanel isAdmin={isAdmin} embedded revision={revision} onOpenSettings={() => setSettings(true)} />}</div>
    {settings && <MonitoringSettings onClose={() => setSettings(false)} onChanged={() => setRevision(value => value + 1)} />}
  </div>;
}
